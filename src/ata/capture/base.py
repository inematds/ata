"""Contrato dos backends de captura (docs/INTERFACES.md §A).

Um backend grava DUAS faixas no bundle: ``far.wav`` (o que a máquina toca: as outras pessoas) e ``mic.wav``
(o microfone: você), ambas 16 kHz mono PCM16. ``start()`` devolve um :class:`Handle`; ``Handle.stop()``
fecha os arquivos e devolve um :class:`ata.bundle.Track` por faixa (``start_epoch``, ``start_measured``,
``samples``, ``silent``). Motivos de dano da captura (vocabulário fechado de ``bundle.DAMAGE_REASONS``) ficam
em ``Handle.damage_reasons``; avisos legíveis (pt-BR, sem texto de reunião) em ``Handle.warnings``.

Escolha do backend: ``backend_for_platform()`` — ``ATA_CAPTURE=fake`` força o backend sintético (testes).
"""

from __future__ import annotations

import math
import os
import sys
import wave
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import numpy as np

from .. import audio
from ..bundle import DAMAGE_REASONS, Track

if TYPE_CHECKING:
    from ..config import Config

SILENT_DB = -60.0
FAR_FILE = "far.wav"
MIC_FILE = "mic.wav"


class CaptureError(RuntimeError):
    """Falha de captura (dispositivo sumiu, gravador morreu ao iniciar...). CLI -> código 1."""


class CaptureMissing(CaptureError):
    """Dependência de captura ausente (pw-record, PyAudioWPatch, helper ata-audio). CLI -> código 4."""


class UnsupportedPlatform(CaptureMissing):
    """O backend pedido não roda nesta plataforma. CLI -> código 4."""


@runtime_checkable
class Handle(Protocol):
    """Captura em andamento. ``stop()`` é idempotente na prática: chame uma vez."""

    damage_reasons: list[str]
    warnings: list[str]

    def preview(self) -> dict[str, Track]:
        """Faixas como conhecidas logo após o início (samples=None); vai para o meta.json inicial."""

    def poll(self) -> str | None:
        """None se a captura segue viva; senão uma mensagem curta (pt-BR) do motivo da queda."""

    def stop(self) -> dict[str, Track]:
        """Para as duas faixas, conserta cabeçalhos e devolve ``{"far": Track, "mic": Track}``."""


@runtime_checkable
class CaptureBackend(Protocol):
    name: str            # ex.: "linux-pipewire"
    recorder_name: str   # vai para meta.recorder.name, ex.: "ata-linux-pw"

    def devices(self) -> dict[str, Any]:
        """Estrutura de :func:`device_listing` (nunca levanta)."""

    def start(self, bundle_dir: Path, far: str | None = None, mic: str | None = None) -> Handle:
        """Começa a gravar ``far.wav``/``mic.wav`` em ``bundle_dir``. ``far``/``mic`` escolhem dispositivo
        (None = padrão do sistema / config). Levanta CaptureError/CaptureMissing."""


def device_listing(backend: str, *, available: bool, far: dict[str, Any] | None = None,
                   mic: dict[str, Any] | None = None, problems: list[str] | None = None,
                   extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Formato comum de ``devices()`` / ``ata.capture.devices.list_devices``::

        {"backend": "linux-pipewire", "platform": "linux", "available": True,
         "far": {"id": "xrdp-sink", "description": "...", "source": "default"|"config"|"env"},
         "mic": {...}, "problems": ["pt-BR, curto"], ...extra}
    """
    out: dict[str, Any] = {"backend": backend, "platform": sys.platform, "available": bool(available),
                           "far": far, "mic": mic, "problems": list(problems or [])}
    if extra:
        out.update(extra)
    return out


def configured_target(config: "Config | None", track: str) -> tuple[str | None, str]:
    """Dispositivo escolhido para ``track`` ("far"/"mic"): env ``ATA_FAR_TARGET``/``ATA_MIC_TARGET`` vence
    ``[audio] far_device/mic_device`` da config. Devolve ``(nome|None, origem)`` com origem env|config|default."""
    env = os.environ.get(f"ATA_{track.upper()}_TARGET")
    if env:
        return env, "env"
    if config is not None:
        value = config.get(f"audio.{track}_device")
        if value:
            return str(value), "config"
    return None, "default"


def clean_reasons(reasons: list[str]) -> list[str]:
    """Filtra para o vocabulário fechado e tira duplicatas preservando a ordem."""
    out: list[str] = []
    for r in reasons:
        if r in DAMAGE_REASONS and r not in out:
            out.append(r)
    return out


def scan_wav(path: Path, threshold_db: float = SILENT_DB, chunk: int = 16000 * 10) -> tuple[int, bool]:
    """(amostras, silenciosa?) lendo em blocos — não carrega 1 h de áudio na memória.

    Silenciosa = RMS global abaixo de ``threshold_db``. Arquivo ausente/ilegível -> (0, True).
    """
    try:
        with wave.open(str(path), "rb") as w:
            total = 0
            sumsq = 0.0
            while True:
                raw = w.readframes(chunk)
                if not raw:
                    break
                x = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float64) / 32768.0
                total += len(x)
                sumsq += float(np.dot(x, x))
    except (OSError, EOFError, wave.Error):
        return 0, True
    if total == 0:
        return 0, True
    rms = math.sqrt(sumsq / total)
    db = -math.inf if rms <= 0 else 20.0 * math.log10(rms)
    return total, db < threshold_db


def read_window(path: Path, start_s: float, seconds: float) -> np.ndarray:
    """Trecho ``[start_s, start_s+seconds)`` de um WAV 16 kHz mono como int16 (vazio se fora do arquivo)."""
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            n = w.getnframes()
            a = min(n, max(0, int(start_s * rate)))
            w.setpos(a)
            raw = w.readframes(int(seconds * rate))
    except (OSError, EOFError, wave.Error):
        return np.zeros(0, np.int16)
    return np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.int16)


def find_first_sound(path: Path, limit_s: float = 600.0, window_s: float = 10.0) -> float | None:
    """Segundo do primeiro som (bloco de 10 ms acima de -55 dB) nos primeiros ``limit_s`` segundos."""
    t = 0.0
    while t < limit_s:
        seg = read_window(path, t, window_s)
        if len(seg) == 0:
            return None
        fs = audio.first_sound(seg)
        if fs is not None:
            return t + fs
        t += window_s
    return None


def finalize_track(bundle_dir: Path, file: str, *, device: str, start_epoch: float | None,
                   start_measured: bool, force_silent: bool = False) -> Track | None:
    """Conserta o cabeçalho (gravador morto não fecha o WAV), mede amostras e silêncio. None se o arquivo
    não existe (vira ``far_missing``/``mic_missing`` no relatório)."""
    p = Path(bundle_dir) / file
    if not p.is_file():
        return None
    audio.repair_header(p)
    samples, silent = scan_wav(p)
    return Track(file=file, device=device, start_epoch=start_epoch, start_measured=start_measured,
                 sample_rate=audio.SAMPLE_RATE, samples=samples, silent=silent or force_silent)


def backend_for_platform(platform: str | None = None, config: "Config | None" = None) -> CaptureBackend:
    """Backend de captura para a plataforma (``sys.platform`` por padrão).

    ``ATA_CAPTURE=fake`` -> FakeBackend; ``ATA_CAPTURE=linux|windows|macos`` força um backend real.
    linux -> PipeWire (2× pw-record); win32 -> WASAPI (PyAudioWPatch); darwin -> helper Swift ``ata-audio``.
    Outra plataforma -> UnsupportedPlatform.
    """
    forced = (os.environ.get("ATA_CAPTURE") or "").strip().lower()
    plat = platform or sys.platform
    if forced == "fake":
        from .fake import FakeBackend
        return FakeBackend.from_env()
    if forced in ("linux", "pipewire"):
        plat = "linux"
    elif forced in ("windows", "wasapi", "win32"):
        plat = "win32"
    elif forced in ("macos", "darwin"):
        plat = "darwin"
    elif forced:
        raise UnsupportedPlatform(f"ATA_CAPTURE desconhecido: {forced!r} (use fake, linux, windows ou macos)")
    if plat.startswith("linux"):
        from .linux import PipeWireBackend
        return PipeWireBackend(config)
    if plat == "win32":
        from .windows import WasapiBackend
        return WasapiBackend(config)
    if plat == "darwin":
        from .macos import MacHelperBackend
        return MacHelperBackend(config)
    raise UnsupportedPlatform(f"gravação não suportada nesta plataforma: {plat}")
