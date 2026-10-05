"""Exportação dos turnos de um bundle: srt, vtt, txt, json, md, csv (nomes de speakers.json aplicados)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .. import bundle
from ..note import fmt_ts
from ..types import Turn

FORMATS = ("srt", "vtt", "txt", "json", "md", "csv")
CUE_MAX_S = 7.0
LINE_CHARS = 42
CUE_LINES = 2


def _clock(t: float, sep: str) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _wrap(text: str, width: int = LINE_CHARS) -> list[str]:
    lines: list[str] = []
    cur = ""
    for tok in text.split():
        if cur and len(cur) + 1 + len(tok) > width:
            lines.append(cur)
            cur = tok
        else:
            cur = f"{cur} {tok}".strip()
    if cur:
        lines.append(cur)
    return lines


def cues(turns: list[Turn], names: dict[str, str]) -> list[tuple[float, float, str]]:
    """Legendas: cada turno quebrado em blocos de até 7 s e 2 linhas × 42 caracteres (com o falante)."""
    out: list[tuple[float, float, str]] = []
    for t in sorted(turns, key=lambda t: t.start):
        who = names.get(t.speaker, t.speaker)
        toks = t.text.split()
        if not toks:
            continue
        # tempos por token: das palavras quando batem 1:1, senão interpolados no turno
        if len(t.words) == len(toks):
            times = [(w.start, w.end) for w in t.words]
        else:
            step = max(1e-3, (t.end - t.start) / len(toks))
            times = [(t.start + i * step, t.start + (i + 1) * step) for i in range(len(toks))]
        chunk: list[str] = []
        c0 = times[0][0]
        c1 = c0
        for tok, (a, b) in zip(toks, times):
            candidate = " ".join([*chunk, tok])
            too_long = len(_wrap(f"{who}: {candidate}")) > CUE_LINES
            if chunk and (b - c0 > CUE_MAX_S or too_long):
                out.append((c0, c1, f"{who}: {' '.join(chunk)}"))
                chunk, c0 = [], a
            chunk.append(tok)
            c1 = max(c1, b)
        if chunk:
            out.append((c0, max(c1, c0 + 0.2), f"{who}: {' '.join(chunk)}"))
    return out


def to_srt(turns: list[Turn], names: dict[str, str]) -> str:
    blocks = []
    for i, (a, b, text) in enumerate(cues(turns, names), 1):
        blocks.append(f"{i}\n{_clock(a, ',')} --> {_clock(b, ',')}\n" + "\n".join(_wrap(text)) + "\n")
    return "\n".join(blocks)


def to_vtt(turns: list[Turn], names: dict[str, str]) -> str:
    blocks = ["WEBVTT\n"]
    for a, b, text in cues(turns, names):
        blocks.append(f"{_clock(a, '.')} --> {_clock(b, '.')}\n" + "\n".join(_wrap(text)) + "\n")
    return "\n".join(blocks)


def to_txt(turns: list[Turn], names: dict[str, str]) -> str:
    return "".join(f"[{fmt_ts(t.start)}] {names.get(t.speaker, t.speaker)}: {t.text}\n"
                   for t in sorted(turns, key=lambda t: t.start))


def to_json(turns: list[Turn], names: dict[str, str]) -> str:
    rows = []
    for t in sorted(turns, key=lambda t: t.start):
        d = t.to_json()
        d["label"] = t.speaker
        d["speaker"] = names.get(t.speaker, t.speaker)
        rows.append(d)
    return json.dumps({"turns": rows}, ensure_ascii=False, indent=2) + "\n"


def to_csv(turns: list[Turn], names: dict[str, str]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["start", "end", "speaker", "label", "track", "text"])
    for t in sorted(turns, key=lambda t: t.start):
        w.writerow([f"{t.start:.3f}", f"{t.end:.3f}", names.get(t.speaker, t.speaker), t.speaker, t.track, t.text])
    return buf.getvalue()


def export(bundle_dir: Path, fmt: str) -> str:
    """Conteúdo exportado como texto. ``md`` = a nota (re)renderizada."""
    if fmt not in FORMATS:
        raise ValueError(f"formato inválido: {fmt} (use {', '.join(FORMATS)})")
    bdir = Path(bundle_dir)
    if fmt == "md":
        note = bdir / "note.md"
        if note.is_file():
            return note.read_text(encoding="utf-8")
        raise bundle.BundleError(f"{bdir.name}: sem note.md; rode `ata process` ou `ata rerender`")
    if not (bdir / "turns.json").is_file():
        raise bundle.BundleError(f"{bdir.name}: ainda não processado (sem turns.json); rode `ata process`")
    turns = bundle.read_turns(bdir)
    names = bundle.read_speaker_names(bdir)
    return {"srt": to_srt, "vtt": to_vtt, "txt": to_txt, "json": to_json, "csv": to_csv}[fmt](turns, names)


__all__ = ["FORMATS", "cues", "export", "to_csv", "to_json", "to_srt", "to_txt", "to_vtt"]
