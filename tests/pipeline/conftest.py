"""Fixtures do pipeline: hooks das outras partes trocados por dublês (nada de D roda aqui)."""

from __future__ import annotations

import pytest

from ata.pipeline import hooks

SUMMARY = {
    "language": "pt-BR",
    "tldr": "Pessoa 2 trouxe os números; lançamento no dia doze.",
    "topics": [{"title": "Lançamento", "start": 5.0, "summary": "Números do teste fechado"}],
    "decisions": [{"text": "Lançar no dia doze com duzentas pessoas", "evidence": [{"t": 16.0, "speaker": "Pessoa 2"}]}],
    "actions": [{"text": "Corrigir o erro dos tablets", "owner": "Pessoa 3", "due": "quarta",
                 "evidence": [{"t": 19.0, "speaker": "Pessoa 3"}]}],
    "questions": [{"text": "Cobramos o premium no beta?", "evidence": [{"t": 24.0, "speaker": "Eu"}]}],
}


@pytest.fixture(autouse=True)
def stub_hooks(monkeypatch):
    calls: dict[str, list] = {"summarize": [], "index": [], "after_note": [], "auto_label": []}

    def summarize(turns, language, config, my_notes=None):
        calls["summarize"].append((len(turns), language, my_notes))
        return dict(SUMMARY, language=language)

    monkeypatch.setattr(hooks, "summarize", summarize)
    monkeypatch.setattr(hooks, "index", lambda b, c: calls["index"].append(b))
    monkeypatch.setattr(hooks, "after_note", lambda b, n, c: calls["after_note"].append(n))

    def auto_label(b, c, spans, labels):
        calls["auto_label"].append(dict(labels))
        return {}

    monkeypatch.setattr(hooks, "auto_label", auto_label)
    return calls
