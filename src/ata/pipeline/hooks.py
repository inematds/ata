"""Pontos de chamada do pipeline para as outras partes (conhecimento, vozes, conexões).

Cada hook importa a função da parte D só na hora. Se o MÓDULO ainda não existe (``ImportError`` cujo
``name`` é exatamente esse módulo), devolve um valor neutro e registra uma vez no log; qualquer outro erro de
import (ex.: o módulo existe mas depende de um pacote ausente) sobe normalmente.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any, Callable

from ..config import Config
from ..types import Span, Turn

log = logging.getLogger("ata.pipeline")

SUMMARY = ("ata.knowledge.summary", "summarize_turns")
VOICES = ("ata.voices", "auto_label")
INDEX = ("ata.knowledge.index", "index_bundle")
CONNECT = ("ata.connect", "after_note")

_missing_logged: set[str] = set()


def _load(module: str, attr: str) -> Callable[..., Any] | None:
    try:
        mod = importlib.import_module(module)
    except ImportError as exc:
        if exc.name != module:
            raise
        if module not in _missing_logged:
            _missing_logged.add(module)
            log.info("módulo %s ainda não instalado; etapa pulada", module)
        return None
    return getattr(mod, attr)


def summarize(turns: list[Turn], language: str, config: Config, my_notes: str | None = None) -> dict | None:
    fn = _load(*SUMMARY)
    if fn is None:
        return None
    return fn(turns, language, config, my_notes=my_notes)


def auto_label(bundle_dir: Path, config: Config, spans_by_track: dict[str, list[Span]],
               labels: dict[str, str]) -> dict[str, str]:
    fn = _load(*VOICES)
    if fn is None:
        return {}
    return dict(fn(bundle_dir, config, spans_by_track, labels) or {})


def index(bundle_dir: Path, config: Config) -> None:
    fn = _load(*INDEX)
    if fn is not None:
        fn(bundle_dir, config)


def after_note(bundle_dir: Path, note_path: Path, config: Config) -> None:
    fn = _load(*CONNECT)
    if fn is not None:
        fn(bundle_dir, note_path, config)
