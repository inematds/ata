"""Protocolos dos motores (docs/CONTRATO.md §3). Toda implementação devolve tempo do ARQUIVO."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable

from ..types import Span, Word


class EngineError(RuntimeError):
    """Motor indisponível ou falhou; a mensagem diz o que fazer (sem texto de reunião)."""


class EngineMissing(EngineError):
    """Dependência do motor não instalada (pacote, binário, serviço)."""


@runtime_checkable
class AsrEngine(Protocol):
    name: str

    def transcribe(self, wav: Path, language: str) -> list[Word]: ...


@runtime_checkable
class Diarizer(Protocol):
    name: str

    def diarize(self, wav: Path, max_speakers: int = 0) -> list[Span]: ...


@runtime_checkable
class SpeakerEmbedder(Protocol):
    name: str

    def embed(self, wav: Path, spans: list[Span]) -> dict[str, list[float]]: ...


@runtime_checkable
class Summarizer(Protocol):
    name: str

    def summarize(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class TextEmbedder(Protocol):
    name: str

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class StreamSession(Protocol):
    def send(self, pcm16: bytes) -> None: ...
    def events(self) -> Iterator[dict[str, Any]]: ...   # {"type": "partial"|"final", "text", "start", "end", ...}
    def close(self) -> None: ...


@runtime_checkable
class StreamingAsr(Protocol):
    name: str

    def open(self, language: str, sample_rate: int = 16000) -> StreamSession: ...
