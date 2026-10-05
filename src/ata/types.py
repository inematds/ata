"""Tipos de dados compartilhados por todo o Ata (ver docs/CONTRATO.md §2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

TrackName = Literal["far", "mic"]
TRACKS: tuple[TrackName, ...] = ("far", "mic")


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float
    confidence: float | None = None

    def shifted(self, offset: float) -> "Word":
        return Word(self.text, self.start + offset, self.end + offset, self.confidence)

    def to_json(self) -> dict[str, Any]:
        d = {"text": self.text, "start": round(self.start, 3), "end": round(self.end, 3)}
        if self.confidence is not None:
            d["confidence"] = round(self.confidence, 4)
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Word":
        return cls(str(d["text"]), float(d["start"]), float(d["end"]),
                   None if d.get("confidence") is None else float(d["confidence"]))


@dataclass(frozen=True)
class Span:
    start: float
    end: float
    speaker: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_json(self) -> dict[str, Any]:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "speaker": self.speaker}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Span":
        return cls(float(d["start"]), float(d["end"]), str(d["speaker"]))


@dataclass
class Turn:
    start: float
    end: float
    speaker: str
    text: str
    track: TrackName
    words: list[Word] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "speaker": self.speaker,
                "text": self.text, "track": self.track, "words": [w.to_json() for w in self.words]}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Turn":
        return cls(float(d["start"]), float(d["end"]), str(d["speaker"]), str(d["text"]),
                   d["track"], [Word.from_json(w) for w in d.get("words", [])])


def to_plain(obj: Any) -> Any:
    """dataclass -> dict recursivo (para json.dumps)."""
    if hasattr(obj, "to_json"):
        return obj.to_json()
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    if isinstance(obj, (list, tuple)):
        return [to_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_plain(v) for k, v in obj.items()}
    return obj
