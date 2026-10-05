"""Reuniões sintéticas já "processadas" (turns/words/summary/note/done) sem depender do pipeline (parte B)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from ata import bundle, i18n
from ata.engines.fake import write_fixture
from ata.testing import DEFAULT_LINES_PT, Line, synth_meeting
from ata.types import Span, Turn, Word

LINES_EN = [
    Line("Eu", "Good morning everyone let us start with the launch agenda"),
    Line("A", "I have the numbers from the closed test with one hundred people"),
    Line("B", "We decided to launch on the twelfth with two hundred users"),
    Line("A", "I will fix the tablet bug by Wednesday"),
    Line("Eu", "Do we charge for the premium plan during the beta?"),
]


def _turns_from_fixtures(bdir: Path, language: str) -> list[Turn]:
    far_words = [Word.from_json(w) for w in json.loads((bdir / "far.wav.fake-words.json").read_text())]
    far_spans = [Span.from_json(s) for s in json.loads((bdir / "far.wav.fake-spans.json").read_text())]
    mic_words = [Word.from_json(w) for w in json.loads((bdir / "mic.wav.fake-words.json").read_text())]
    labels = {f"S{i}": i18n.other_label(language, i + 2) for i in range(8)}
    turns: list[Turn] = []
    for s in far_spans:
        if s.speaker not in labels:
            continue
        ws =[w for w in far_words if s.start - 0.01 <= w.start < s.end]
        if ws:
            turns.append(Turn(s.start, s.end, labels[s.speaker], " ".join(w.text for w in ws), "far", ws))
    group: list[Word] = []
    for w in mic_words + [None]:
        if group and (w is None or w.start - group[-1].end > 0.3):
            turns.append(Turn(group[0].start, group[-1].end, i18n.me_label(language),
                              " ".join(x.text for x in group), "mic", list(group)))
            group = []
        if w is not None:
            group.append(w)
    return sorted(turns, key=lambda t: t.start)


def make_meeting(config, lines=None, *, title="lançamento beta", language="pt-BR", started=None,
                 names: dict[str, str] | None = None, summary: dict | None | bool = True,
                 voices: dict[str, str] | None = None, mic_offset: float = 0.0) -> Path:
    """Bundle completo: áudio sintético + turns/words/summary/note/done (+ cópia da nota em paths.notes).

    ``summary=True`` usa o resumo fake via ``summarize_turns``; dict = esse resumo; False/None = sem resumo.
    ``voices`` grava a fixture do far com {rótulo do diarizador: voz} para o embedder fake."""
    from ata.knowledge.summary import summarize_turns, transcript_lines

    lines = lines or (DEFAULT_LINES_PT if language == "pt-BR" else LINES_EN)
    bdir = synth_meeting(config.recordings, lines, title=title, language=language, bleed_db=None,
                         started=started)
    if voices:
        # o embedder fake escolhe o vetor pelo rótulo recebido: gravamos a voz tanto no rótulo do diarizador
        # ("S0", usado por auto_label) quanto no rótulo final ("Pessoa 2", usado por enroll)
        spans = [Span.from_json(s) for s in json.loads((bdir / "far.wav.fake-spans.json").read_text())]
        final = {k: i18n.other_label(language, int(k[1:]) + 2) for k in voices}
        extra = [Span(s.start, s.end, final[s.speaker]) for s in spans if s.speaker in final]
        write_fixture(bdir / "far.wav", spans=spans + extra,
                      voices={**voices, **{final[k]: v for k, v in voices.items()}})
    turns = _turns_from_fixtures(bdir, language)
    if mic_offset:
        meta = bundle.read_meta(bdir)
        tracks = dict(meta.tracks)
        far = tracks["far"]
        tracks["mic"] = bundle.Track(**{**tracks["mic"].to_json(), "start_epoch": far.start_epoch + mic_offset})
        bundle.write_meta(bdir, meta.with_(tracks=tracks))
    bundle.write_words(bdir, {"far": [w for t in turns if t.track == "far" for w in t.words],
                              "mic": [w for t in turns if t.track == "mic" for w in t.words]})
    bundle.write_turns(bdir, turns)
    if names:
        bundle.write_speaker_names(bdir, names)
    if summary is True:
        summary = summarize_turns(turns, language, config)
    if summary:
        bundle.write_json(bdir / "summary.json", summary)
    note = "\n".join(["---", f"date: {bdir.name[:10]}", f"language: {language}", f'bundle: "{bdir}"',
                      "tags: [reuniao]", "---", "", f"# {title}", "", "## Transcrição", ""]
                     + transcript_lines(turns)) + "\n"
    bundle.write_text(bdir / "note.md", note)
    config.notes.mkdir(parents=True, exist_ok=True)
    bundle.write_text(config.notes / f"{bdir.name}.md", note)
    bundle.write_json(bdir / "done.json", {"ok": True, "at": datetime.now().astimezone().isoformat()})
    return bdir


def run_cmd(module, argv: list[str], config) -> int:
    """Roda um comando do módulo sem passar por ata.cli (que importa módulos de outras partes)."""
    parser = argparse.ArgumentParser(prog="ata")
    sub = parser.add_subparsers(dest="command")
    module.add_parser(sub)
    args = parser.parse_args(argv)
    return int(args.func(args, config) or 0)
