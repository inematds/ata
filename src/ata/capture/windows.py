"""Captura no Windows: WASAPI loopback (far) + microfone (mic) no MESMO processo, via PyAudioWPatch.

Contrato:
- um relógio só: o instante de cada callback é ``time.perf_counter()`` mapeado para epoch por
  :class:`ClockMap` (par ``time.time()``/``perf_counter()`` tirado uma vez). Por isso ``start_measured=True``.
- ``start_epoch`` de cada faixa = mediana de ``epoch(callback) - amostras_escritas/16000`` nos primeiros
  callbacks (:class:`StartEstimator`), o que tira o jitter do agendador.
- áudio do dispositivo (48 kHz estéreo, tipicamente) -> 16 kHz mono int16 por :class:`ChunkResampler`
  (estado entre blocos, sem deriva de arredondamento) e gravado aos poucos por :class:`WavWriter`.
- keep-alive: o loopback do WASAPI NÃO entrega callbacks quando nada toca. Uma thread completa com zeros pelo
  relógio de parede (:func:`pad_samples_needed`), então ``far.wav`` acompanha o tempo real. Isso é normal no
  far; buraco no MIC é anormal e vira ``dropped_frames``.
- PyAudioWPatch é importado só em ``start()``; fora do win32 -> ``UnsupportedPlatform``.

As partes puras (ClockMap, StartEstimator, ChunkResampler, WavWriter, WasapiTrack) não importam PyAudioWPatch e
são testadas no Linux com relógios falsos.
"""

from __future__ import annotations

import logging
import math
import statistics
import sys
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import numpy as np

from .. import audio
from ..bundle import Track
from .base import (FAR_FILE, MIC_FILE, CaptureError, CaptureMissing, UnsupportedPlatform, device_listing,
                   finalize_track)

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger("ata.capture.windows")

PAD_TOLERANCE_S = 0.25      # atraso de callback tolerado antes de completar com zeros
KEEPALIVE_INTERVAL_S = 0.2
ESTIMATOR_SAMPLES = 50
DROPPED_MIC_S = 0.5         # buraco total no mic acima disso -> dropped_frames
INSTALL_HINT = "instale o extra de Windows: uv tool install 'ata[windows]' (PyAudioWPatch)"


# ---- partes puras ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ClockMap:
    """Mapeia ``perf_counter`` (monotônico, alta resolução) para epoch Unix com um único par de referência."""

    epoch0: float
    perf0: float

    @classmethod
    def capture(cls, time_fn: Callable[[], float] = time.time,
                perf_fn: Callable[[], float] = time.perf_counter) -> "ClockMap":
        p1 = perf_fn()
        e = time_fn()
        p2 = perf_fn()
        return cls(e, (p1 + p2) / 2.0)

    def to_epoch(self, perf: float) -> float:
        return self.epoch0 + (perf - self.perf0)


def pad_samples_needed(written: int, anchor_epoch: float, now_epoch: float, rate: int = audio.SAMPLE_RATE,
                       tolerance_s: float = PAD_TOLERANCE_S) -> int:
    """Quantos zeros completar para a faixa alcançar o relógio de parede.

    Esperado = ``(now - anchor) * rate``. Só completa quando o atraso passa de ``tolerance_s`` (um callback
    atrasado normal não vira silêncio falso) e então completa até ``now - tolerance_s``, deixando folga para o
    callback que ainda pode chegar."""
    behind = (now_epoch - anchor_epoch) - written / float(rate)
    if behind <= tolerance_s:
        return 0
    return max(0, int(math.floor((now_epoch - anchor_epoch - tolerance_s) * rate)) - written)


class StartEstimator:
    """Mediana de ``epoch_do_callback - segundos_já_escritos`` nos primeiros ``limit`` callbacks."""

    def __init__(self, limit: int = ESTIMATOR_SAMPLES) -> None:
        self.limit = limit
        self.values: list[float] = []

    def add(self, callback_epoch: float, seconds_written: float) -> None:
        if len(self.values) < self.limit:
            self.values.append(callback_epoch - seconds_written)

    def estimate(self) -> float | None:
        return statistics.median(self.values) if self.values else None


class ChunkResampler:
    """Reamostragem linear com estado entre blocos (posição absoluta, sem deriva). float32 -> float32."""

    def __init__(self, src_rate: int, dst_rate: int = audio.SAMPLE_RATE) -> None:
        self.src, self.dst = int(src_rate), int(dst_rate)
        self.ratio = self.src / self.dst
        self.buf = np.zeros(0, np.float32)
        self.base = 0      # índice absoluto de entrada de buf[0]
        self.out_n = 0     # amostras de saída já produzidas

    def push(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, np.float32)
        if self.src == self.dst:
            return x
        self.buf = np.concatenate([self.buf, x])
        end = self.base + len(self.buf)
        if end == 0:
            return np.zeros(0, np.float32)
        kmax = int(math.floor((end - 1) / self.ratio))
        if kmax < self.out_n:
            return np.zeros(0, np.float32)
        ks = np.arange(self.out_n, kmax + 1)
        y = np.interp(ks * self.ratio - self.base, np.arange(len(self.buf)), self.buf).astype(np.float32)
        self.out_n = kmax + 1
        drop = max(0, min(len(self.buf) - 1, int(math.floor(self.out_n * self.ratio)) - self.base))
        self.buf = self.buf[drop:]
        self.base += drop
        return y


def to_mono_float(raw: bytes, channels: int, sample_format: str = "int16") -> np.ndarray:
    """Bytes intercalados do PortAudio -> mono float32 (média dos canais)."""
    if sample_format == "int16":
        a = np.frombuffer(raw[: len(raw) // 2 * 2], "<i2").astype(np.float32) / 32768.0
    elif sample_format == "float32":
        a = np.frombuffer(raw[: len(raw) // 4 * 4], "<f4").astype(np.float32)
    else:
        raise ValueError(f"formato não suportado: {sample_format}")
    if channels > 1:
        a = a[: len(a) // channels * channels].reshape(-1, channels).mean(axis=1)
    return a


class WavWriter:
    """WAV 16 kHz mono PCM16 escrito aos poucos. O módulo ``wave`` reescreve o cabeçalho a cada bloco, então
    o arquivo é válido mesmo se o processo morrer (e ``audio.repair_header`` cobre o resto)."""

    def __init__(self, path: Path, rate: int = audio.SAMPLE_RATE) -> None:
        self.path = Path(path)
        self.rate = rate
        self.samples = 0
        self._f = open(self.path, "wb")
        self._w = wave.open(self._f, "wb")
        self._w.setnchannels(1)
        self._w.setsampwidth(2)
        self._w.setframerate(rate)

    def write(self, samples: np.ndarray) -> None:
        data = audio.to_int16(np.asarray(samples))
        if len(data):
            self._w.writeframes(data.astype("<i2").tobytes())
            self._f.flush()   # leitores (modo ao vivo) veem o arquivo crescendo
            self.samples += len(data)

    def pad(self, n: int) -> None:
        if n > 0:
            self.write(np.zeros(n, np.int16))

    def close(self) -> None:
        if self._w is not None:
            self._w.close()
            self._f.close()
            self._w = None  # type: ignore[assignment]


class WasapiTrack:
    """Estado de uma faixa: conversão, escrita, estimativa de início e keep-alive. Thread-safe."""

    def __init__(self, name: str, path: Path, device: str, src_rate: int, channels: int, clock: ClockMap,
                 anchor_epoch: float, sample_format: str = "int16") -> None:
        self.name = name
        self.device = device
        self.channels = channels
        self.sample_format = sample_format
        self.clock = clock
        self.anchor_epoch = anchor_epoch    # instante da abertura do stream: âncora do keep-alive
        self.writer = WavWriter(path)
        self.resampler = ChunkResampler(src_rate)
        self.estimator = StartEstimator()
        self.padded = 0
        self.callbacks = 0
        self._lock = threading.Lock()

    def on_audio(self, raw: bytes, callback_perf: float) -> None:
        """Chamado no callback do PortAudio (``callback_perf`` = ``perf_counter()`` no callback)."""
        y = self.resampler.push(to_mono_float(raw, self.channels, self.sample_format))
        with self._lock:
            self.writer.write(y)
            self.callbacks += 1
            self.estimator.add(self.clock.to_epoch(callback_perf), self.writer.samples / audio.SAMPLE_RATE)

    def keepalive(self, now_epoch: float) -> int:
        """Completa com zeros se a faixa ficou para trás do relógio de parede. Devolve quantos zeros pôs."""
        with self._lock:
            n = pad_samples_needed(self.writer.samples, self.anchor_epoch, now_epoch)
            self.writer.pad(n)
            self.padded += n
            return n

    @property
    def start_epoch(self) -> float:
        est = self.estimator.estimate()
        return self.anchor_epoch if est is None else est

    def close(self) -> None:
        with self._lock:
            self.writer.close()


# ---- backend ---------------------------------------------------------------------------------------------

class WasapiHandle:
    def __init__(self, bundle_dir: Path, pa: Any, streams: list[Any], tracks: dict[str, WasapiTrack],
                 clock: ClockMap, time_fn: Callable[[], float] = time.time) -> None:
        self.bundle_dir = Path(bundle_dir)
        self.pa = pa
        self.streams = streams
        self.tracks = tracks
        self.clock = clock
        self.time_fn = time_fn
        self.damage_reasons: list[str] = []
        self.warnings: list[str] = []
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._keepalive_loop, name="ata-wasapi-keepalive", daemon=True)
        self._thread.start()
        self._result: dict[str, Track] | None = None

    def _keepalive_loop(self) -> None:
        while not self._halt.wait(KEEPALIVE_INTERVAL_S):
            now = self.time_fn()
            for t in self.tracks.values():
                t.keepalive(now)

    def preview(self) -> dict[str, Track]:
        return {k: Track(file=t.writer.path.name, device=t.device, start_epoch=t.anchor_epoch,
                         start_measured=True) for k, t in self.tracks.items()}

    def poll(self) -> str | None:
        for s in self.streams:
            try:
                if not s.is_active():
                    return "stream WASAPI parou"
            except Exception:  # noqa: BLE001 - stream fechado por fora
                return "stream WASAPI indisponível"
        return None

    def stop(self) -> dict[str, Track]:
        if self._result is not None:
            return self._result
        self._halt.set()
        self._thread.join(timeout=2)
        for s in self.streams:
            try:
                s.stop_stream()
                s.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.pa.terminate()
        except Exception:  # noqa: BLE001
            pass
        now = self.time_fn()
        out: dict[str, Track] = {}
        for k, t in self.tracks.items():
            t.keepalive(now)
            t.close()
            if k == "mic" and t.padded / audio.SAMPLE_RATE > DROPPED_MIC_S:
                self.damage_reasons.append("dropped_frames")
                self.warnings.append(f"o microfone ficou {t.padded / audio.SAMPLE_RATE:.1f} s sem entregar áudio")
            tr = finalize_track(self.bundle_dir, t.writer.path.name, device=t.device, start_epoch=t.start_epoch,
                                start_measured=True)
            if tr is not None:
                out[k] = tr
        self._result = out
        return out


class WasapiBackend:
    """Backend WASAPI. ``pyaudio_module`` injetável (teste); sem ele exige win32 + PyAudioWPatch."""

    name = "windows-wasapi"
    recorder_name = "ata-windows-wasapi"

    def __init__(self, config: "Config | None" = None, *, pyaudio_module: Any = None,
                 time_fn: Callable[[], float] = time.time, perf_fn: Callable[[], float] = time.perf_counter
                 ) -> None:
        self.config = config
        self._mod = pyaudio_module
        self.time_fn = time_fn
        self.perf_fn = perf_fn

    def _module(self) -> Any:
        if self._mod is not None:
            return self._mod
        if sys.platform != "win32":
            raise UnsupportedPlatform("captura WASAPI só existe no Windows")
        try:
            import pyaudiowpatch  # type: ignore[import-not-found]
        except ImportError:
            raise CaptureMissing(f"PyAudioWPatch ausente: {INSTALL_HINT}") from None
        self._mod = pyaudiowpatch
        return pyaudiowpatch

    def _pick(self, pa: Any, track: str, explicit: str | None) -> dict[str, Any]:
        from .base import configured_target
        wanted = explicit or configured_target(self.config, track)[0]
        if wanted:
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if wanted.lower() in str(info.get("name", "")).lower() and (
                        track == "mic" or info.get("isLoopbackDevice")):
                    return info
            raise CaptureError(f"dispositivo {track} não encontrado: {wanted}")
        if track == "far":
            return pa.get_default_wasapi_loopback()
        return pa.get_default_input_device_info()

    def devices(self) -> dict[str, Any]:
        try:
            mod = self._module()
            pa = mod.PyAudio()
            try:
                far = self._pick(pa, "far", None)
                mic = self._pick(pa, "mic", None)
            finally:
                pa.terminate()
        except CaptureError as exc:
            return device_listing(self.name, available=False, problems=[str(exc)])
        except Exception as exc:  # noqa: BLE001 - driver de áudio pode falhar de muitos jeitos
            return device_listing(self.name, available=False, problems=[f"WASAPI: {type(exc).__name__}"])
        return device_listing(self.name, available=True,
                              far={"id": far.get("name"), "description": far.get("name"), "source": "default"},
                              mic={"id": mic.get("name"), "description": mic.get("name"), "source": "default"})

    def start(self, bundle_dir: Path, far: str | None = None, mic: str | None = None) -> WasapiHandle:
        mod = self._module()
        pa = mod.PyAudio()
        clock = ClockMap.capture(self.time_fn, self.perf_fn)
        streams: list[Any] = []
        tracks: dict[str, WasapiTrack] = {}
        try:
            for track, explicit, file in (("far", far, FAR_FILE), ("mic", mic, MIC_FILE)):
                info = self._pick(pa, track, explicit)
                rate = int(info.get("defaultSampleRate", 48000))
                ch = max(1, int(info.get("maxInputChannels", 1)))
                t = WasapiTrack(track, Path(bundle_dir) / file, str(info.get("name", track)), rate, ch, clock,
                                anchor_epoch=self.time_fn())
                tracks[track] = t
                perf = self.perf_fn

                def callback(in_data, frame_count, time_info, status, _t=t, _perf=perf):  # noqa: ANN001
                    _t.on_audio(in_data, _perf())
                    return (None, mod.paContinue)

                stream = pa.open(format=mod.paInt16, channels=ch, rate=rate, input=True,
                                 input_device_index=int(info["index"]), frames_per_buffer=max(256, rate // 50),
                                 stream_callback=callback)
                streams.append(stream)
                log.info("WASAPI %s aberto (%d Hz, %d canais)", track, rate, ch)
        except Exception as exc:
            for s in streams:
                try:
                    s.close()
                except Exception:  # noqa: BLE001
                    pass
            for t in tracks.values():
                t.close()
            pa.terminate()
            if isinstance(exc, CaptureError):
                raise
            raise CaptureError(f"WASAPI não abriu: {type(exc).__name__}: {exc}") from None
        for s in streams:
            try:
                s.start_stream()
            except Exception:  # noqa: BLE001 - alguns hosts já iniciam no open
                pass
        return WasapiHandle(Path(bundle_dir), pa, streams, tracks, clock, self.time_fn)
