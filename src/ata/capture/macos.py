"""Captura no macOS via helper Swift ``ata-audio`` (fonte em ``helpers/macos/AtaAudio.swift``).

Protocolo (um JSON por linha no stdout do helper; stderr é só diagnóstico):

    ata-audio record --far <far.wav> --mic <mic.wav> [--far-device ID] [--mic-device ID]
    {"event": "started", "track": "far", "start_epoch": 1791234567.123, "device": "System Audio"}
    {"event": "started", "track": "mic", "start_epoch": 1791234567.125, "device": "MacBook Pro Microphone"}
    {"event": "level",   "track": "far", "db": -23.5}
    {"event": "error",   "track": "far", "code": "permission_denied", "message": "..."}
    {"event": "stopped", "track": "far", "samples": 2531200}
    ata-audio devices  ->  {"event": "devices", "far": {...}, "mic": {...}}

Parada: SIGINT (o helper fecha os WAVs e emite ``stopped``). ``start_epoch`` vem do relógio do host no 1º
buffer de cada faixa (mesmo relógio para as duas) -> ``start_measured=True``.

Permissão negada (``code = permission_denied``) NUNCA é falha muda: a faixa vira ``silent=True`` (o arquivo é
criado com zeros se o helper não o criou), o motivo ``far_silent``/``mic_silent`` entra no bundle e um aviso
pt-BR explica onde liberar ("Ajustes > Privacidade e Segurança > Gravação de Tela e Áudio do Sistema").
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Sequence

import numpy as np

from .. import audio
from ..bundle import Track
from .base import (FAR_FILE, MIC_FILE, CaptureError, CaptureMissing, UnsupportedPlatform, configured_target,
                   device_listing, finalize_track)

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger("ata.capture.macos")

EVENTS = ("started", "level", "stopped", "error", "devices")
ERROR_CODES = ("permission_denied", "device_missing", "tap_failed", "unsupported_os", "write_failed")
PERMISSION_HINT = {
    "far": "libere o Ata em Ajustes do Sistema > Privacidade e Segurança > Gravação de Tela e Áudio do Sistema "
           "(\"Somente áudio do sistema\")",
    "mic": "libere o Ata em Ajustes do Sistema > Privacidade e Segurança > Microfone",
}
BUILD_HINT = "compile o helper: veja helpers/macos/README.md (swiftc ... -o ata-audio) e ponha no PATH " \
             "ou em ATA_AUDIO_HELPER"


@dataclass(frozen=True)
class HelperEvent:
    event: str
    track: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


def parse_event(line: str) -> HelperEvent | None:
    """Uma linha do helper -> HelperEvent; linha vazia, JSON inválido ou evento desconhecido -> None."""
    s = line.strip()
    if not s:
        return None
    try:
        d = json.loads(s)
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or d.get("event") not in EVENTS:
        return None
    track = d.get("track")
    if track is not None and track not in ("far", "mic"):
        return None
    return HelperEvent(str(d["event"]), track, {k: v for k, v in d.items() if k not in ("event", "track")})


@dataclass
class HelperState:
    """Agrega os eventos do helper. ``apply`` é chamado na thread leitora."""

    start_epoch: dict[str, float] = field(default_factory=dict)
    device: dict[str, str] = field(default_factory=dict)
    level_db: dict[str, float] = field(default_factory=dict)
    samples: dict[str, int] = field(default_factory=dict)
    stopped: set[str] = field(default_factory=set)
    denied: set[str] = field(default_factory=set)
    errors: list[tuple[str | None, str]] = field(default_factory=list)   # (track, code)
    devices: dict[str, Any] = field(default_factory=dict)

    def apply(self, ev: HelperEvent) -> None:
        d = ev.data
        if ev.event == "started" and ev.track:
            epoch = d.get("start_epoch")
            if isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
                self.start_epoch[ev.track] = float(epoch)
            if isinstance(d.get("device"), str):
                self.device[ev.track] = d["device"]
        elif ev.event == "level" and ev.track:
            db = d.get("db")
            if isinstance(db, (int, float)) and not isinstance(db, bool):
                self.level_db[ev.track] = float(db)
        elif ev.event == "stopped":
            for tr in ([ev.track] if ev.track else ["far", "mic"]):
                self.stopped.add(tr)
                if isinstance(d.get("samples"), int) and ev.track:
                    self.samples[tr] = d["samples"]
        elif ev.event == "error":
            code = str(d.get("code", "unknown"))
            self.errors.append((ev.track, code))
            if code == "permission_denied":
                self.denied.update([ev.track] if ev.track else ["far", "mic"])
        elif ev.event == "devices":
            self.devices = {k: v for k, v in d.items() if k in ("far", "mic")}

    def fatal_error(self) -> str | None:
        """Erro que impede gravar (qualquer código além de permission_denied)."""
        for track, code in self.errors:
            if code != "permission_denied":
                return f"{code}" + (f" ({track})" if track else "")
        return None

    def warnings(self) -> list[str]:
        out = []
        for tr in ("far", "mic"):
            if tr in self.denied:
                quem = "áudio do sistema" if tr == "far" else "microfone"
                out.append(f"permissão de {quem} negada: a faixa {tr} ficou em silêncio; {PERMISSION_HINT[tr]}")
        return out


def feed(state: HelperState, lines: Sequence[str]) -> HelperState:
    """Aplica várias linhas (útil para fixtures)."""
    for line in lines:
        ev = parse_event(line)
        if ev is not None:
            state.apply(ev)
    return state


class MacHelperHandle:
    def __init__(self, bundle_dir: Path, proc: subprocess.Popen, state: HelperState, reader: threading.Thread,
                 stop_timeout_s: float = 10.0) -> None:
        self.bundle_dir = Path(bundle_dir)
        self.proc = proc
        self.state = state
        self.reader = reader
        self.stop_timeout_s = stop_timeout_s
        self.damage_reasons: list[str] = []
        self.warnings: list[str] = []
        self._result: dict[str, Track] | None = None

    def preview(self) -> dict[str, Track]:
        files = {"far": FAR_FILE, "mic": MIC_FILE}
        return {k: Track(file=files[k], device=self.state.device.get(k, ""),
                         start_epoch=self.state.start_epoch.get(k), start_measured=k in self.state.start_epoch)
                for k in ("far", "mic")}

    def poll(self) -> str | None:
        code = self.proc.poll()
        return None if code is None else f"helper ata-audio saiu (código {code})"

    def stop(self) -> dict[str, Track]:
        if self._result is not None:
            return self._result
        if self.proc.poll() is None:
            try:
                self.proc.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
        try:
            self.proc.wait(timeout=self.stop_timeout_s)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            self.damage_reasons.append("recorder_killed")
        self.reader.join(timeout=2)
        files = {"far": FAR_FILE, "mic": MIC_FILE}
        out: dict[str, Track] = {}
        lengths = []
        for k in ("far", "mic"):
            p = self.bundle_dir / files[k]
            if p.is_file():
                audio.repair_header(p)
                lengths.append(audio.duration(p))
        for k in ("far", "mic"):
            denied = k in self.state.denied
            p = self.bundle_dir / files[k]
            if denied and not p.is_file():
                n = int(max(lengths, default=0.0) * audio.SAMPLE_RATE)
                audio.write_wav(p, np.zeros(n, np.int16))
            epoch = self.state.start_epoch.get(k)
            tr = finalize_track(self.bundle_dir, files[k], device=self.state.device.get(k, ""),
                                start_epoch=epoch, start_measured=epoch is not None, force_silent=denied)
            if tr is not None:
                out[k] = tr
            if denied:
                self.damage_reasons.append(f"{k}_silent")
        self.warnings.extend(self.state.warnings())
        if any(k not in self.state.stopped for k in out) and "recorder_killed" not in self.damage_reasons:
            log.warning("helper não confirmou 'stopped' para todas as faixas")
        self._result = out
        return out


def _read_events(stream: IO[str], state: HelperState) -> None:
    for line in stream:
        ev = parse_event(line)
        if ev is None:
            continue
        state.apply(ev)
        if ev.event == "error":
            log.warning("helper: erro %s (%s)", ev.data.get("code"), ev.track or "-")


class MacHelperBackend:
    """Roda o helper Swift. ``helper`` injetável (argv prefixo) para testes fora do macOS."""

    name = "macos-coreaudio-tap"
    recorder_name = "ata-macos-tap"

    def __init__(self, config: "Config | None" = None, *, helper: Sequence[str] | None = None,
                 startup_timeout_s: float = 5.0, stop_timeout_s: float = 10.0) -> None:
        self.config = config
        self._helper = tuple(helper) if helper else None
        self.startup_timeout_s = startup_timeout_s
        self.stop_timeout_s = stop_timeout_s

    def helper_argv(self) -> tuple[str, ...]:
        if self._helper:
            return self._helper
        if sys.platform != "darwin":
            raise UnsupportedPlatform("o helper ata-audio (Core Audio tap) só roda no macOS 14.2+")
        cand = os.environ.get("ATA_AUDIO_HELPER") or (self.config.get("audio.macos_helper") if self.config
                                                      else None) or shutil.which("ata-audio")
        if not cand or not Path(str(cand)).expanduser().is_file():
            raise CaptureMissing(f"helper ata-audio não encontrado: {BUILD_HINT}")
        return (str(Path(str(cand)).expanduser()),)

    def devices(self) -> dict[str, Any]:
        try:
            argv = self.helper_argv()
            res = subprocess.run([*argv, "devices"], capture_output=True, text=True, timeout=10)
        except CaptureError as exc:
            return device_listing(self.name, available=False, problems=[str(exc)])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return device_listing(self.name, available=False, problems=[f"ata-audio devices: {type(exc).__name__}"])
        state = feed(HelperState(), (res.stdout or "").splitlines())
        problems = [f"ata-audio: {c}" for _, c in state.errors]
        return device_listing(self.name, available=res.returncode == 0 and not problems,
                              far=state.devices.get("far"), mic=state.devices.get("mic"), problems=problems)

    def start(self, bundle_dir: Path, far: str | None = None, mic: str | None = None) -> MacHelperHandle:
        bundle_dir = Path(bundle_dir)
        argv = [*self.helper_argv(), "record", "--far", str(bundle_dir / FAR_FILE), "--mic",
                str(bundle_dir / MIC_FILE)]
        far = far or configured_target(self.config, "far")[0]
        mic = mic or configured_target(self.config, "mic")[0]
        if far:
            argv += ["--far-device", far]
        if mic:
            argv += ["--mic-device", mic]
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True, bufsize=1, start_new_session=True)
        except FileNotFoundError:
            raise CaptureMissing(f"helper ata-audio não encontrado: {BUILD_HINT}") from None
        state = HelperState()
        reader = threading.Thread(target=_read_events, args=(proc.stdout, state), name="ata-audio-reader",
                                  daemon=True)
        reader.start()
        deadline = time.monotonic() + self.startup_timeout_s
        while time.monotonic() < deadline:
            settled = {t for t in ("far", "mic") if t in state.start_epoch or t in state.denied}
            if state.fatal_error() or settled == {"far", "mic"} or proc.poll() is not None:
                break
            time.sleep(0.02)
        fatal = state.fatal_error()
        if fatal or (proc.poll() is not None and not state.denied):
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            reader.join(timeout=2)
            raise CaptureError(f"helper ata-audio não iniciou: {fatal or f'saiu com código {proc.returncode}'}")
        return MacHelperHandle(bundle_dir, proc, state, reader, self.stop_timeout_s)
