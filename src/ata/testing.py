"""Gerador de reuniões sintéticas de duas faixas (usado pelos testes e por `ata demo --no-tts`).

Cada fala vira um "tom de voz" (soma de senoides com envelope) na faixa certa, com vazamento opcional do far
no mic, e as fixtures dos motores fake (palavras e spans por faixa) são gravadas ao lado dos WAVs. Assim o
pipeline inteiro roda de verdade (gate, diarização, turnos, nota) sem modelo nenhum.
"""

from __future__ import annotations

import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from . import audio, bundle
from .engines.fake import write_fixture
from .types import Span, Word

VOICE_FREQS = {"Eu": (140.0, 280.0), "A": (220.0, 440.0), "B": (170.0, 510.0), "C": (250.0, 375.0)}


@dataclass(frozen=True)
class Line:
    who: str          # "Eu" (mic) ou um rótulo do outro lado ("A", "B", ...)
    text: str
    overlap: bool = False


def _tone(seconds: float, freqs: tuple[float, float], amp: float = 0.25) -> np.ndarray:
    n = int(seconds * audio.SAMPLE_RATE)
    t = np.arange(n) / audio.SAMPLE_RATE
    sig = np.sin(2 * np.pi * freqs[0] * t) + 0.5 * np.sin(2 * np.pi * freqs[1] * t)
    # modulação silábica ~4 Hz deixa parecido com fala para os detectores de energia
    env = 0.55 + 0.45 * np.sin(2 * np.pi * 4.0 * t)
    ramp = np.minimum(1.0, np.minimum(t, t[::-1] if n else t) / 0.02) if n else t
    return (amp * sig * env * ramp).astype(np.float32)


def synth_meeting(out_root: Path, lines: list[Line], *, title: str = "teste", language: str = "pt-BR",
                  bleed_db: float | None = -18.0, gap: float = 0.5, words_per_second: float = 2.5,
                  started: datetime | None = None, mic_offset: float = 0.0) -> Path:
    """Escreve um bundle ata/1 completo (far.wav, mic.wav, meta.json + fixtures fake) e devolve a pasta."""
    started = started or datetime.now().astimezone()
    bdir = bundle.new_bundle_dir(out_root, started, title)
    t = 1.0
    far_words: list[Word] = []
    mic_words: list[Word] = []
    far_spans: list[Span] = []
    placed: list[tuple[str, float, float, str]] = []
    for line in lines:
        toks = line.text.split()
        dur = max(0.6, len(toks) / words_per_second)
        start = t - 0.3 if (line.overlap and placed) else t
        end = start + dur
        placed.append((line.who, start, end, line.text))
        step = dur / max(1, len(toks))
        ws = [Word(tok, start + i * step, start + (i + 1) * step - 0.02, 0.9) for i, tok in enumerate(toks)]
        if line.who == "Eu":
            mic_words.extend(ws)
        else:
            far_words.extend(ws)
            far_spans.append(Span(start, end, f"S{ord(line.who[0]) - ord('A')}"))
        t = end + gap
    total = t + 1.0
    n = int(total * audio.SAMPLE_RATE)
    far = np.zeros(n, np.float32)
    mic = np.zeros(n, np.float32)
    for who, start, end, _ in placed:
        a = int(start * audio.SAMPLE_RATE)
        seg = _tone(end - start, VOICE_FREQS.get(who, (300.0, 600.0)))
        target = mic if who == "Eu" else far
        target[a:a + len(seg)] += seg[: max(0, n - a)]
    if bleed_db is not None:
        mic += far * (10 ** (bleed_db / 20.0))
        if bleed_db > -40:
            # o ASR "ouve" no mic as palavras vazadas do far (o gate tem que tirá-las)
            mic_words = sorted(mic_words + far_words, key=lambda w: w.start)
    if mic_offset:
        k = int(mic_offset * audio.SAMPLE_RATE)
        mic = np.concatenate([np.zeros(k, np.float32), mic])[:n] if k > 0 else np.concatenate(
            [mic[-k:], np.zeros(-k, np.float32)])
    audio.write_wav(bdir / "far.wav", far)
    audio.write_wav(bdir / "mic.wav", mic)
    write_fixture(bdir / "far.wav", far_words, far_spans)
    write_fixture(bdir / "mic.wav", mic_words, [Span(w.start, w.end, "S0") for w in mic_words])
    epoch = started.timestamp()
    meta = bundle.BundleMeta(
        name=bdir.name, created_at=started.isoformat(timespec="seconds"),
        stopped_at=(started + timedelta(seconds=total)).isoformat(timespec="seconds"),
        slug=bundle.slugify(title), title=title, host=socket.gethostname(), platform=sys.platform,
        recorder={"name": "ata-synth", "version": "0"}, language_requested=language,
        tracks={k: bundle.Track(file=f"{k}.wav", device="synthetic", start_epoch=epoch, start_measured=True,
                                samples=n, silent=False) for k in ("far", "mic")})
    bundle.write_meta(bdir, meta)
    return bdir


DEFAULT_LINES_PT = [
    Line("Eu", "Bom dia a todos vamos começar pela agenda do lançamento"),
    Line("A", "Tenho os números do teste fechado com cento e quarenta pessoas"),
    Line("Eu", "Ótimo pode mostrar"),
    Line("B", "A maior parte dos erros vem dos tablets antigos"),
    Line("A", "Decidimos lançar no dia doze com duzentas pessoas"),
    Line("B", "Eu vou corrigir o erro dos tablets até quarta"),
    Line("Eu", "Cobramos o plano premium durante o beta?"),
]
