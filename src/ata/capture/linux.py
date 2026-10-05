"""Captura no Linux via PipeWire: dois ``pw-record`` (far = monitor do sink padrão, mic = source padrão).

Contrato:
- argv de cada faixa: ``pw-record --target <node.name> --rate 16000 --channels 1 --format s16
  [-P stream.capture.sink=true] <arquivo>`` (o ``-P`` só no far: grava o que o sink toca).
- dispositivo: argumento ``far``/``mic`` > env ``ATA_FAR_TARGET``/``ATA_MIC_TARGET`` > config
  ``[audio] far_device/mic_device`` > ``wpctl inspect @DEFAULT_AUDIO_SINK@`` / ``@DEFAULT_AUDIO_SOURCE@``.
- início: ``time.time()`` no spawn de cada processo (estimado). No ``stop()`` o vazamento da caixa de som no
  mic é medido por ``audio.estimate_lag`` a partir do primeiro som do far; força >= 0.2 -> o mic é realinhado
  (``mic.start_epoch = far.start_epoch - lag``) e as duas faixas ficam ``start_measured=True``. Sem
  vazamento (fone de ouvido) -> ``start_measured=False`` -> ``start_unmeasured`` no relatório.
- parada: SIGINT, espera ``stop_timeout_s``; quem não sair leva SIGKILL e o bundle ganha ``recorder_killed``.
  Os cabeçalhos WAV são sempre consertados com ``audio.repair_header``.

O executor de processos é injetável (``pw_record=(sys.executable, "fake.py")``, ``runner=``) para testes.
"""

from __future__ import annotations

import logging
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Callable, Sequence

from .. import audio
from ..bundle import Track
from .base import (FAR_FILE, MIC_FILE, CaptureError, CaptureMissing, configured_target, device_listing,
                   find_first_sound, finalize_track, read_window)

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger("ata.capture.linux")

DEFAULT_SELECTORS = {"far": "@DEFAULT_AUDIO_SINK@", "mic": "@DEFAULT_AUDIO_SOURCE@"}
LAG_MIN_STRENGTH = 0.2
LAG_WINDOW_S = 60.0

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def parse_inspect(output: str) -> dict[str, str]:
    """Extrai ``node.name`` / ``node.description`` / ``node.nick`` da saída de ``wpctl inspect``.

    Linhas no formato ``  * node.name = "alsa_output.pci..."`` (o ``*`` é opcional)."""
    out: dict[str, str] = {}
    for line in output.splitlines():
        s = line.strip().lstrip("*").strip()
        key, sep, value = s.partition("=")
        if not sep:
            continue
        key = key.strip()
        if key in ("node.name", "node.description", "node.nick", "media.class"):
            out.setdefault(key, value.strip().strip('"'))
    return out


def build_argv(pw_record: Sequence[str], target: str, out: Path, *, monitor: bool) -> list[str]:
    """argv de um ``pw-record`` 16 kHz mono s16 (``monitor`` = capturar o que o sink toca)."""
    argv = [*pw_record, "--target", target, "--rate", str(audio.SAMPLE_RATE), "--channels", "1",
            "--format", "s16"]
    if monitor:
        argv += ["-P", "stream.capture.sink=true"]
    argv.append(str(out))
    return argv


def refine_start(bundle_dir: Path, far: Track, mic: Track) -> tuple[Track, Track, float | None, float]:
    """Realinha ``mic.start_epoch`` pelo vazamento do far no mic. Devolve (far, mic, lag|None, força).

    Correlação feita a partir do primeiro som do far (reuniões costumam começar caladas), no MESMO índice
    das duas faixas, o que preserva o atraso relativo. Força < 0.2 -> não confia: ambas start_measured=False.
    """
    from dataclasses import replace
    if far.silent or mic.silent or far.start_epoch is None:
        return replace(far, start_measured=False), replace(mic, start_measured=False), None, 0.0
    fs = find_first_sound(Path(bundle_dir) / far.file)
    if fs is None:
        return replace(far, start_measured=False), replace(mic, start_measured=False), None, 0.0
    t0 = max(0.0, fs - 1.0)
    a = read_window(Path(bundle_dir) / far.file, t0, LAG_WINDOW_S)
    b = read_window(Path(bundle_dir) / mic.file, t0, LAG_WINDOW_S)
    lag, strength = audio.estimate_lag(a, b)
    if strength < LAG_MIN_STRENGTH:
        return replace(far, start_measured=False), replace(mic, start_measured=False), None, strength
    return (replace(far, start_measured=True),
            replace(mic, start_epoch=far.start_epoch - lag, start_measured=True), lag, strength)


class PipeWireHandle:
    """Dois ``pw-record`` vivos. Ver contrato no topo do módulo."""

    def __init__(self, bundle_dir: Path, procs: dict[str, subprocess.Popen], epochs: dict[str, float],
                 devices: dict[str, str], errs: dict[str, IO[bytes]], stop_timeout_s: float) -> None:
        self.bundle_dir = Path(bundle_dir)
        self.procs = procs
        self.epochs = epochs
        self.devices = devices
        self._errs = errs
        self.stop_timeout_s = stop_timeout_s
        self.damage_reasons: list[str] = []
        self.warnings: list[str] = []
        self.lag: float | None = None
        self.lag_strength: float = 0.0
        self._stopped: dict[str, Track] | None = None

    def preview(self) -> dict[str, Track]:
        files = {"far": FAR_FILE, "mic": MIC_FILE}
        return {k: Track(file=files[k], device=self.devices[k], start_epoch=self.epochs[k],
                         start_measured=False) for k in ("far", "mic")}

    def poll(self) -> str | None:
        for track, p in self.procs.items():
            code = p.poll()
            if code is not None:
                return f"pw-record da faixa {track} parou (código {code})"
        return None

    def stop(self) -> dict[str, Track]:
        if self._stopped is not None:
            return self._stopped
        for track, p in self.procs.items():
            code = p.poll()
            if code is not None and code != 0:
                self.warnings.append(f"pw-record da faixa {track} morreu durante a gravação (código {code})")
                self.damage_reasons.append("recorder_killed")
            elif code is None:
                try:
                    p.send_signal(signal.SIGINT)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + self.stop_timeout_s
        for track, p in self.procs.items():
            try:
                p.wait(timeout=max(0.05, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                log.warning("pw-record (%s) não saiu com SIGINT; SIGKILL", track)
                p.kill()
                p.wait()
                self.damage_reasons.append("recorder_killed")
        for f in self._errs.values():
            f.close()
        files = {"far": FAR_FILE, "mic": MIC_FILE}
        tracks: dict[str, Track] = {}
        for k in ("far", "mic"):
            t = finalize_track(self.bundle_dir, files[k], device=self.devices[k], start_epoch=self.epochs[k],
                               start_measured=False)
            if t is not None:
                tracks[k] = t
        if "far" in tracks and "mic" in tracks:
            far, mic, lag, strength = refine_start(self.bundle_dir, tracks["far"], tracks["mic"])
            tracks["far"], tracks["mic"] = far, mic
            self.lag, self.lag_strength = lag, strength
            if lag is None:
                log.info("alinhamento far/mic não medido (força %.2f): start_unmeasured", strength)
            else:
                log.info("alinhamento far/mic medido: atraso %.3f s, força %.2f", lag, strength)
        self._stopped = tracks
        return tracks


class PipeWireBackend:
    """Backend PipeWire. ``pw_record``/``wpctl``/``runner``/``clock`` são injetáveis para teste."""

    name = "linux-pipewire"
    recorder_name = "ata-linux-pw"

    def __init__(self, config: "Config | None" = None, *, pw_record: Sequence[str] = ("pw-record",),
                 wpctl: Sequence[str] = ("wpctl",), runner: Runner = subprocess.run,
                 clock: Callable[[], float] = time.time, startup_check_s: float = 0.3,
                 stop_timeout_s: float = 5.0) -> None:
        self.config = config
        self.pw_record = tuple(pw_record)
        self.wpctl = tuple(wpctl)
        self.runner = runner
        self.clock = clock
        self.startup_check_s = startup_check_s
        self.stop_timeout_s = stop_timeout_s

    # ---- dispositivos ----------------------------------------------------------------------------------

    def inspect(self, selector: str) -> dict[str, str]:
        """``wpctl inspect <selector>`` já interpretado. CaptureMissing sem wpctl; CaptureError se falhar."""
        try:
            res = self.runner([*self.wpctl, "inspect", selector], capture_output=True, text=True, timeout=5)
        except FileNotFoundError:
            raise CaptureMissing("wpctl não encontrado: instale o WirePlumber/PipeWire "
                                 "(pacotes pipewire-bin e wireplumber)") from None
        except subprocess.TimeoutExpired:
            raise CaptureError(f"wpctl inspect {selector} não respondeu") from None
        if res.returncode != 0:
            raise CaptureError(f"wpctl inspect {selector} falhou (código {res.returncode}); "
                               "o PipeWire está rodando?")
        info = parse_inspect(res.stdout or "")
        if "node.name" not in info:
            raise CaptureError(f"não achei node.name para {selector}")
        return info

    def resolve(self, track: str, explicit: str | None = None) -> tuple[str, str, str]:
        """(node.name, descrição, origem) do dispositivo da faixa: argumento > env > config > padrão."""
        if explicit:
            return explicit, explicit, "arg"
        name, origin = configured_target(self.config, track)
        if name:
            return name, name, origin
        info = self.inspect(DEFAULT_SELECTORS[track])
        return info["node.name"], info.get("node.description", info["node.name"]), "default"

    def devices(self) -> dict[str, Any]:
        problems: list[str] = []
        found: dict[str, dict[str, Any] | None] = {"far": None, "mic": None}
        for track in ("far", "mic"):
            try:
                name, desc, origin = self.resolve(track)
                found[track] = {"id": name, "description": desc, "source": origin}
            except CaptureError as exc:
                problems.append(str(exc))
        available = self._has_pw_record()
        if not available:
            problems.append("pw-record não encontrado: instale o pacote pipewire-bin")
        return device_listing(self.name, available=available and not problems, far=found["far"],
                              mic=found["mic"], problems=problems)

    def _has_pw_record(self) -> bool:
        import shutil
        exe = self.pw_record[0]
        return bool(shutil.which(exe) or Path(exe).is_file())

    # ---- gravação --------------------------------------------------------------------------------------

    def _spawn(self, argv: list[str], err: IO[bytes]) -> subprocess.Popen:
        try:
            # mesmo grupo de processos do supervisor (que roda em sessão própria): se o supervisor morrer,
            # ata.recorder encerra os pw-record órfãos com killpg(pid_do_supervisor).
            return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err)
        except FileNotFoundError:
            raise CaptureMissing("pw-record não encontrado: instale o pacote pipewire-bin") from None

    def start(self, bundle_dir: Path, far: str | None = None, mic: str | None = None) -> PipeWireHandle:
        bundle_dir = Path(bundle_dir)
        targets = {"far": self.resolve("far", far)[0], "mic": self.resolve("mic", mic)[0]}
        files = {"far": FAR_FILE, "mic": MIC_FILE}
        procs: dict[str, subprocess.Popen] = {}
        epochs: dict[str, float] = {}
        errs: dict[str, IO[bytes]] = {}
        try:
            for track in ("far", "mic"):
                errs[track] = tempfile.TemporaryFile()
                argv = build_argv(self.pw_record, targets[track], bundle_dir / files[track],
                                  monitor=(track == "far"))
                epochs[track] = self.clock()
                procs[track] = self._spawn(argv, errs[track])
                log.info("pw-record %s -> %s", track, targets[track])
        except BaseException:
            _kill_all(procs)
            for f in errs.values():
                f.close()
            raise
        time.sleep(self.startup_check_s)
        dead = {k: p.poll() for k, p in procs.items() if p.poll() is not None}
        if dead:
            _kill_all(procs)
            details = []
            for k, code in dead.items():
                errs[k].seek(0)
                tail = errs[k].read()[-300:].decode("utf-8", "replace").strip()
                details.append(f"{k} (código {code}{': ' + tail if tail else ''})")
            for f in errs.values():
                f.close()
            raise CaptureError("pw-record não iniciou: " + "; ".join(details))
        return PipeWireHandle(bundle_dir, procs, epochs, targets, errs, self.stop_timeout_s)


def _kill_all(procs: dict[str, subprocess.Popen]) -> None:
    for p in procs.values():
        if p.poll() is None:
            p.kill()
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
