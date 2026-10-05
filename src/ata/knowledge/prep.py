"""Cola pré-reunião: ``build_prep`` junta, das reuniões que casam com a consulta (título, participantes, texto),
as ações em aberto, as decisões, as perguntas em aberto e um roteiro de 3 a 5 itens ``- [ ] (qN) ...``.
Grava em ``<notes>/<AAAA-MM-DD>-<slug>-preparacao.md``.

``tick_prep`` roda depois da reunião (gancho de ``ata.connect.after_note``): marca ``[x]`` nos itens do
roteiro respondidos pelas decisões/ações da reunião nova (tokens do item presentes no texto ÷ tokens do item
≥ 0,5, sem acento e sem palavras vazias). Só olha colas datadas de até 7 dias antes da reunião.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .. import bundle, i18n
from ..config import Config
from . import actions as actions_mod
from . import index
from .summary import fmt_ts

log = logging.getLogger("ata.knowledge.prep")

TICK_WINDOW_DAYS = 7
TICK_THRESHOLD = 0.5
MAX_MEETINGS = 5
SCRIPT_MIN, SCRIPT_MAX = 3, 5

_PREFIX = {
    "pt-BR": {"question": "Retomar", "action": "Status", "decision": "Confirmar"},
    "en": {"question": "Follow up", "action": "Status", "decision": "Confirm"},
    "es": {"question": "Retomar", "action": "Estado", "decision": "Confirmar"},
}
_GENERIC = {
    "pt-BR": ["Qual é o objetivo desta reunião?", "Quais são os próximos passos e quem é responsável por cada um?",
              "Quais prazos precisam ficar definidos hoje?", "Existe algum bloqueio que precisa de ajuda?",
              "O que precisa ser comunicado para quem não está aqui?"],
    "en": ["What is the goal of this meeting?", "What are the next steps and who owns each one?",
           "Which deadlines must be set today?", "Is there any blocker that needs help?",
           "What must be communicated to people who are not here?"],
    "es": ["¿Cuál es el objetivo de esta reunión?", "¿Cuáles son los próximos pasos y quién es responsable de cada uno?",
           "¿Qué plazos deben quedar definidos hoy?", "¿Hay algún bloqueo que necesite ayuda?",
           "¿Qué hay que comunicar a quienes no están aquí?"],
}
_LOCAL = {
    "pt-BR": {"none": "Nenhuma reunião dos últimos {days} dias casa com a consulta.", "calendar": "Agenda",
              "empty": "(nada)"},
    "en": {"none": "No meeting from the last {days} days matches the query.", "calendar": "Calendar",
           "empty": "(none)"},
    "es": {"none": "Ninguna reunión de los últimos {days} días coincide con la consulta.", "calendar": "Agenda",
           "empty": "(nada)"},
}

STOPWORDS = set("""
a o e os as um uma uns umas de do da dos das em no na nos nas por para pra com sem que se ao aos sobre
the an and or of to in on for with by at is are be it this that we you they i will
el la los las un una y o de del en con por para que se lo es son
eu voce ele ela nos vamos vou vai ser foi esta isso essa esse este
""".split())

_ITEM = re.compile(r"^(?P<pre>\s*- )\[ \](?P<rest> \((?P<q>q\d+)\) (?P<text>.+?))\s*$")
_DECOR = re.compile(r"^\*\*[^*]+\*\*\s*[—:-]\s*")


def _lang(config: Config) -> str:
    try:
        lang = i18n.normalize(config.get("language.default"))
    except ValueError:
        return i18n.DEFAULT_LANGUAGE
    return i18n.DEFAULT_LANGUAGE if lang == "auto" else lang


def content_tokens(text: str) -> set[str]:
    return {t for t in index.tokens(text) if len(t) >= 3 and t not in STOPWORDS}


def overlap(item: str, answer: str) -> float:
    it = content_tokens(_DECOR.sub("", item))
    if not it:
        return 0.0
    return len(it & content_tokens(answer)) / len(it)


def prep_path(config: Config, query: str, day: date | None = None) -> Path:
    day = day or date.today()
    slug = bundle.slugify(query) or "reuniao"
    return config.notes / f"{day.isoformat()}-{slug}-preparacao.md"


def matching_meetings(config: Config, query: str, days: int = 90, calendar: str | None = None) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    text = f"{query} {calendar or ''}"
    qtok = content_tokens(text)
    scores: dict[str, float] = {}
    rows = {m["id"]: m for m in index.meetings(config, since=since)}
    for mid, m in rows.items():
        hay = content_tokens(f"{m.get('title') or ''} {' '.join(m.get('participants') or [])}")
        hit = len(qtok & hay)
        if hit:
            scores[mid] = scores.get(mid, 0.0) + 2.0 * hit
    if qtok:
        try:
            for rank, h in enumerate(index.search(config, text, limit=20, filters={"since": since})):
                scores[h.meeting] = scores.get(h.meeting, 0.0) + 1.0 / (1 + rank)
        except Exception as exc:
            log.warning("prep: busca falhou (%s)", type(exc).__name__)
    found = [rows[m] for m in scores if m in rows]
    found.sort(key=lambda m: (scores[m["id"]], m.get("started_at") or ""), reverse=True)
    return found[:MAX_MEETINGS]


def _yaml(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_prep(config: Config, query: str, days: int = 90, calendar: str | None = None, *,
               dry_run: bool = False) -> str:
    """Markdown da cola (no idioma da config). Grava em ``prep_path`` salvo ``dry_run``."""
    lang = _lang(config)
    T = i18n.TEXTS[lang]
    L = _LOCAL[lang]
    found = matching_meetings(config, query, days, calendar)
    ids = [m["id"] for m in found]
    done = actions_mod.load_state(config)["done"]
    open_actions: list[dict] = []
    decisions: list[dict] = []
    questions: list[dict] = []
    for m in found:
        bdir = Path(m["bundle"]) if m.get("bundle") else config.recordings / m["id"]
        try:
            items = actions_mod.items_for_bundle(bdir, config)
        except Exception:
            continue
        for it in items:
            if it["id"] in done:
                continue
            {"action": open_actions, "decision": decisions, "question": questions}[it["kind"]].append(it)

    def cite(it: dict) -> str:
        return f"[{it['title']}, {it['ts']}]"

    script: list[str] = []
    P = _PREFIX[lang]
    for kind, pool in (("question", questions), ("action", open_actions), ("decision", decisions)):
        for it in pool:
            if len(script) >= SCRIPT_MAX:
                break
            extra = f" ({it['owner']})" if kind == "action" and it.get("owner") else ""
            script.append(f"**{P[kind]}** — {it['text']}{extra}")
    for g in _GENERIC[lang]:
        if len(script) >= SCRIPT_MIN:
            break
        script.append(g)

    today = date.today().isoformat()
    lines = ["---", "tipo: preparacao", f"consulta: {_yaml(query)}", f"data: {today}",
             f"idioma: {lang}", "reunioes: [" + ", ".join(_yaml(i) for i in ids) + "]", "ata: prep", "---", "",
             f"# {T['prep']}: {query}", ""]
    if calendar:
        lines += [f"> {L['calendar']}: {' '.join(calendar.split())}", ""]
    lines += [f"## {T['notes_used']}", ""]
    if found:
        for m in found:
            ref = f"[{m['title']}]({m['note_path']})" if m.get("note_path") else m["title"]
            lines.append(f"- {ref} — {m['date']}")
    else:
        lines.append(L["none"].format(days=days))
    lines.append("")
    sections = ((T["open_actions"], open_actions, True), (T["decided_before"], decisions, False),
                (T["questions"], questions, False))
    for head, pool, box in sections:
        lines += [f"## {head}", ""]
        if not pool:
            lines.append(L["empty"])
        for it in pool:
            owner = f" — {it['owner']}" if it.get("owner") else ""
            due = f" ({it['due']})" if it.get("due") else ""
            lead = "- [ ] " if box else "- "
            lines.append(f"{lead}{it['text']}{owner}{due} {cite(it)}")
        lines.append("")
    lines += [f"## {T['script']}", ""]
    lines += [f"- [ ] (q{i}) {s}" for i, s in enumerate(script, start=1)]
    md = "\n".join(lines) + "\n"
    if not dry_run:
        p = prep_path(config, query)
        p.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text(p, md)
    return md


def _prep_files(config: Config, meeting_day: date) -> list[Path]:
    try:
        notes = config.notes
    except Exception:
        return []
    if not notes.is_dir():
        return []
    out = []
    for p in sorted(notes.glob("*-preparacao.md")):
        try:
            d = date.fromisoformat(p.name[:10])
        except ValueError:
            continue
        if meeting_day - timedelta(days=TICK_WINDOW_DAYS) <= d <= meeting_day:
            out.append(p)
    return out


def tick_prep(config: Config, bundle_dir: Path | str) -> int:
    """Marca os itens ``(qN)`` respondidos pela reunião ``bundle_dir``. Devolve quantos marcou."""
    bdir = Path(bundle_dir)
    summary = bundle.read_summary(bdir)
    if not summary:
        return 0
    meta = bundle.read_meta(bdir)
    try:
        day = date.fromisoformat(meta.created_at[:10])
    except ValueError:
        day = date.today()
    answers: list[tuple[str, float]] = []
    for key in ("decisions", "actions"):
        for item in summary.get(key) or []:
            text = str(item.get("text") or "")
            ev = (item.get("evidence") or [{}])[0] or {}
            if text.strip():
                answers.append((text, float(ev.get("t") or 0.0)))
    if not answers:
        return 0
    title = meta.title or meta.name
    ticked = 0
    for p in _prep_files(config, day):
        lines = p.read_text(encoding="utf-8").splitlines()
        changed = False
        for i, line in enumerate(lines):
            m = _ITEM.match(line)
            if not m:
                continue
            best = max(answers, key=lambda a: overlap(m["text"], a[0]))
            if overlap(m["text"], best[0]) >= TICK_THRESHOLD:
                lines[i] = f"{m['pre']}[x]{m['rest']} ✓ [{title}, {fmt_ts(best[1])}]"
                ticked += 1
                changed = True
        if changed:
            bundle.write_text(p, "\n".join(lines) + "\n")
    return ticked


def prep_items(path: Path) -> list[dict[str, Any]]:
    """Itens ``(qN)`` de uma cola (para o modo ao vivo/MCP): [{q, text, done}]."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*- \[( |x)\] \((q\d+)\) (.+)$", line)
        if m:
            out.append({"q": m.group(2), "text": _DECOR.sub("", m.group(3)), "done": m.group(1) == "x"})
    return out
