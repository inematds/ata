"""Fixtures da parte E: reunião "processada" sem o pipeline real e fakes das partes A, B e D.

As partes A/B/D são construídas em paralelo; aqui elas viram módulos falsos em ``sys.modules`` com os
NOMES EXATOS de docs/INTERFACES.md, e cada chamada fica registrada em ``fakes.calls``.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

import ata
from ata import bundle
from ata.types import Turn, Word

TURNS = [
    Turn(1.0, 4.0, "Eu", "Bom dia a todos vamos começar pela agenda do lançamento", "mic"),
    Turn(4.5, 8.0, "Pessoa 2", "Tenho os números do teste fechado com cento e quarenta pessoas", "far"),
    Turn(8.5, 10.0, "Eu", "Ótimo pode mostrar", "mic"),
    Turn(10.5, 13.0, "Pessoa 3", "A maior parte dos erros vem dos tablets antigos", "far"),
    Turn(13.5, 16.0, "Pessoa 2", "Decidimos lançar no dia doze com duzentas pessoas", "far"),
    Turn(16.5, 19.0, "Pessoa 3", "Eu vou corrigir o erro dos tablets até quarta", "far"),
    Turn(19.5, 21.0, "Eu", "Cobramos o plano premium durante o beta?", "mic"),
]
SUMMARY = {
    "language": "pt-BR", "tldr": "Lançamento do beta no dia doze.",
    "topics": [{"title": "Lançamento", "start": 1.0, "summary": "agenda"}],
    "decisions": [{"text": "Lançar no dia doze com duzentas pessoas", "evidence": [{"t": 13.5, "speaker": "Pessoa 2"}]}],
    "actions": [{"text": "Corrigir o erro dos tablets", "owner": "Pessoa 3", "due": "quarta",
                 "evidence": [{"t": 16.5, "speaker": "Pessoa 3"}]}],
    "questions": [{"text": "Cobramos o plano premium durante o beta?", "evidence": [{"t": 19.5, "speaker": "Eu"}]}],
}


def make_processed(bdir: Path, config) -> Path:
    turns = [Turn(t.start, t.end, t.speaker, t.text, t.track, [Word(w, t.start, t.end) for w in t.text.split()[:1]])
             for t in TURNS]
    bundle.write_turns(bdir, turns)
    bundle.write_json(bdir / "summary.json", SUMMARY)
    bundle.write_text(bdir / "note.md", f"---\nbundle: {bdir}\n---\n# Nota\n\nResumo do lançamento.\n")
    bundle.write_json(bdir / "done.json", {"ok": True})
    return bdir


@pytest.fixture
def processed(meeting, config) -> Path:
    return make_processed(meeting, config)


@dataclass
class FakeHit:
    meeting: str
    title: str
    date: str
    start: float
    speaker: str
    text: str
    score: float
    note_path: Path | None


class Fakes:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.phase = "idle"
        self.bundle: Path | None = None


@pytest.fixture
def fakes(monkeypatch, config, tmp_path):
    f = Fakes()

    rec = types.ModuleType("ata.recorder")

    def start(config, *, title=None, speakers=None, language=None):
        f.calls.append(("recorder.start", title, speakers, language))
        f.phase = "recording"
        f.bundle = config.recordings / "2026-10-05-1000-gravando"
        f.bundle.mkdir(parents=True, exist_ok=True)
        return f.bundle

    def stop(config, *, process=True):
        f.calls.append(("recorder.stop", process))
        f.phase = "idle"
        return {"bundle": str(f.bundle), "note": None}

    def status(config):
        return {"phase": f.phase, "bundle": str(f.bundle) if f.bundle and f.phase == "recording" else None,
                "started_at": None, "elapsed_s": 3.0 if f.phase == "recording" else None, "pid": None}

    rec.start, rec.stop, rec.status = start, stop, status

    run = types.ModuleType("ata.pipeline.run")

    def process_bundle(bundle_dir, config, *, language=None, speakers=None, summarize=True, log=None):
        f.calls.append(("process_bundle", Path(bundle_dir).name, language, speakers, summarize))
        make_processed(Path(bundle_dir), config)
        out = config.notes / f"{Path(bundle_dir).name}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("# nota\n", encoding="utf-8")
        return out

    run.process_bundle = process_bundle

    idx = types.ModuleType("ata.knowledge.index")

    def search(config, query, mode="hybrid", limit=10, filters=None):
        f.calls.append(("index.search", query, mode, limit))
        return [FakeHit("m1", "Reunião 1", "2026-10-05", 12.0 + i, "Ana", f"trecho {i} {query}", 1.0 - i / 100,
                        None) for i in range(limit)]

    def reindex(config):
        f.calls.append(("index.reindex",))
        return {"bundles": 1}

    idx.search, idx.reindex = search, reindex

    ask_mod = types.ModuleType("ata.knowledge.ask")

    def ask(config, question, *, meeting=None):
        f.calls.append(("ask", question, meeting))
        return {"answer": "dia doze", "evidence": [{"meeting": "m1", "t": "00:13"}]}

    ask_mod.ask = ask

    prep_mod = types.ModuleType("ata.knowledge.prep")

    def build_prep(config, query, days=90, calendar=None, *, dry_run=False):
        f.calls.append(("build_prep", query, days, dry_run))
        return "# Preparação\n\n## Roteiro\n- (q1) Quando é o lançamento do beta\n- (q2) Quem corrige os tablets\n"

    prep_mod.build_prep = build_prep

    for name, mod in {"ata.recorder": rec, "ata.pipeline.run": run, "ata.knowledge.index": idx,
                      "ata.knowledge.ask": ask_mod, "ata.knowledge.prep": prep_mod}.items():
        monkeypatch.setitem(sys.modules, name, mod)
        parent, _, leaf = name.rpartition(".")
        monkeypatch.setattr(importlib.import_module(parent), leaf, mod, raising=False)
    monkeypatch.setattr(ata, "recorder", rec, raising=False)
    return f


def run_cmd(module, argv, config) -> int:
    """Roda um subcomando da parte E sem passar por ata.cli (as outras partes ainda podem não existir)."""
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command")
    module.add_parser(sub)
    args = p.parse_args(argv)
    return int(args.func(args, config) or 0)


@pytest.fixture
def no_parts(monkeypatch):
    """Simula A/B/D ausentes (import falha), para testar a degradação."""
    for name in ("ata.recorder", "ata.pipeline.run", "ata.knowledge.index", "ata.knowledge.ask",
                 "ata.knowledge.prep"):
        monkeypatch.setitem(sys.modules, name, None)
        parent, _, leaf = name.rpartition(".")
        pmod = importlib.import_module(parent)
        if hasattr(pmod, leaf):
            monkeypatch.delattr(pmod, leaf)
