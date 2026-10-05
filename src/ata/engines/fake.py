"""Motores simulados, determinísticos, sem modelo e sem rede. Usados nos testes e com ATA_ENGINES=fake.

Convenção de fixtures (escritas por `ata demo --no-tts` e pelos testes), ao lado do WAV:
  <wav>.fake-words.json   [{"text", "start", "end"}, ...]           -> FakeAsr devolve isso
  <wav>.fake-spans.json   [{"start", "end", "speaker"}, ...]        -> FakeDiarizer devolve isso
Sem fixture: FakeAsr gera uma palavra "fala" por trecho com energia; FakeDiarizer põe tudo em "S0".
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterator

from .. import audio
from ..types import Span, Word


def _sidecar(wav: Path, kind: str) -> Path:
    return Path(str(wav) + f".fake-{kind}.json")


def energy_segments(wav: Path, threshold_db: float = -45.0, min_gap: float = 0.3) -> list[tuple[float, float]]:
    x = audio.read_wav(wav)
    block = 320  # 20 ms
    segs: list[list[float]] = []
    for i in range(0, len(x) - block + 1, block):
        db = audio.rms_db(x[i:i + block])
        t = i / audio.SAMPLE_RATE
        if db > threshold_db:
            if segs and t - segs[-1][1] <= min_gap:
                segs[-1][1] = t + block / audio.SAMPLE_RATE
            else:
                segs.append([t, t + block / audio.SAMPLE_RATE])
    return [(a, b) for a, b in segs if b - a >= 0.1]


class FakeAsr:
    name = "fake"

    def transcribe(self, wav: Path, language: str) -> list[Word]:
        side = _sidecar(Path(wav), "words")
        if side.is_file():
            return [Word.from_json(w) for w in json.loads(side.read_text(encoding="utf-8"))]
        return [Word("fala", a, b, 0.5) for a, b in energy_segments(Path(wav))]


class FakeDiarizer:
    name = "fake"

    def diarize(self, wav: Path, max_speakers: int = 0) -> list[Span]:
        side = _sidecar(Path(wav), "spans")
        if side.is_file():
            return [Span.from_json(s) for s in json.loads(side.read_text(encoding="utf-8"))]
        return [Span(a, b, "S0") for a, b in energy_segments(Path(wav))]


class FakeSpeakerEmbedder:
    """Vetor determinístico por (arquivo, rótulo); o mesmo rótulo de fixture com o mesmo nome de voz gera o
    mesmo vetor se a fixture de spans tiver o campo opcional "voice"."""

    name = "fake"

    def embed(self, wav: Path, spans: list[Span]) -> dict[str, list[float]]:
        voices: dict[str, str] = {}
        side = _sidecar(Path(wav), "spans")
        if side.is_file():
            for s in json.loads(side.read_text(encoding="utf-8")):
                if s.get("voice"):
                    voices[str(s["speaker"])] = str(s["voice"])
        out = {}
        for label in sorted({s.speaker for s in spans}):
            key = voices.get(label, f"{Path(wav).name}:{label}")
            out[label] = _unit_vector(key)
        return out


def _unit_vector(key: str, dim: int = 16) -> list[float]:
    h = hashlib.sha256(key.encode()).digest()
    v = [(b - 127.5) / 127.5 for b in h[:dim]]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


_DECISION = re.compile(r"\b(decid\w*|decided|we will|vamos|ficou definido|acordamos|quedamos|acordado)\b", re.I)
_ACTION = re.compile(r"\b(vou|vai|will|i'll|voy a|va a|preciso|need to|tengo que|até|by|para el)\b", re.I)
_QUESTION = re.compile(r"\?\s*$")


class FakeSummarizer:
    """Resumo por regras sobre as linhas `[mm:ss] Falante: texto` do prompt. Sempre valida no schema."""

    name = "fake"

    def summarize(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        lang = "pt-BR"
        m = re.search(r"LANGUAGE:\s*(\S+)", prompt)
        if m:
            lang = m.group(1)
        lines = re.findall(r"^\[(\d+):(\d\d)\]\s*([^:]+):\s*(.+)$", prompt, re.M)
        decisions, actions, questions, topics = [], [], [], []
        for mm, ss, who, text in lines:
            t = int(mm) * 60 + int(ss)
            ev = [{"t": float(t), "speaker": who.strip()}]
            if _DECISION.search(text):
                decisions.append({"text": text.strip(), "evidence": ev})
            elif _QUESTION.search(text):
                questions.append({"text": text.strip(), "evidence": ev})
            elif _ACTION.search(text):
                actions.append({"text": text.strip(), "owner": who.strip(), "due": None, "evidence": ev})
        if lines:
            topics.append({"title": lines[0][3][:60], "start": 0.0, "summary": lines[0][3][:200]})
        tldr = (lines[0][3][:160] if lines else "")
        return {"language": lang, "tldr": tldr, "topics": topics, "decisions": decisions[:10],
                "actions": actions[:10], "questions": questions[:10]}


class FakeTextEmbedder:
    """Bolsa de palavras com hashing (dimensão 64), normalizada: busca semântica 'de mentira' mas estável."""

    name = "fake"
    dim = 64

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for tok in re.findall(r"\w+", t.lower()):
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


class _FakeStream:
    def __init__(self, language: str) -> None:
        self.language = language
        self.received = 0
        self._events: list[dict[str, Any]] = []

    def send(self, pcm16: bytes) -> None:
        before = self.received // 32000
        self.received += len(pcm16)
        after = self.received // 32000          # 1 s de áudio = 32000 bytes
        for sec in range(before, after):
            self._events.append({"type": "final", "text": f"segundo {sec + 1}", "start": float(sec),
                                 "end": float(sec + 1)})

    def events(self) -> Iterator[dict[str, Any]]:
        while self._events:
            yield self._events.pop(0)

    def close(self) -> None:
        pass


class FakeStreamingAsr:
    name = "fake"

    def open(self, language: str, sample_rate: int = 16000) -> _FakeStream:
        return _FakeStream(language)


def write_fixture(wav: Path, words: list[Word] | None = None, spans: list[Span] | None = None,
                  voices: dict[str, str] | None = None) -> None:
    """Grava as fixtures lidas pelos motores fake (usado por demo --no-tts e testes)."""
    if words is not None:
        _sidecar(wav, "words").write_text(json.dumps([w.to_json() for w in words], ensure_ascii=False),
                                          encoding="utf-8")
    if spans is not None:
        rows = []
        for s in spans:
            d = s.to_json()
            if voices and s.speaker in voices:
                d["voice"] = voices[s.speaker]
            rows.append(d)
        _sidecar(wav, "spans").write_text(json.dumps(rows), encoding="utf-8")
