"""Nota Markdown da reunião, no idioma do bundle (Obsidian-friendly).

Frontmatter YAML (date, start, duration, language, speakers, participants, bundle, engines, tags), H1
"<Reunião> <título ou data>", seções traduzidas por ``i18n.TEXTS`` e a transcrição em linhas
``[mm:ss] Falante: texto`` (``[h:mm:ss]`` a partir de 1 h). Nomes de ``speakers.json`` aplicados na hora de
renderizar: turnos e resumo guardam os rótulos (``Pessoa 2``), a nota mostra o nome.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import i18n
from .bundle import BundleMeta
from .types import Turn

TAGS = {"pt-BR": "reuniao", "en": "meeting", "es": "reunion"}

DAMAGE_TEXT: dict[str, dict[str, str]] = {
    "pt-BR": {
        "far_missing": "faixa das outras pessoas (far) ausente", "mic_missing": "faixa do microfone ausente",
        "far_silent": "faixa das outras pessoas em silêncio", "mic_silent": "microfone em silêncio",
        "start_unmeasured": "início das faixas estimado, não medido", "length_drift": "faixas com durações diferentes",
        "start_separation": "faixas começaram com muita diferença", "recorder_killed": "gravador interrompido",
        "dropped_frames": "áudio perdido durante a gravação",
    },
    "en": {
        "far_missing": "far-end track missing", "mic_missing": "microphone track missing",
        "far_silent": "far-end track silent", "mic_silent": "microphone silent",
        "start_unmeasured": "track start estimated, not measured", "length_drift": "track lengths differ",
        "start_separation": "tracks started far apart", "recorder_killed": "recorder was killed",
        "dropped_frames": "audio dropped during recording",
    },
    "es": {
        "far_missing": "falta la pista de los demás (far)", "mic_missing": "falta la pista del micrófono",
        "far_silent": "pista de los demás en silencio", "mic_silent": "micrófono en silencio",
        "start_unmeasured": "inicio de las pistas estimado, no medido",
        "length_drift": "pistas con duraciones distintas", "start_separation": "pistas empezaron muy separadas",
        "recorder_killed": "grabador interrumpido", "dropped_frames": "audio perdido durante la grabación",
    },
}


def fmt_ts(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def fmt_duration(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _yaml_str(v: Any) -> str:
    s = "" if v is None else str(v)
    return json.dumps(s, ensure_ascii=False)   # JSON string é YAML válido e sempre seguro


def _lang(language: str) -> str:
    lang = i18n.normalize(language) if language else i18n.DEFAULT_LANGUAGE
    return i18n.DEFAULT_LANGUAGE if lang == "auto" else lang


def _started(meta: BundleMeta) -> datetime | None:
    try:
        return datetime.fromisoformat(meta.created_at)
    except (TypeError, ValueError):
        return None


def meeting_duration(meta: BundleMeta, turns: list[Turn]) -> float:
    secs = [t.seconds for t in meta.tracks.values() if t.seconds is not None]
    if secs:
        return max(secs)
    return max((t.end for t in turns), default=0.0)


def talk_times(turns: list[Turn]) -> list[tuple[str, float, str]]:
    """(rótulo, segundos, faixa) por ordem da primeira fala."""
    order: dict[str, list[Any]] = {}
    for t in sorted(turns, key=lambda t: t.start):
        row = order.setdefault(t.speaker, [0.0, t.track])
        row[0] += max(0.0, t.end - t.start)
    return [(k, v[0], v[1]) for k, v in order.items()]


def _name(label: str | None, names: dict[str, str]) -> str:
    if not label:
        return ""
    return names.get(label, label)


def _rename_in_text(text: str, names: dict[str, str]) -> str:
    for label in sorted(names, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(label)}(?!\w)", names[label], text)
    return text


def _evidence(items: Any) -> str:
    ts = []
    for e in items or []:
        if isinstance(e, dict) and isinstance(e.get("t"), (int, float)):
            ts.append(f"[{fmt_ts(float(e['t']))}]")
    return (" " + " ".join(ts)) if ts else ""


def render_note(meta: BundleMeta, turns: list[Turn], summary: dict[str, Any] | None, names: dict[str, str],
                my_notes: str | None, damage: list[str], language: str, *,
                bundle_path: Path | str | None = None) -> str:
    lang = _lang(language)
    T = i18n.TEXTS[lang]
    names = dict(names or {})
    started = _started(meta)
    duration = meeting_duration(meta, turns)
    talk = talk_times(turns)
    participants = [_name(label, names) for label, _, _ in talk]
    bundle_ref = str(bundle_path) if bundle_path is not None else meta.name

    fm = ["---"]
    fm.append(f"date: {started.date().isoformat() if started else _yaml_str(meta.created_at[:10])}")
    fm.append(f"start: {_yaml_str(started.strftime('%H:%M') if started else '')}")
    fm.append(f"duration: {_yaml_str(fmt_duration(duration))}")
    fm.append(f"language: {_yaml_str(lang)}")
    fm.append(f"speakers: {len(participants)}")
    if participants:
        fm.append("participants:")
        fm.extend(f"  - {_yaml_str(p)}" for p in participants)
    else:
        fm.append("participants: []")
    fm.append(f"bundle: {_yaml_str(bundle_ref)}")
    if meta.engines:
        fm.append("engines:")
        fm.extend(f"  {k}: {_yaml_str(v)}" for k, v in sorted(meta.engines.items()))
    else:
        fm.append("engines: {}")
    fm.append(f"tags: [{TAGS[lang]}]")
    fm.append("---")

    when = started.strftime("%Y-%m-%d %H:%M") if started else meta.name
    out = ["\n".join(fm), "", f"# {T['meeting']} {meta.title or when}", ""]

    s = summary if isinstance(summary, dict) else None
    out += [f"## {T['summary']}", ""]
    tldr = (s or {}).get("tldr")
    out += [_rename_in_text(str(tldr), names) if tldr else T["no_summary"], ""]

    if s:
        topics = [x for x in s.get("topics") or [] if isinstance(x, dict)]
        if topics:
            out += [f"## {T['topics']}", ""]
            for tp in topics:
                ts = f" [{fmt_ts(float(tp['start']))}]" if isinstance(tp.get("start"), (int, float)) else ""
                body = f" — {_rename_in_text(str(tp['summary']), names)}" if tp.get("summary") else ""
                out.append(f"- **{_rename_in_text(str(tp.get('title', '')), names)}**{ts}{body}")
            out.append("")
        for key, field in (("decisions", "decisions"), ("actions", "actions"), ("questions", "questions")):
            items = [x for x in s.get(field) or [] if isinstance(x, dict) and x.get("text")]
            if not items:
                continue
            out += [f"## {T[key]}", ""]
            for it in items:
                line = f"- {_rename_in_text(str(it['text']), names)}"
                if key == "actions":
                    extra = []
                    if it.get("owner"):
                        extra.append(f"{T['owner']}: {_name(str(it['owner']), names)}")
                    if it.get("due"):
                        extra.append(f"{T['due']}: {it['due']}")
                    if extra:
                        line += f" ({'; '.join(extra)})"
                    line = "- [ ] " + line[2:]
                out.append(line + _evidence(it.get("evidence")))
            out.append("")

    out += [f"## {T['participants']}", ""]
    if talk:
        for label, secs, _track in talk:
            shown = _name(label, names)
            alias = f" ({label})" if shown != label else ""
            out.append(f"- {shown}{alias} — {T['talk_time']} {fmt_duration(secs)}")
    else:
        out.append("-")
    out.append("")

    if my_notes and my_notes.strip():
        out += [f"## {T['my_notes']}", "", my_notes.strip(), ""]

    if damage:
        texts = DAMAGE_TEXT[lang]
        out += [f"## {T['problems']}", "", T["damaged"], ""]
        out += [f"- `{r}` — {texts.get(r, r)}" for r in damage]
        out.append("")

    out += [f"## {T['transcript']}", ""]
    for t in sorted(turns, key=lambda t: t.start):
        if t.text.strip():
            out.append(f"[{fmt_ts(t.start)}] {_name(t.speaker, names)}: {t.text.strip()}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"
