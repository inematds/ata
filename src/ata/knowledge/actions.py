"""Ações e decisões entre reuniões, lidas dos ``summary.json`` dos bundles.

Cada item tem um id estável (hash curto de reunião + tipo + texto), então reprocessar a reunião sem mudar o
texto mantém o id. O estado "feito" fica num checklist em ``<cache>/actions.json``
(``{"done": {"<id>": "<quando>"}}``), nunca dentro do bundle.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import bundle
from ..config import Config
from .index import fold, note_path_for, parse_date
from .summary import fmt_ts

KINDS = {"action": "actions", "decision": "decisions", "question": "questions"}


def state_path(config: Config) -> Path:
    return config.cache / "actions.json"


def load_state(config: Config) -> dict[str, Any]:
    p = state_path(config)
    if not p.is_file():
        return {"done": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"done": {}}
    d.setdefault("done", {})
    return d


def save_state(config: Config, state: dict[str, Any]) -> None:
    p = state_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_json(p, state)


def item_id(meeting: str, kind: str, text: str) -> str:
    return hashlib.sha1(f"{meeting}\0{kind}\0{' '.join(text.split())}".encode()).hexdigest()[:8]


def items_for_bundle(bundle_dir: Path, config: Config, kinds: tuple[str, ...] = ("action", "decision",
                                                                                  "question")) -> list[dict]:
    summary = bundle.read_summary(bundle_dir)
    if not summary:
        return []
    meta = bundle.read_meta(bundle_dir)
    names = bundle.read_speaker_names(bundle_dir)
    note = note_path_for(bundle_dir, config)
    out = []
    for kind in kinds:
        for item in summary.get(KINDS[kind]) or []:
            text = " ".join(str(item.get("text") or "").split())
            if not text:
                continue
            ev = (item.get("evidence") or [{}])[0] or {}
            t = float(ev.get("t") or 0.0)
            speaker = names.get(str(ev.get("speaker") or ""), str(ev.get("speaker") or ""))
            owner = item.get("owner")
            owner = names.get(str(owner), str(owner)) if owner else None
            out.append({
                "id": item_id(meta.name, kind, text), "kind": kind, "text": text, "owner": owner,
                "due": item.get("due"), "meeting": meta.name, "title": meta.title or meta.name,
                "date": meta.created_at[:10], "t": t, "ts": fmt_ts(t), "speaker": speaker,
                "note_path": str(note) if note else None, "bundle": str(bundle_dir),
            })
    return out


def list_items(config: Config, *, kind: str = "action", owner: str | None = None, since: str | None = None,
               until: str | None = None, status: str = "open", meeting: str | None = None) -> list[dict]:
    """Itens entre reuniões, mais recentes primeiro. ``status``: open | done | all (decisões/perguntas
    também podem ser marcadas). ``owner`` casa sem acento e por substring (ação: dono; outros: falante)."""
    if status not in ("open", "done", "all"):
        raise ValueError("status deve ser open, done ou all")
    since_d, until_d = parse_date(since), parse_date(until)
    done = load_state(config)["done"]
    out = []
    for bdir in bundle.list_bundles(config.recordings):
        if meeting and bdir.name != meeting and not bdir.name.startswith(meeting):
            continue
        try:
            items = items_for_bundle(bdir, config, (kind,))
        except Exception:
            continue
        for it in items:
            if since_d and it["date"] < since_d:
                continue
            if until_d and it["date"] > until_d:
                continue
            who = it["owner"] if kind == "action" else it["speaker"]
            if owner and fold(owner) not in fold(who or ""):
                continue
            it["status"] = "done" if it["id"] in done else "open"
            if status != "all" and it["status"] != status:
                continue
            out.append(it)
    return out


def set_done(config: Config, item: str, done: bool = True) -> str | None:
    """Marca (ou desmarca) um item pelo id (aceita prefixo único). Devolve o id completo ou None."""
    candidates = {it["id"] for k in KINDS for it in list_items(config, kind=k, status="all")}
    match = sorted(c for c in candidates if c.startswith(item.strip()))
    if len(match) != 1:
        return None
    state = load_state(config)
    if done:
        state["done"][match[0]] = datetime.now().astimezone().isoformat(timespec="seconds")
    else:
        state["done"].pop(match[0], None)
    save_state(config, state)
    return match[0]


def format_item(it: dict[str, Any]) -> str:
    box = "[x]" if it.get("status") == "done" else "[ ]"
    extra = ""
    if it["kind"] == "action":
        if it.get("owner"):
            extra += f" — {it['owner']}"
        if it.get("due"):
            extra += f" ({it['due']})"
    return f"- {box} {it['text']}{extra} [{it['title']}, {it['ts']}] {{{it['id']}}}"
