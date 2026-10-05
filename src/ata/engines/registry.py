"""Escolha de motor por papel e idioma. ``ATA_ENGINES=fake`` força tudo para os motores simulados.

Referências de config têm a forma ``motor:modelo`` (ex.: ``nemo:parakeet-tdt-0.6b-v3``). Os módulos reais
(nemo, onnx, ollama, cli_llm) são importados só quando escolhidos, para o pacote base não depender deles.
"""

from __future__ import annotations

import importlib
import os
from typing import Any, Callable

from ..config import Config
from .base import EngineError, EngineMissing
from . import fake

ROLES = ("asr", "diarizer", "speaker_embedder", "summarizer", "text_embedder", "streaming")

# papel -> motor -> "modulo:fabrica"; a fábrica recebe (config, modelo) e devolve a instância
_FACTORIES: dict[str, dict[str, str]] = {
    "asr": {"nemo": "ata.engines.nemo:make_asr", "onnx": "ata.engines.onnx:make_asr"},
    "diarizer": {"nemo": "ata.engines.nemo:make_diarizer", "onnx": "ata.engines.onnx:make_diarizer"},
    "speaker_embedder": {"onnx": "ata.engines.onnx:make_speaker_embedder",
                         "nemo": "ata.engines.onnx:make_speaker_embedder"},
    "summarizer": {"ollama": "ata.engines.ollama:make_summarizer",
                   "claude": "ata.engines.cli_llm:make_claude", "codex": "ata.engines.cli_llm:make_codex"},
    "text_embedder": {"ollama": "ata.engines.ollama:make_text_embedder"},
    "streaming": {"nemo": "ata.engines.nemo:make_streaming"},
}

_FAKES: dict[str, Callable[[], Any]] = {
    "asr": fake.FakeAsr, "diarizer": fake.FakeDiarizer, "speaker_embedder": fake.FakeSpeakerEmbedder,
    "summarizer": fake.FakeSummarizer, "text_embedder": fake.FakeTextEmbedder,
    "streaming": fake.FakeStreamingAsr,
}


def forced_fake() -> bool:
    return os.environ.get("ATA_ENGINES", "").strip().lower() == "fake"


def split_ref(ref: str) -> tuple[str, str]:
    engine, _, model = str(ref).partition(":")
    return engine.strip().lower(), model.strip()


def _load(path: str) -> Callable[..., Any]:
    mod, _, fn = path.partition(":")
    try:
        return getattr(importlib.import_module(mod), fn)
    except ImportError as exc:
        raise EngineMissing(f"motor {mod} indisponível: {exc}") from None


def build(role: str, ref: str, config: Config) -> Any:
    if role not in ROLES:
        raise ValueError(f"papel desconhecido: {role}")
    engine, model = split_ref(ref)
    if forced_fake() or engine == "fake":
        return _FAKES[role]()
    factories = _FACTORIES[role]
    if engine not in factories:
        raise EngineError(f"motor {engine!r} não serve para {role} (opções: {', '.join(factories)}, fake)")
    return _load(factories[engine])(config, model)


def asr_ref(config: Config, language: str) -> str:
    return str(config.get(f"asr.{language}") or config.get("asr.pt-BR"))


def asr_for(config: Config, language: str) -> Any:
    return build("asr", asr_ref(config, language), config)


def asr_fallback(config: Config) -> Any:
    return build("asr", str(config.get("asr.fallback")), config)


def diarizer_for(config: Config) -> Any:
    return build("diarizer", str(config.get("diarization.engine")), config)


def summarizer_for(config: Config) -> Any | None:
    provider = str(config.get("summary.provider"))
    if provider == "none" and not forced_fake():
        return None
    model = {"ollama": config.get("summary.ollama_model"), "claude": config.get("summary.claude_model"),
             "codex": config.get("summary.codex_model")}.get(provider, "")
    return build("summarizer", f"{provider}:{model}", config)


def text_embedder_for(config: Config) -> Any:
    return build("text_embedder", f"{config.get('embeddings.provider')}:{config.get('embeddings.model')}",
                 config)


def streaming_for(config: Config) -> Any:
    return build("streaming", str(config.get("live.engine")), config)
