"""Camada de dados compartilhada pelo dashboard e pelo servidor MCP.

Só lê arquivos do bundle (meta, turns, summary, speakers, live/) e chama as APIs das outras partes por import
preguiçoso, no momento da chamada (``ata.recorder``, ``ata.pipeline.run``, ``ata.knowledge.*``). Quando uma parte
não está instalada/pronta, a função degrada com uma mensagem em pt-BR em vez de derrubar o servidor.
Nada aqui registra texto de reunião em log.
"""

from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import bundle
from ..config import Config
from ..types import Turn, to_plain

MEETING_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SUMMARY_PARTS = ("tldr", "topics", "decisions", "actions", "questions")
ALL_PARTS = ("meta", "summary", "decisions", "actions", "questions", "transcript", "note", "my_notes")
EXPORT_FORMATS = ("srt", "vtt", "txt", "json", "md", "csv")
MAX_LIMIT = 200


class ApiError(ValueError):
    """Erro de uso (id inválido, reunião não encontrada); ``status`` vira o código HTTP."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ---- paginação ---------------------------------------------------------------------------------------------

def _limit(limit: Any, default: int = 20) -> int:
    try:
        n = int(limit if limit is not None else default)
    except (TypeError, ValueError):
        raise ApiError("limit deve ser inteiro") from None
    return max(1, min(MAX_LIMIT, n))


def _offset(cursor: Any) -> int:
    if cursor in (None, ""):
        return 0
    try:
        n = int(str(cursor))
    except ValueError:
        raise ApiError("cursor inválido (use o next_cursor da página anterior)") from None
    if n < 0:
        raise ApiError("cursor inválido")
    return n


def paginate(items: list[Any], limit: Any = None, cursor: Any = None, default: int = 20) -> dict[str, Any]:
    n, off = _limit(limit, default), _offset(cursor)
    page = items[off:off + n]
    nxt = off + n if off + n < len(items) else None
    return {"items": page, "total": len(items), "next_cursor": None if nxt is None else str(nxt)}


# ---- reuniões ----------------------------------------------------------------------------------------------

def resolve_meeting(config: Config, meeting_id: str) -> Path:
    """Id = nome da pasta do bundle sob ``paths.recordings`` (ou 'latest'). Bloqueia path traversal."""
    mid = str(meeting_id or "").strip()
    root = config.recordings.expanduser()
    if mid == "latest":
        found = bundle.list_bundles(root)
        if not found:
            raise ApiError("nenhuma gravação ainda", 404)
        return found[0]
    if not MEETING_ID_RE.match(mid) or ".." in mid:
        raise ApiError("id de reunião inválido", 400)
    p = (root / mid)
    try:
        if p.resolve().parent != root.resolve():
            raise ApiError("id de reunião inválido", 400)
    except OSError:
        raise ApiError("id de reunião inválido", 400) from None
    if not (p / bundle.META_FILE).is_file():
        raise ApiError(f"reunião não encontrada: {mid}", 404)
    return p


def read_turns_safe(bdir: Path) -> list[Turn]:
    try:
        return bundle.read_turns(bdir)
    except (OSError, ValueError, KeyError):
        return []


def named_turns(bdir: Path) -> list[dict[str, Any]]:
    """Turnos com o nome de exibição aplicado (speakers.json) e sem as palavras (mais leve)."""
    names = bundle.read_speaker_names(bdir)
    out = []
    for i, t in enumerate(read_turns_safe(bdir)):
        out.append({"i": i, "start": round(t.start, 3), "end": round(t.end, 3), "label": t.speaker,
                    "speaker": names.get(t.speaker, t.speaker), "track": t.track, "text": t.text})
    return out


def note_path(config: Config, bdir: Path) -> Path | None:
    try:
        p = config.notes / f"{bdir.name}.md"
        if p.is_file():
            return p
    except Exception:  # noqa: BLE001 - paths.notes ausente
        pass
    q = bdir / "note.md"
    return q if q.is_file() else None


def _duration(meta: bundle.BundleMeta) -> float | None:
    secs = [t.seconds for t in meta.tracks.values() if t.seconds is not None]
    return round(max(secs), 2) if secs else None


def meeting_summary(config: Config, bdir: Path) -> dict[str, Any]:
    try:
        meta = bundle.read_meta(bdir)
    except bundle.BundleError as exc:
        return {"id": bdir.name, "error": str(exc)}
    turns = read_turns_safe(bdir)
    names = bundle.read_speaker_names(bdir)
    speakers = sorted({names.get(t.speaker, t.speaker) for t in turns})
    np_ = note_path(config, bdir)
    return {
        "id": bdir.name, "title": meta.title or meta.name, "date": meta.created_at, "stopped_at": meta.stopped_at,
        "duration_s": _duration(meta), "language": meta.language, "processed": bundle.is_processed(bdir),
        "damage": bundle.damage_report(meta, bdir), "speakers": speakers, "turns": len(turns),
        "note_path": str(np_) if np_ else None, "has_live": (bdir / "live" / "turns.jsonl").is_file(),
        "recording": meta.stopped_at is None,
    }


def _date(s: str | None) -> str:
    return (s or "")[:10]


def list_meetings(config: Config, *, date_from: str | None = None, date_to: str | None = None,
                  participant: str | None = None, language: str | None = None, query: str | None = None,
                  limit: Any = None, cursor: Any = None) -> dict[str, Any]:
    rows = [meeting_summary(config, b) for b in bundle.list_bundles(config.recordings)]
    if date_from:
        rows = [r for r in rows if _date(r.get("date")) >= date_from[:10]]
    if date_to:
        rows = [r for r in rows if _date(r.get("date")) <= date_to[:10]]
    if participant:
        p = participant.lower()
        rows = [r for r in rows if any(p in s.lower() for s in r.get("speakers", []))]
    if language:
        rows = [r for r in rows if r.get("language") == language]
    if query:
        q = query.lower()
        rows = [r for r in rows if q in str(r.get("title", "")).lower() or q in r["id"].lower()]
    return paginate(rows, limit, cursor)


def _in_window(t: dict[str, Any], from_s: float | None, to_s: float | None) -> bool:
    if from_s is not None and t["end"] < from_s:
        return False
    if to_s is not None and t["start"] > to_s:
        return False
    return True


def meeting_detail(config: Config, meeting_id: str, *, parts: list[str] | None = None,
                   from_s: float | None = None, to_s: float | None = None, limit: Any = None,
                   cursor: Any = None) -> dict[str, Any]:
    bdir = resolve_meeting(config, meeting_id)
    want = list(parts) if parts else ["meta", "summary", "transcript"]
    bad = [p for p in want if p not in ALL_PARTS]
    if bad:
        raise ApiError(f"parts inválido: {', '.join(bad)} (use {', '.join(ALL_PARTS)})")
    out: dict[str, Any] = {"id": bdir.name}
    if "meta" in want:
        out["meta"] = meeting_summary(config, bdir)
        out["names"] = bundle.read_speaker_names(bdir)
        out["audio"] = {k: (bdir / f"{k}.wav").is_file() for k in ("far", "mic")}
        try:
            out["offsets"] = bundle.track_offsets(bundle.read_meta(bdir))
        except bundle.BundleError:
            out["offsets"] = {"far": 0.0, "mic": 0.0}
    summary = bundle.read_summary(bdir)
    if "summary" in want:
        out["summary"] = summary
    for key in ("decisions", "actions", "questions"):
        if key in want:
            out[key] = (summary or {}).get(key, [])
    if "transcript" in want:
        turns = [t for t in named_turns(bdir) if _in_window(t, from_s, to_s)]
        page = paginate(turns, limit, cursor, default=MAX_LIMIT)
        out["transcript"] = page
    if "note" in want:
        np_ = note_path(config, bdir)
        out["note"] = np_.read_text(encoding="utf-8") if np_ else None
    if "my_notes" in want:
        p = bdir / "my-notes.md"
        out["my_notes"] = p.read_text(encoding="utf-8") if p.is_file() else None
    return out


def transcript_text(config: Config, meeting_id: str) -> str:
    bdir = resolve_meeting(config, meeting_id)
    return "\n".join(f"[{_mmss(t['start'])}] {t['speaker']}: {t['text']}" for t in named_turns(bdir))


def note_text(config: Config, meeting_id: str) -> str:
    bdir = resolve_meeting(config, meeting_id)
    np_ = note_path(config, bdir)
    if np_:
        return np_.read_text(encoding="utf-8")
    return f"# {bdir.name}\n\n(ainda sem nota: rode `ata process {bdir.name}`)\n"


def _mmss(t: float) -> str:
    t = max(0, int(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ---- busca -------------------------------------------------------------------------------------------------

def _hit_plain(h: Any) -> dict[str, Any]:
    d = to_plain(h)
    if not isinstance(d, dict):
        d = {k: getattr(h, k, None) for k in ("meeting", "title", "date", "start", "speaker", "text", "score",
                                                 "note_path")}
    d = {k: (str(v) if isinstance(v, Path) else v) for k, v in d.items()}
    return d


def _lexical_fallback(config: Config, query: str, limit: int) -> list[dict[str, Any]]:
    toks = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
    hits = []
    for b in bundle.list_bundles(config.recordings):
        try:
            meta = bundle.read_meta(b)
        except bundle.BundleError:
            continue
        for t in named_turns(b):
            low = t["text"].lower()
            score = sum(low.count(w) for w in toks)
            if score:
                np_ = note_path(config, b)
                hits.append({"meeting": b.name, "title": meta.title or meta.name, "date": meta.created_at,
                             "start": t["start"], "speaker": t["speaker"], "text": t["text"],
                             "score": float(score), "note_path": str(np_) if np_ else None})
    hits.sort(key=lambda h: -h["score"])
    return hits[: limit * 5]


def search(config: Config, query: str, *, mode: str = "hybrid", limit: Any = None, cursor: Any = None,
           filters: dict[str, Any] | None = None) -> dict[str, Any]:
    if not query or not str(query).strip():
        raise ApiError("query vazia")
    if mode not in ("hybrid", "lexical", "semantic"):
        raise ApiError("mode deve ser hybrid, lexical ou semantic")
    n, off = _limit(limit, 10), _offset(cursor)
    engine = "index"
    try:
        from ..knowledge.index import search as _search  # parte D
        hits = [_hit_plain(h) for h in _search(config, query, mode=mode, limit=n + off,
                                                filters=filters or {})]
    except ImportError:
        engine = "lexical-fallback"
        hits = _lexical_fallback(config, query, n + off)
    for h in hits:
        if h.get("start") is not None:
            h["t"] = _mmss(float(h["start"]))
    page = paginate(hits, n, off)
    page["engine"] = engine
    return page


def ask(config: Config, question: str, meeting: str | None = None) -> dict[str, Any]:
    if not question or not question.strip():
        raise ApiError("pergunta vazia")
    if meeting:
        resolve_meeting(config, meeting)
    try:
        from ..knowledge.ask import ask as _ask  # parte D
    except ImportError:
        hits = search(config, question, limit=8)["items"]
        return {"answer": None, "evidence": hits,
                "note": "módulo de perguntas indisponível; devolvendo só as evidências da busca"}
    return to_plain(_ask(config, question, meeting=meeting))


def prep(config: Config, query: str, days: int = 90) -> dict[str, Any]:
    if not query or not query.strip():
        raise ApiError("informe o título/assunto da reunião")
    try:
        from ..knowledge.prep import build_prep  # parte D
    except ImportError:
        raise ApiError("módulo de preparação indisponível (ata.knowledge.prep)", 503) from None
    text = build_prep(config, query, days=days)
    items = [{"id": m.group(1), "text": m.group(2).strip()}
             for m in re.finditer(r"\((q\d+)\)\s*(.+)$", text, re.M)]
    return {"markdown": text, "items": items}


def reindex(config: Config) -> dict[str, Any]:
    try:
        from ..knowledge.index import reindex as _reindex  # parte D
    except ImportError:
        raise ApiError("índice indisponível (ata.knowledge.index)", 503) from None
    res = _reindex(config)
    return {"ok": True, "result": to_plain(res)}


# ---- ações / decisões entre reuniões (lidas de summary.json) ---------------------------------------------

def collect_items(config: Config, kind: str, *, owner: str | None = None, since: str | None = None,
                  query: str | None = None, limit: Any = None, cursor: Any = None) -> dict[str, Any]:
    if kind not in ("actions", "decisions", "questions"):
        raise ApiError("kind inválido")
    rows = []
    for b in bundle.list_bundles(config.recordings):
        s = bundle.read_summary(b)
        if not s:
            continue
        try:
            meta = bundle.read_meta(b)
        except bundle.BundleError:
            continue
        if since and _date(meta.created_at) < since[:10]:
            continue
        names = bundle.read_speaker_names(b)
        for item in s.get(kind) or []:
            if not isinstance(item, dict):
                continue
            row = {"meeting": b.name, "title": meta.title or meta.name, "date": meta.created_at,
                   "text": item.get("text", ""), "evidence": item.get("evidence", [])}
            if kind == "actions":
                own = item.get("owner")
                row["owner"] = names.get(own, own) if own else own
                row["due"] = item.get("due")
                row["done"] = bool(item.get("done", False))
            ev = row["evidence"]
            if ev and isinstance(ev, list) and isinstance(ev[0], dict) and ev[0].get("t") is not None:
                row["t"] = _mmss(float(ev[0]["t"]))
            rows.append(row)
    if owner:
        o = owner.lower()
        rows = [r for r in rows if o in str(r.get("owner") or "").lower()]
    if query:
        q = query.lower()
        rows = [r for r in rows if q in str(r.get("text", "")).lower()]
    return paginate(rows, limit, cursor, default=50)


def person_timeline(config: Config, name: str, limit: Any = None, cursor: Any = None) -> dict[str, Any]:
    if not name or not name.strip():
        raise ApiError("informe o nome")
    n = name.strip().lower()
    rows = []
    for b in bundle.list_bundles(config.recordings):
        turns = named_turns(b)
        mine = [t for t in turns if n in t["speaker"].lower()]
        if not mine:
            continue
        s = bundle.read_summary(b) or {}
        names = bundle.read_speaker_names(b)
        owned = [a.get("text") for a in s.get("actions", []) or []
                 if isinstance(a, dict) and n in str(names.get(a.get("owner"), a.get("owner")) or "").lower()]
        summ = meeting_summary(config, b)
        rows.append({"meeting": b.name, "title": summ.get("title"), "date": summ.get("date"),
                     "turns": len(mine), "talk_time_s": round(sum(t["end"] - t["start"] for t in mine), 1),
                     "first_t": _mmss(mine[0]["start"]), "actions": owned})
    out = paginate(rows, limit, cursor)
    out["name"] = name
    return out


# ---- exportação (renderização local a partir de turns.json) ----------------------------------------------

def _ts(t: float, sep: str) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def render_export(turns: list[dict[str, Any]], fmt: str, title: str = "") -> str:
    if fmt == "srt":
        return "\n".join(f"{i + 1}\n{_ts(t['start'], ',')} --> {_ts(t['end'], ',')}\n{t['speaker']}: {t['text']}\n"
                         for i, t in enumerate(turns))
    if fmt == "vtt":
        body = "\n".join(f"{_ts(t['start'], '.')} --> {_ts(t['end'], '.')}\n<v {t['speaker']}>{t['text']}\n"
                         for t in turns)
        return "WEBVTT\n\n" + body
    if fmt == "txt":
        return "\n".join(f"[{_mmss(t['start'])}] {t['speaker']}: {t['text']}" for t in turns) + "\n"
    if fmt == "json":
        return json.dumps({"title": title, "turns": turns}, ensure_ascii=False, indent=2) + "\n"
    if fmt == "md":
        lines = [f"# {title}", ""] + [f"- **[{_mmss(t['start'])}] {t['speaker']}:** {t['text']}" for t in turns]
        return "\n".join(lines) + "\n"
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["start", "end", "speaker", "track", "text"])
        for t in turns:
            w.writerow([t["start"], t["end"], t["speaker"], t["track"], t["text"]])
        return buf.getvalue()
    raise ApiError(f"formato inválido: {fmt} (use {', '.join(EXPORT_FORMATS)})")


def export(config: Config, meeting_id: str, fmt: str, out: str | None = None) -> dict[str, Any]:
    bdir = resolve_meeting(config, meeting_id)
    if fmt not in EXPORT_FORMATS:
        raise ApiError(f"formato inválido: {fmt} (use {', '.join(EXPORT_FORMATS)})")
    turns = named_turns(bdir)
    if not turns:
        raise ApiError("reunião ainda sem transcrição (processe antes)", 409)
    meta = bundle.read_meta(bdir)
    text = render_export(turns, fmt, meta.title or meta.name)
    dest = Path(out).expanduser() if out else bdir / f"export.{fmt}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text(dest, text)
    return {"path": str(dest), "format": fmt, "turns": len(turns), "bytes": len(text.encode("utf-8"))}


# ---- falantes ----------------------------------------------------------------------------------------------

def _cli(config: Config, *argv: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "ata.cli"]
    if config.source:
        cmd += ["--config", str(config.source)]
    return subprocess.run(cmd + list(argv), capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


def rerender(config: Config, bdir: Path) -> dict[str, Any]:
    """Re-renderiza a nota pelo CLI documentado (`ata rerender <alvo>`). Falha não é fatal."""
    if not (bdir / "turns.json").is_file():
        return {"ok": False, "error": "sem transcrição para re-renderizar"}
    try:
        r = _cli(config, "rerender", str(bdir))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": type(exc).__name__}
    return {"ok": r.returncode == 0, "code": r.returncode}


def rename_speakers(config: Config, meeting_id: str, names: dict[str, str], *,
                    do_rerender: bool = True) -> dict[str, Any]:
    bdir = resolve_meeting(config, meeting_id)
    if not isinstance(names, dict) or not names:
        raise ApiError('informe names, ex.: {"Pessoa 2": "Ana"}')
    clean = {}
    for k, v in names.items():
        k, v = str(k).strip(), str(v or "").strip()
        if not k or len(k) > 80 or len(v) > 80 or "\n" in v:
            raise ApiError("nome de falante inválido")
        clean[k] = v
    merged = {**bundle.read_speaker_names(bdir), **clean}
    merged = {k: v for k, v in merged.items() if v and v != k}
    bundle.write_speaker_names(bdir, merged)
    out: dict[str, Any] = {"id": bdir.name, "names": merged}
    out["rerender"] = rerender(config, bdir) if do_rerender else {"ok": False, "skipped": True}
    return out


# ---- gravador (parte A) ------------------------------------------------------------------------------------

def recorder_status(config: Config) -> dict[str, Any]:
    try:
        from .. import recorder  # parte A
    except ImportError:
        return {"phase": "idle", "bundle": None, "started_at": None, "elapsed_s": None, "pid": None,
                "available": False}
    st = dict(recorder.status(config))
    st.setdefault("available", True)
    if st.get("bundle") is not None:
        st["bundle"] = str(st["bundle"])
    return st


def record_start(config: Config, *, title: str | None = None, speakers: int | None = None,
                 language: str | None = None) -> dict[str, Any]:
    try:
        from .. import recorder
    except ImportError:
        raise ApiError("gravador indisponível (ata.recorder)", 503) from None
    st = recorder_status(config)
    if st.get("phase") == "recording":
        return {"ok": False, "error": "já gravando", "status": st}
    bdir = recorder.start(config, title=title, speakers=speakers, language=language)
    return {"ok": True, "bundle": str(bdir), "id": Path(bdir).name}


def record_stop(config: Config, *, process: bool = True) -> dict[str, Any]:
    try:
        from .. import recorder
    except ImportError:
        raise ApiError("gravador indisponível (ata.recorder)", 503) from None
    st = recorder_status(config)
    if st.get("phase") != "recording":
        return {"ok": False, "error": "nada gravando", "status": st}
    res = to_plain(recorder.stop(config, process=process))
    return {"ok": True, **{k: (str(v) if isinstance(v, Path) else v) for k, v in dict(res).items()}}


def record_toggle(config: Config, *, title: str | None = None) -> dict[str, Any]:
    if recorder_status(config).get("phase") == "recording":
        return {"action": "stop", **record_stop(config)}
    return {"action": "start", **record_start(config, title=title)}


# ---- doctor / config ---------------------------------------------------------------------------------------

def doctor(config: Config) -> dict[str, Any]:
    """`ata doctor --json --skip-benchmark` (parte C) por subprocesso; sem ele, checagens básicas locais."""
    try:
        r = _cli(config, "doctor", "--json", "--skip-benchmark", timeout=90)
        if r.returncode in (0, 1) and r.stdout.strip().startswith(("{", "[")):
            return {"source": "ata doctor", "code": r.returncode, "report": json.loads(r.stdout)}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    import shutil
    checks = {
        "recordings_dir": config.recordings.is_dir(),
        "notes_dir": _safe_is_dir(config, "paths.notes"),
        "ffmpeg": bool(shutil.which("ffmpeg")), "pw-record": bool(shutil.which("pw-record")),
        "piper": bool(shutil.which("piper")), "claude": bool(shutil.which("claude")),
        "codex": bool(shutil.which("codex")), "ollama": bool(shutil.which("ollama")),
    }
    return {"source": "básico", "code": None, "report": {"checks": checks, "platform": sys.platform}}


def _safe_is_dir(config: Config, key: str) -> bool:
    try:
        return config.path(key).is_dir()
    except Exception:  # noqa: BLE001
        return False


def config_view(config: Config) -> dict[str, Any]:
    return {"source": str(config.source) if config.source else None, "config": json.loads(json.dumps(
        config.data, default=str))}


# ---- ao vivo -----------------------------------------------------------------------------------------------

def live_turns(bdir: Path, since: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Linhas novas de live/turns.jsonl a partir da linha ``since``; devolve (turnos, próximo since)."""
    p = bdir / "live" / "turns.jsonl"
    if not p.is_file():
        return [], since
    lines = p.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines[since:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out, len(lines)


def current_live_bundle(config: Config) -> Path | None:
    st = recorder_status(config)
    if st.get("phase") == "recording" and st.get("bundle"):
        p = Path(st["bundle"])
        if p.is_dir():
            return p
    found = [b for b in bundle.list_bundles(config.recordings) if (b / "live" / "turns.jsonl").is_file()]
    return found[0] if found else None


# ---- trabalhos em segundo plano (process) -----------------------------------------------------------------

class Jobs:
    """Trabalhos de processamento numa thread cada; um por bundle de cada vez (lane de trabalho)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def submit(self, config: Config, meeting_id: str, *, language: str | None = None,
               speakers: int | None = None, summarize: bool = True) -> dict[str, Any]:
        bdir = resolve_meeting(config, meeting_id)
        with self._lock:
            for j in self._jobs.values():
                if j["meeting"] == bdir.name and j["status"] in ("queued", "running"):
                    return {"job_id": j["job_id"], "status": j["status"], "busy": True}
            jid = uuid.uuid4().hex[:12]
            job = {"job_id": jid, "meeting": bdir.name, "status": "queued", "progress": 0.0,
                   "stage": "na fila", "note": None, "error": None,
                   "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                   "started": None, "seconds": None}
            self._jobs[jid] = job
        th = threading.Thread(target=self._run, args=(jid, config, bdir, language, speakers, summarize),
                              name=f"ata-job-{jid}", daemon=True)
        th.start()
        return {"job_id": jid, "status": "queued", "meeting": bdir.name}

    def _set(self, jid: str, **kw: Any) -> None:
        with self._lock:
            self._jobs[jid].update(kw)

    def _run(self, jid: str, config: Config, bdir: Path, language: str | None, speakers: int | None,
             summarize: bool) -> None:
        t0 = time.monotonic()
        self._set(jid, status="running", progress=0.1, stage="processando", started=t0)
        try:
            from ..pipeline.run import process_bundle  # parte B
            note = process_bundle(bdir, config, language=language, speakers=speakers, summarize=summarize)
            self._set(jid, status="done", progress=1.0, stage="pronto", note=str(note) if note else None)
        except ImportError:
            self._set(jid, status="error", stage="falhou", error="pipeline indisponível (ata.pipeline.run)")
        except Exception as exc:  # noqa: BLE001 - o erro vai para o job, sem texto de reunião
            self._set(jid, status="error", stage="falhou", error=f"{type(exc).__name__}: {exc}"[:300])
        finally:
            self._set(jid, seconds=round(time.monotonic() - t0, 3))

    def get(self, jid: str) -> dict[str, Any]:
        with self._lock:
            j = self._jobs.get(jid)
            if j is None:
                raise ApiError(f"job desconhecido: {jid}", 404)
            out = dict(j)
        if out["status"] == "running" and out.get("started"):
            out["elapsed_s"] = round(time.monotonic() - out["started"], 1)
        out.pop("started", None)
        return out

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(self._jobs)
        return [self.get(j) for j in ids]

    def wait(self, jid: str, timeout: float = 30.0) -> dict[str, Any]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            j = self.get(jid)
            if j["status"] in ("done", "error"):
                return j
            time.sleep(0.02)
        return self.get(jid)
