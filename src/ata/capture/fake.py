"""Backend de captura sintético (``ATA_CAPTURE=fake``): para testes do gravador, do MCP, do dashboard e do live.

Contrato: ``start()`` escreve JÁ no início um ``far.wav``/``mic.wav`` com uma reunião sintética de
``ata.testing.synth_meeting`` (as mesmas falas sempre, independente do tempo gravado) e as fixtures dos motores
fake ao lado (``<wav>.fake-words.json``/``.fake-spans.json``), então o pipeline com ``ATA_ENGINES=fake``
produz uma nota de verdade. ``stop()`` só mede e devolve as faixas.

Modos (``ATA_FAKE_CAPTURE`` ou argumento ``mode``):
- ``meeting`` (padrão): duas faixas, vazamento -18 dB, ``start_measured=True``;
- ``silent_far``: far em zeros, sem fixture -> ``far_silent``;
- ``unmeasured``: sem vazamento e ``start_measured=False`` -> ``start_unmeasured``;
- ``fail``: ``start()`` levanta CaptureError (testa o caminho de falha do supervisor).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .. import audio
from ..bundle import Track
from ..testing import DEFAULT_LINES_PT, Line, synth_meeting
from .base import FAR_FILE, MIC_FILE, CaptureError, device_listing, finalize_track

MODES = ("meeting", "silent_far", "unmeasured", "fail")


class FakeHandle:
    def __init__(self, bundle_dir: Path, epoch: float, measured: bool, silent_far: bool) -> None:
        self.bundle_dir = Path(bundle_dir)
        self.epoch = epoch
        self.measured = measured
        self.silent_far = silent_far
        self.damage_reasons: list[str] = []
        self.warnings: list[str] = []
        self.stopped = False
        self._result: dict[str, Track] | None = None

    def preview(self) -> dict[str, Track]:
        return {k: Track(file=f, device="synthetic", start_epoch=self.epoch, start_measured=self.measured)
                for k, f in (("far", FAR_FILE), ("mic", MIC_FILE))}

    def poll(self) -> str | None:
        return None

    def stop(self) -> dict[str, Track]:
        if self._result is None:
            self.stopped = True
            out = {}
            for k, f in (("far", FAR_FILE), ("mic", MIC_FILE)):
                tr = finalize_track(self.bundle_dir, f, device="synthetic", start_epoch=self.epoch,
                                    start_measured=self.measured)
                if tr is not None:
                    out[k] = tr
            self._result = out
        return self._result


class FakeBackend:
    name = "fake"
    recorder_name = "ata-fake"

    def __init__(self, mode: str = "meeting", lines: list[Line] | None = None, language: str = "pt-BR") -> None:
        if mode not in MODES:
            raise ValueError(f"modo fake desconhecido: {mode!r} (use {', '.join(MODES)})")
        self.mode = mode
        self.lines = lines or DEFAULT_LINES_PT
        self.language = language

    @classmethod
    def from_env(cls) -> "FakeBackend":
        return cls(mode=(os.environ.get("ATA_FAKE_CAPTURE") or "meeting").strip() or "meeting")

    def devices(self) -> dict[str, Any]:
        return device_listing(self.name, available=True,
                              far={"id": "fake-far", "description": "faixa sintética", "source": "default"},
                              mic={"id": "fake-mic", "description": "microfone sintético", "source": "default"})

    def start(self, bundle_dir: Path, far: str | None = None, mic: str | None = None) -> FakeHandle:
        if self.mode == "fail":
            raise CaptureError("captura fake configurada para falhar (ATA_FAKE_CAPTURE=fail)")
        bundle_dir = Path(bundle_dir)
        epoch = time.time()
        bleed = None if self.mode == "unmeasured" else -18.0
        with tempfile.TemporaryDirectory(prefix="ata-fake-capture-") as tmp:
            src = synth_meeting(Path(tmp), self.lines, title="fake", language=self.language, bleed_db=bleed,
                                started=datetime.fromtimestamp(epoch).astimezone())
            for f in src.iterdir():
                if f.name.startswith(("far.wav", "mic.wav")):
                    shutil.move(str(f), bundle_dir / f.name)
        if self.mode == "silent_far":
            n = len(audio.read_wav(bundle_dir / FAR_FILE))
            audio.write_wav(bundle_dir / FAR_FILE, np.zeros(n, np.int16))
            for side in bundle_dir.glob("far.wav.fake-*.json"):
                side.unlink()
        return FakeHandle(bundle_dir, epoch, measured=self.mode != "unmeasured",
                          silent_far=self.mode == "silent_far")
