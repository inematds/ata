"""Pipeline offline de um bundle: áudio + meta -> palavras, turnos, resumo, nota.

Ordem: meta (+compat) -> ASR por faixa (motor do idioma; ``asr.fallback`` se der ``EngineError``) -> offsets
(ÚNICO ponto que converte tempo do arquivo em relógio do bundle) -> gate -> diarização do far (+ mic quando
``diarization.mic_speakers > 1``) -> rótulos (Eu / Pessoa N) -> voiceprints (hook) -> turnos -> limpeza ->
words.json, turns.json -> resumo (hook) -> note.md (bundle + ``paths.notes``) -> done.json -> índice e conexões
(hooks). ``pipeline.log`` só tem contagens, tempos e nomes de motor; nunca texto de reunião.
"""

from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .. import audio, bundle, i18n
from ..bundle import BundleError, BundleMeta
from ..config import Config
from ..engines import registry
from ..engines.base import EngineError
from ..note import render_note
from ..types import TRACKS, Span, Turn, Word
from . import cleanup, gate, hooks
from .turns import MIC_PREFIX, build_turns, first_appearance_labels, speaker_stats

MIN_TRACK_SECONDS = 0.1
MY_NOTES_MAX = 1 << 20
IMPORT_RECORDER = "ata-import"


class PipelineLog:
    """Linhas ``<iso> etapa chave=valor`` em pipeline.log (append) + eco opcional para o usuário."""

    def __init__(self, path: Path | None, echo: Callable[[str], None] | None = None) -> None:
        self.path = path
        self.echo = echo
        self.t0 = time.monotonic()

    def __call__(self, stage: str, **fields: Any) -> None:
        parts = [datetime.now().astimezone().isoformat(timespec="seconds"), stage]
        parts += [f"{k}={v}" for k, v in fields.items()]
        parts.append(f"t={time.monotonic() - self.t0:.2f}s")
        line = " ".join(str(p) for p in parts)
        if self.path is not None:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        if self.echo is not None:
            self.echo(line)


# ---- idioma -------------------------------------------------------------------------------------------------

def detect_language(engine: Any, wav: Path) -> str | None:
    """LID do lado do motor: se o motor ASR tiver ``detect_language(wav) -> str|None``, usa; senão None."""
    fn = getattr(engine, "detect_language", None)
    if not callable(fn):
        return None
    try:
        found = fn(wav)
        return i18n.normalize(found) if found else None
    except (EngineError, ValueError):
        return None


def resolve_language(meta: BundleMeta, config: Config, requested: str | None) -> str:
    """Idioma pedido (arg > meta) normalizado; 'auto' fica 'auto' para o ASR decidir depois."""
    lang = i18n.normalize(requested) if requested else i18n.normalize(meta.language_requested or None)
    if lang == "auto" and meta.language_detected:
        return i18n.normalize(meta.language_detected)
    return lang


# ---- compat -------------------------------------------------------------------------------------------------

def migrate_compat(bundle_dir: Path, config: Config, meta: BundleMeta | None = None) -> Path:
    """Copia um bundle de outro formato (ex.: crunchlog/2) para um bundle ata/1 novo em ``paths.recordings``.

    O original nunca é tocado. Se o destino já existe como ata/1, reaproveita."""
    meta = meta or bundle.read_meta(bundle_dir)
    root = config.recordings
    root.mkdir(parents=True, exist_ok=True)
    dest = root / meta.name
    if (dest / bundle.META_FILE).is_file():
        try:
            if bundle.read_meta(dest).source_schema == bundle.SCHEMA:
                return dest
        except BundleError:
            pass
        dest = root / f"{meta.name}-ata"
    dest.mkdir(parents=True, exist_ok=True)
    for tr in meta.tracks.values():
        src = Path(bundle_dir) / tr.file
        if src.is_file():
            shutil.copy2(src, dest / tr.file)
    for extra in ("my-notes.md",):
        if (Path(bundle_dir) / extra).is_file():
            shutil.copy2(Path(bundle_dir) / extra, dest / extra)
    recorder = dict(meta.recorder)
    recorder["migrated_from"] = meta.source_schema
    bundle.write_meta(dest, meta.with_(recorder=recorder))
    return dest


# ---- faixas -------------------------------------------------------------------------------------------------

def _load_track(bdir: Path, meta: BundleMeta, name: str, writable: bool) -> tuple[Path | None, np.ndarray | None]:
    tr = meta.tracks.get(name)
    if tr is None:
        return None, None
    p = bdir / tr.file
    if not p.is_file() or p.stat().st_size <= 44:
        return None, None
    if writable:
        try:
            audio.repair_header(p)
        except OSError:
            pass
    try:
        x = audio.read_wav(p)
    except ValueError:
        try:
            x = audio.read_any_wav(p)
        except (ValueError, EOFError, OSError):
            return None, None
    except (EOFError, OSError):
        return None, None
    if len(x) < MIN_TRACK_SECONDS * audio.SAMPLE_RATE:
        return None, None
    return p, x


def _engine_label(engine: Any, ref: str) -> str:
    name = str(getattr(engine, "name", "?"))
    eng, model = registry.split_ref(ref)
    return f"{name}:{model}" if model and eng == name else name


def _transcribe(wav: Path, language: str, config: Config, plog: PipelineLog, track: str,
                engines: dict[str, str]) -> list[Word]:
    ref = registry.asr_ref(config, language)
    try:
        engine = registry.asr_for(config, language)
        words = engine.transcribe(wav, language)
        engines["asr"] = _engine_label(engine, ref)
    except EngineError as exc:
        plog("asr_fallback", track=track, error=type(exc).__name__)
        fb_ref = str(config.get("asr.fallback"))
        engine = registry.asr_fallback(config)   # se falhar também, sobe EngineError (sem texto de reunião)
        words = engine.transcribe(wav, language)
        engines["asr"] = _engine_label(engine, fb_ref)
        engines["asr_fallback"] = "yes"
    return list(words)


def _diarize(wav: Path, max_speakers: int, config: Config, plog: PipelineLog, track: str,
             engines: dict[str, str]) -> list[Span]:
    refs = [str(config.get("diarization.engine")), str(config.get("diarization.fallback") or "")]
    for i, ref in enumerate(r for r in refs if r):
        try:
            d = registry.build("diarizer", ref, config)
            spans = list(d.diarize(wav, max_speakers=max_speakers))
            engines["diarizer"] = _engine_label(d, ref)
            if i:
                engines["diarizer_fallback"] = "yes"
            return spans
        except EngineError as exc:
            plog("diarize_error", track=track, engine=registry.split_ref(ref)[0], error=type(exc).__name__)
    engines.setdefault("diarizer", "none")
    return []


def _shift_spans(spans: list[Span], offset: float) -> list[Span]:
    return [Span(s.start + offset, s.end + offset, s.speaker) for s in spans]


def _read_my_notes(bdir: Path) -> str | None:
    p = bdir / "my-notes.md"
    if not p.is_file():
        return None
    try:
        return p.read_bytes()[:MY_NOTES_MAX].decode("utf-8", "replace")
    except OSError:
        return None


def effective_damage(meta: BundleMeta, bdir: Path, unreadable: list[str] = ()) -> list[str]:
    damage = bundle.damage_report(meta, bdir)
    if meta.recorder.get("name") == IMPORT_RECORDER:
        damage = [r for r in damage if r != "mic_missing"]   # import é só far por definição
    for name in unreadable:
        r = f"{name}_missing"
        if r not in damage and not (r == "mic_missing" and meta.recorder.get("name") == IMPORT_RECORDER):
            damage.append(r)
    return damage


def note_paths(bdir: Path, config: Config) -> tuple[Path, Path]:
    return bdir / "note.md", config.notes / f"{bdir.name}.md"


def write_note(bdir: Path, config: Config, text: str) -> Path:
    local, shared = note_paths(bdir, config)
    bundle.write_text(local, text)
    shared.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text(shared, text)
    return shared


def glossary_for(config: Config) -> cleanup.Glossary:
    try:
        return cleanup.load_glossary(config.get("cleanup.glossary"))
    except (OSError, UnicodeDecodeError):
        return []


def extra_fillers(config: Config, language: str) -> list[str]:
    extra = list(config.get("cleanup.extra_fillers") or [])
    per_lang = config.get("cleanup.fillers")
    if isinstance(per_lang, dict):
        extra += list(per_lang.get(language) or [])
    return [str(x) for x in extra]


def clean_turns(turns: list[Turn], language: str, config: Config) -> list[Turn]:
    gl = glossary_for(config)
    extra = extra_fillers(config, language)
    out = []
    for t in turns:
        text = cleanup.clean_text(t.text, language, gl, extra_fillers=extra)
        if text:
            out.append(Turn(t.start, t.end, t.speaker, text, t.track, t.words))
    return out


# ---- principal ----------------------------------------------------------------------------------------------

def process_bundle(bundle_dir: Path | str, config: Config, *, language: str | None = None,
                   speakers: int | None = None, summarize: bool = True,
                   log: Callable[[str], None] | None = None) -> Path:
    """Processa o bundle e devolve o caminho da nota em ``paths.notes``."""
    t_start = time.monotonic()
    bdir = Path(bundle_dir).expanduser()
    meta = bundle.read_meta(bdir)
    if meta.source_schema != bundle.SCHEMA:
        bdir = migrate_compat(bdir, config, meta)
        meta = bundle.read_meta(bdir)
        if log:
            log(f"bundle convertido para {bundle.SCHEMA} em {bdir}")
    plog = PipelineLog(bdir / "pipeline.log", log)
    plog("start", schema=meta.source_schema, tracks=len(meta.tracks))

    engines: dict[str, str] = {}
    paths: dict[str, Path] = {}
    arrays: dict[str, np.ndarray] = {}
    unreadable: list[str] = []
    for name in TRACKS:
        p, x = _load_track(bdir, meta, name, writable=True)
        if p is not None and x is not None:
            paths[name], arrays[name] = p, x
        elif name in meta.tracks:
            unreadable.append(name)

    lang = resolve_language(meta, config, language)
    if lang == "auto":
        probe = paths.get("far") or paths.get("mic")
        detected = None
        if probe is not None:
            try:
                detected = detect_language(registry.asr_for(config, i18n.normalize(config.get("language.default"))),
                                           probe)
            except EngineError:
                detected = None
        lang = detected or i18n.normalize(config.get("language.default"))
        meta = meta.with_(language_detected=lang)
        plog("language", detected=bool(detected), language=lang)

    # ASR (tempo do arquivo) -> offsets (único ponto) -> relógio do bundle
    offsets = bundle.track_offsets(meta)
    words: dict[str, list[Word]] = {"far": [], "mic": []}
    for name, p in paths.items():
        t0 = time.monotonic()
        raw = _transcribe(p, lang, config, plog, name, engines)
        off = offsets.get(name, 0.0)
        words[name] = [w.shifted(off) for w in raw]
        plog("asr", track=name, engine=engines.get("asr"), words=len(raw), s=f"{time.monotonic() - t0:.2f}")

    # gate: tira do mic o eco do far
    gated = 0
    if config.get("gate.enabled", True) and words["mic"] and "far" in arrays and "mic" in arrays:
        kept, dropped = gate.gate_mic_words(words["mic"], arrays["far"], arrays["mic"],
                                            float(config.get("gate.margin_db", 3.0)),
                                            far_offset=offsets.get("far", 0.0), mic_offset=offsets.get("mic", 0.0))
        words["mic"], gated = kept, len(dropped)
        plog("gate", kept=len(kept), dropped=gated)

    # diarização
    hint = speakers if speakers else meta.speakers_hint
    far_spans: list[Span] = []
    if words["far"] and "far" in paths:
        if hint:
            max_far = max(1, hint - 1) if "mic" in paths else max(1, hint)
        else:
            max_far = int(config.get("diarization.max_speakers") or 0)
        far_spans = _shift_spans(_diarize(paths["far"], max_far, config, plog, "far", engines),
                                 offsets.get("far", 0.0))
        plog("diarize", track="far", spans=len(far_spans), max_speakers=max_far,
             speakers=len({s.speaker for s in far_spans}))
    mic_spans: list[Span] = []
    mic_n = int(config.get("diarization.mic_speakers") or 1)
    if mic_n > 1 and words["mic"] and "mic" in paths:
        mic_spans = _shift_spans(_diarize(paths["mic"], mic_n, config, plog, "mic", engines),
                                 offsets.get("mic", 0.0))
        plog("diarize", track="mic", spans=len(mic_spans), max_speakers=mic_n)

    # rótulos: far por ordem de aparição; no mic, quem mais fala é você, o resto continua a numeração
    labels = first_appearance_labels(far_spans, lang)
    if mic_spans:
        talk: dict[str, float] = {}
        for s in mic_spans:
            talk[s.speaker] = talk.get(s.speaker, 0.0) + s.duration
        main = max(talk, key=lambda k: talk[k])
        labels[MIC_PREFIX + main] = i18n.me_label(lang)
        others = [s for s in mic_spans if s.speaker != main]
        n_far = sum(1 for k in labels if not k.startswith(MIC_PREFIX))
        labels.update(first_appearance_labels(others, lang, first_n=2 + n_far, prefix=MIC_PREFIX))

    names = bundle.read_speaker_names(bdir)
    if config.get("voices.enabled", False):
        found = hooks.auto_label(bdir, config, {"far": far_spans, "mic": mic_spans}, labels)
        added = {k: v for k, v in found.items() if v and k not in names}
        if added:
            names.update(added)
            bundle.write_speaker_names(bdir, names)
        plog("voices", matched=len(added))

    turns = build_turns(words["far"], far_spans, words["mic"], labels, lang, mic_spans=mic_spans or None)
    turns = clean_turns(turns, lang, config)
    stats = speaker_stats(turns)
    plog("turns", turns=len(turns), speakers=len(stats))

    bundle.write_words(bdir, words)
    bundle.write_turns(bdir, turns, {"me_label": i18n.me_label(lang), "language": lang, "stats": stats,
                                     "labels": labels})

    my_notes = _read_my_notes(bdir)
    summary = None
    if summarize and turns:
        t0 = time.monotonic()
        summary = hooks.summarize(turns, lang, config, my_notes)
        if isinstance(summary, dict):
            bundle.write_json(bdir / "summary.json", summary)
            engines["summary"] = "fake" if registry.forced_fake() else str(config.get("summary.provider"))
        plog("summary", ok=isinstance(summary, dict), s=f"{time.monotonic() - t0:.2f}")
    elif (bdir / "summary.json").is_file():
        summary = bundle.read_summary(bdir)

    meta = meta.with_(engines={**meta.engines, **engines})
    bundle.write_meta(bdir, meta)

    damage = effective_damage(meta, bdir, unreadable)
    text = render_note(meta, turns, summary, names, my_notes, damage, lang, bundle_path=bdir.resolve())
    note = write_note(bdir, config, text)
    plog("note", damaged=len(damage), lines=text.count("\n"))

    seconds = round(time.monotonic() - t_start, 3)
    bundle.write_json(bdir / "done.json", {
        "ok": True, "at": datetime.now().astimezone().isoformat(timespec="seconds"), "engines": meta.engines,
        "seconds": seconds, "language": lang, "words": {k: len(v) for k, v in words.items()}, "gated": gated,
        "turns": len(turns), "damage": damage, "note": str(note)})

    for stage, fn in (("index", lambda: hooks.index(bdir, config)),
                      ("connect", lambda: hooks.after_note(bdir, note, config))):
        try:
            fn()
            plog(stage, ok=True)
        except Exception as exc:  # noqa: BLE001 - etapa opcional nunca derruba o pipeline
            plog(stage, ok=False, error=type(exc).__name__)
    plog("done", seconds=seconds)
    return note


def rerender(bundle_dir: Path | str, config: Config) -> Path:
    """Refaz a nota a partir de turns.json + summary.json + speakers.json, sem ASR."""
    bdir = Path(bundle_dir).expanduser()
    meta = bundle.read_meta(bdir)
    if not (bdir / "turns.json").is_file():
        raise BundleError(f"{bdir.name}: ainda não processado (sem turns.json); rode `ata process`")
    turns = bundle.read_turns(bdir)
    try:
        info = json.loads((bdir / "turns.json").read_text(encoding="utf-8")).get("speakers") or {}
    except ValueError:
        info = {}
    lang = info.get("language") or meta.language
    lang = i18n.normalize(lang) if lang != "auto" else i18n.normalize(meta.language)
    text = render_note(meta, turns, bundle.read_summary(bdir), bundle.read_speaker_names(bdir),
                       _read_my_notes(bdir), effective_damage(meta, bdir), lang, bundle_path=bdir.resolve())
    note = write_note(bdir, config, text)
    PipelineLog(bdir / "pipeline.log")("rerender", turns=len(turns))
    return note
