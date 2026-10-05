"""Índice de busca das reuniões: SQLite em ``<cache>/ata.db`` (FTS5 + vetores em tabela própria).

O índice é descartável: a fonte canônica são os bundles (turns.json, summary.json) e as notas. ``reindex``
reconstrói tudo. Busca lexical (FTS5/BM25, sem acento), semântica (cosseno em numpy sobre float32
normalizados) e híbrida (fusão RRF, k=60). Os trechos de transcrição são janelas de ~60–90 s; decisões,
ações, perguntas e o tldr do resumo entram como trechos próprios (coluna ``kind``).

Os nomes de ``speakers.json`` são aplicados na hora de indexar: depois de renomear falantes, rode
``index_bundle`` de novo.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .. import bundle
from ..config import Config
from ..types import Turn
from .summary import fmt_ts

log = logging.getLogger("ata.knowledge.index")

DB_NAME = "ata.db"
RRF_K = 60
CHUNK_MIN_S = 60.0
CHUNK_MAX_S = 90.0
EMBED_BATCH = 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY, title TEXT, date TEXT, started_at TEXT, language TEXT,
    participants TEXT, note_path TEXT, bundle TEXT, indexed_at TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'transcript',
    start REAL, "end" REAL, speaker TEXT, speakers TEXT, text TEXT, lines TEXT
);
CREATE INDEX IF NOT EXISTS chunks_meeting ON chunks(meeting_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize = 'unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS vectors (
    chunk_id INTEGER PRIMARY KEY, dim INTEGER NOT NULL, model TEXT, blob BLOB NOT NULL
);
"""


def db_path(config: Config) -> Path:
    return config.cache / DB_NAME


def connect(config: Config) -> sqlite3.Connection:
    p = db_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


# ---- texto -----------------------------------------------------------------------------------------------

def fold(text: str) -> str:
    """minúsculas sem acento (para comparar tokens)."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def tokens(text: str) -> list[str]:
    return re.findall(r"\w+", fold(text))


def fts_query(query: str) -> str | None:
    """Consulta do usuário -> expressão FTS5 segura: cada token entre aspas, prefixo nos longos, OR."""
    toks = [t for t in tokens(query) if t]
    if not toks:
        return None
    seen: list[str] = []
    for t in toks:
        term = f'"{t}"*' if len(t) >= 4 else f'"{t}"'
        if term not in seen:
            seen.append(term)
    return " OR ".join(seen)


# ---- trechos ---------------------------------------------------------------------------------------------

@dataclass
class Chunk:
    kind: str
    start: float
    end: float
    speaker: str
    text: str
    lines: list[list[Any]] = field(default_factory=list)   # [[start, speaker, texto], ...]

    @property
    def speakers(self) -> list[str]:
        return sorted({str(x[1]) for x in self.lines} or {self.speaker})


def chunk_turns(turns: Iterable[Turn], min_s: float = CHUNK_MIN_S, max_s: float = CHUNK_MAX_S) -> list[Chunk]:
    """Agrupa turnos em janelas: fecha ao passar de ``min_s``; nunca passa de ``max_s`` (exceto turno único)."""
    out: list[Chunk] = []
    cur: list[Turn] = []

    def close() -> None:
        if not cur:
            return
        chars: dict[str, int] = {}
        for t in cur:
            chars[t.speaker] = chars.get(t.speaker, 0) + len(t.text)
        main = max(chars, key=lambda k: chars[k])
        out.append(Chunk("transcript", cur[0].start, max(t.end for t in cur), main,
                         "\n".join(f"{t.speaker}: {t.text}" for t in cur),
                         [[round(t.start, 3), t.speaker, t.text] for t in cur]))
        cur.clear()

    for t in sorted((t for t in turns if str(t.text).strip()), key=lambda t: t.start):
        if cur and t.end - cur[0].start > max_s:
            close()
        cur.append(t)
        if cur[-1].end - cur[0].start >= min_s:
            close()
    close()
    return out


def _ev(item: dict[str, Any]) -> tuple[float, str]:
    ev = item.get("evidence") or []
    if ev and isinstance(ev[0], dict):
        return float(ev[0].get("t") or 0.0), str(ev[0].get("speaker") or "")
    return 0.0, ""


def summary_chunks(summary: dict[str, Any] | None) -> list[Chunk]:
    if not summary:
        return []
    out: list[Chunk] = []
    if summary.get("tldr"):
        out.append(Chunk("tldr", 0.0, 0.0, "", str(summary["tldr"]), [[0.0, "", str(summary["tldr"])]]))
    for kind, key in (("decision", "decisions"), ("action", "actions"), ("question", "questions")):
        for item in summary.get(key) or []:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            t, sp = _ev(item)
            if kind == "action" and item.get("owner"):
                sp = sp or str(item["owner"])
            out.append(Chunk(kind, t, t, sp, text, [[t, sp, text]]))
    return out


# ---- indexação -------------------------------------------------------------------------------------------

def note_path_for(bundle_dir: Path, config: Config) -> Path | None:
    try:
        p = config.notes / f"{Path(bundle_dir).name}.md"
        if p.is_file():
            return p
    except Exception:
        pass
    q = Path(bundle_dir) / "note.md"
    return q if q.is_file() else None


def _named_turns(bundle_dir: Path) -> list[Turn]:
    if not (bundle_dir / "turns.json").is_file():
        return []
    turns = bundle.read_turns(bundle_dir)
    names = bundle.read_speaker_names(bundle_dir)
    if names:
        for t in turns:
            t.speaker = names.get(t.speaker, t.speaker)
    return turns


def _embed(config: Config, texts: list[str]) -> tuple[str, list[np.ndarray]] | None:
    from ..engines import registry
    try:
        emb = registry.text_embedder_for(config)
        vecs: list[np.ndarray] = []
        for i in range(0, len(texts), EMBED_BATCH):
            for v in emb.embed_texts(texts[i:i + EMBED_BATCH]):
                a = np.asarray(v, dtype=np.float32)
                n = float(np.linalg.norm(a)) or 1.0
                vecs.append(a / n)
        if len(vecs) != len(texts):
            raise ValueError("número de vetores diferente do de textos")
        model = f"{getattr(emb, 'name', '?')}:{config.get('embeddings.model') or ''}"
        return model, vecs
    except Exception as exc:
        log.warning("embeddings indisponíveis: %s", type(exc).__name__)
        return None


def _delete_meeting(con: sqlite3.Connection, meeting_id: str) -> None:
    ids = [r[0] for r in con.execute("SELECT id FROM chunks WHERE meeting_id = ?", (meeting_id,))]
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        marks = ",".join("?" * len(part))
        con.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({marks})", part)
        con.execute(f"DELETE FROM vectors WHERE chunk_id IN ({marks})", part)
    con.execute("DELETE FROM chunks WHERE meeting_id = ?", (meeting_id,))
    con.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))


def _index_into(con: sqlite3.Connection, bundle_dir: Path, config: Config, embed: bool = True) -> int:
    bundle_dir = Path(bundle_dir)
    meta = bundle.read_meta(bundle_dir)
    turns = _named_turns(bundle_dir)
    summary = bundle.read_summary(bundle_dir)
    chunks = chunk_turns(turns) + summary_chunks(summary)
    participants = sorted({t.speaker for t in turns})
    note = note_path_for(bundle_dir, config)
    _delete_meeting(con, meta.name)
    con.execute(
        "INSERT INTO meetings (id, title, date, started_at, language, participants, note_path, bundle, indexed_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (meta.name, meta.title or meta.name, meta.created_at[:10], meta.created_at, meta.language,
         json.dumps(participants, ensure_ascii=False), str(note) if note else None, str(bundle_dir.resolve()),
         datetime.now().astimezone().isoformat(timespec="seconds")))
    ids: list[int] = []
    for c in chunks:
        cur = con.execute(
            'INSERT INTO chunks (meeting_id, kind, start, "end", speaker, speakers, text, lines)'
            " VALUES (?,?,?,?,?,?,?,?)",
            (meta.name, c.kind, c.start, c.end, c.speaker, "|" + "|".join(c.speakers) + "|", c.text,
             json.dumps(c.lines, ensure_ascii=False)))
        ids.append(int(cur.lastrowid))
        con.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (cur.lastrowid, c.text))
    if embed and chunks:
        got = _embed(config, [c.text for c in chunks])
        if got:
            model, vecs = got
            con.executemany("INSERT INTO vectors (chunk_id, dim, model, blob) VALUES (?,?,?,?)",
                            [(i, int(v.shape[0]), model, v.tobytes()) for i, v in zip(ids, vecs)])
    return len(chunks)


def index_bundle(bundle_dir: Path | str, config: Config) -> int:
    """(Re)indexa uma reunião. Devolve o número de trechos. Sem embeddings -> só lexical (e loga o tipo)."""
    con = connect(config)
    try:
        with con:
            return _index_into(con, Path(bundle_dir), config)
    finally:
        con.close()


def remove_bundle(meeting_id: str, config: Config) -> None:
    con = connect(config)
    try:
        with con:
            _delete_meeting(con, meeting_id)
    finally:
        con.close()


def _indexable(bdir: Path) -> bool:
    return (bdir / "turns.json").is_file() or (bdir / "summary.json").is_file()


def reindex(config: Config) -> dict[str, int]:
    """Reconstrói o índice a partir de todos os bundles em ``paths.recordings``."""
    p = db_path(config)
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(p) + suffix).unlink(missing_ok=True)
    con = connect(config)
    meetings = chunks = failed = 0
    try:
        for bdir in bundle.list_bundles(config.recordings):
            if not _indexable(bdir):
                continue
            try:
                with con:
                    chunks += _index_into(con, bdir, config)
                meetings += 1
            except Exception as exc:
                failed += 1
                log.warning("reindex: %s falhou (%s)", bdir.name, type(exc).__name__)
    finally:
        con.close()
    return {"meetings": meetings, "chunks": chunks, "failed": failed}


def ensure_index(config: Config) -> None:
    """Índice vazio mas há reuniões processadas -> reconstrói (primeira busca depois de instalar)."""
    con = connect(config)
    try:
        n = con.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    finally:
        con.close()
    if n == 0 and any(_indexable(b) for b in bundle.list_bundles(config.recordings)):
        reindex(config)


# ---- busca -----------------------------------------------------------------------------------------------

@dataclass
class Hit:
    meeting: str
    title: str
    date: str
    start: float
    speaker: str
    text: str
    score: float
    note_path: str | None = None
    kind: str = "transcript"
    chunk_id: int = 0
    bundle: str | None = None

    @property
    def ts(self) -> str:
        return fmt_ts(self.start)

    def citation(self) -> str:
        return f"[{self.title}, {self.ts}]"

    def format(self, width: int = 240) -> str:
        text = " ".join(self.text.split())
        if len(text) > width:
            text = text[: width - 1].rstrip() + "…"
        who = f"{self.speaker}: " if self.speaker else ""
        return f"{self.citation()} {who}{text}"

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["ts"] = self.ts
        d["line"] = self.format()
        return d


_REL = re.compile(r"^(\d+)\s*([dsmw])$", re.I)


def parse_date(value: str | None) -> str | None:
    """'AAAA-MM-DD' passa; '7d', '2w', '3m' viram data relativa a hoje; vazio -> None."""
    if not value:
        return None
    s = str(value).strip()
    m = _REL.match(s)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        days = {"d": n, "s": 7 * n, "w": 7 * n, "m": 30 * n}[unit]
        return (date.today() - timedelta(days=days)).isoformat()
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        raise ValueError(f"data inválida: {value!r} (use AAAA-MM-DD ou 7d/2w/3m)") from None


def resolve_meeting_id(config: Config, target: str) -> str:
    """Caminho/nome/'latest'/nota -> id da reunião (nome da pasta); se não achar, devolve o texto."""
    try:
        return bundle.resolve(target, config.recordings).name
    except Exception:
        return str(target)


def _where(filters: dict[str, Any] | None) -> tuple[str, list[Any]]:
    f = filters or {}
    clauses, args = [], []
    since, until = parse_date(f.get("since")), parse_date(f.get("until"))
    if since:
        clauses.append("m.date >= ?")
        args.append(since)
    if until:
        clauses.append("m.date <= ?")
        args.append(until)
    if f.get("language"):
        clauses.append("m.language = ?")
        args.append(str(f["language"]))
    if f.get("speaker"):
        clauses.append("c.speakers LIKE ?")
        args.append(f"%|{f['speaker']}|%")
    if f.get("meeting"):
        mid = str(f["meeting"])
        clauses.append("(m.id = ? OR m.id LIKE ? OR m.title = ?)")
        args += [mid, f"{mid}%", mid]
    if f.get("kind"):
        kinds = f["kind"] if isinstance(f["kind"], (list, tuple)) else [f["kind"]]
        clauses.append(f"c.kind IN ({','.join('?' * len(kinds))})")
        args += list(kinds)
    return (" AND ".join(clauses) or "1"), args


def _lexical(con: sqlite3.Connection, query: str, where: str, args: list[Any], n: int) -> list[int]:
    q = fts_query(query)
    if not q:
        return []
    sql = ("SELECT c.id FROM chunks_fts f JOIN chunks c ON c.id = f.rowid JOIN meetings m ON m.id = c.meeting_id"
           f" WHERE chunks_fts MATCH ? AND {where} ORDER BY bm25(chunks_fts) ASC LIMIT ?")
    return [r[0] for r in con.execute(sql, [q, *args, n])]


def _semantic(con: sqlite3.Connection, config: Config, query: str, where: str, args: list[Any],
              n: int) -> list[int]:
    got = _embed(config, [query])
    if not got:
        return []
    _, (qv,) = got
    rows = con.execute(
        "SELECT v.chunk_id, v.blob FROM vectors v JOIN chunks c ON c.id = v.chunk_id"
        f" JOIN meetings m ON m.id = c.meeting_id WHERE v.dim = ? AND {where}", [int(qv.shape[0]), *args]
    ).fetchall()
    if not rows:
        return []
    mat = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), -1)
    scores = mat @ qv
    order = np.argsort(-scores)[:n]
    return [int(rows[i][0]) for i in order if scores[i] > 0]


def rrf(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    out: dict[int, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, start=1):
            out[cid] = out.get(cid, 0.0) + 1.0 / (k + rank)
    return out


def _best_line(lines: list[list[Any]], query: str) -> list[Any] | None:
    if not lines:
        return None
    q = set(tokens(query))
    best, best_score = lines[0], -1.0
    for ln in lines:
        lt = set(tokens(str(ln[2])))
        score = len(q & lt) + 0.01 * len({x for x in lt for y in q if len(y) >= 4 and x.startswith(y)})
        if score > best_score:
            best, best_score = ln, score
    return best


def search(config: Config, query: str, mode: str = "hybrid", limit: int = 10,
           filters: dict[str, Any] | None = None) -> list[Hit]:
    """Busca nas reuniões. ``mode``: hybrid (padrão) | lexical | semantic. ``filters``: since, until,
    speaker, language, meeting, kind. Cada Hit aponta para a fala que melhor casa com a consulta."""
    if mode not in ("hybrid", "lexical", "semantic"):
        raise ValueError(f"modo inválido: {mode!r} (use hybrid, lexical ou semantic)")
    ensure_index(config)
    where, args = _where(filters)
    n = max(50, limit * 5)
    con = connect(config)
    try:
        rankings = []
        if mode in ("hybrid", "lexical"):
            rankings.append(_lexical(con, query, where, args, n))
        if mode in ("hybrid", "semantic"):
            rankings.append(_semantic(con, config, query, where, args, n))
        scores = rrf(rankings)
        top = sorted(scores, key=lambda c: (-scores[c], c))[:limit]
        hits = []
        for cid in top:
            r = con.execute(
                'SELECT c.*, m.title, m.date, m.note_path, m.bundle FROM chunks c JOIN meetings m'
                " ON m.id = c.meeting_id WHERE c.id = ?", (cid,)).fetchone()
            if r is None:
                continue
            ln = _best_line(json.loads(r["lines"] or "[]"), query)
            start, speaker, text = (float(ln[0]), str(ln[1]), str(ln[2])) if ln else (r["start"], r["speaker"],
                                                                                       r["text"])
            hits.append(Hit(meeting=r["meeting_id"], title=r["title"], date=r["date"], start=start,
                            speaker=speaker, text=text, score=round(scores[cid], 6), note_path=r["note_path"],
                            kind=r["kind"], chunk_id=cid, bundle=r["bundle"]))
        return hits
    finally:
        con.close()


def meetings(config: Config, *, since: str | None = None, until: str | None = None) -> list[dict[str, Any]]:
    """Reuniões indexadas (mais recentes primeiro)."""
    ensure_index(config)
    clauses, args = [], []
    if parse_date(since):
        clauses.append("date >= ?")
        args.append(parse_date(since))
    if parse_date(until):
        clauses.append("date <= ?")
        args.append(parse_date(until))
    con = connect(config)
    try:
        rows = con.execute(f"SELECT * FROM meetings WHERE {' AND '.join(clauses) or '1'}"
                           " ORDER BY started_at DESC", args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["participants"] = json.loads(d.get("participants") or "[]")
            out.append(d)
        return out
    finally:
        con.close()
