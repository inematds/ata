"""Bundle ``ata/1``: uma pasta por reunião. Contrato completo em docs/CONTRATO.md §1.

Escrita atômica, leitura estrita (tipo errado -> BundleError com o caminho do campo), relógio do bundle =
``min(start_epoch)``. Os derivados (words, turns, summary, note) são recriáveis a partir de áudio + meta.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .types import TRACKS, Turn, Word

SCHEMA = "ata/1"
META_FILE = "meta.json"
STOP_FILE = "STOP"
DAMAGE_REASONS = ("far_missing", "mic_missing", "far_silent", "mic_silent", "start_unmeasured",
                  "length_drift", "start_separation", "recorder_killed", "dropped_frames")
MAX_LENGTH_DRIFT_S = 2.0
MAX_START_SEPARATION_S = 5.0


class BundleError(ValueError):
    pass


def slugify(text: str | None, limit: int = 40) -> str | None:
    if not text:
        return None
    ascii_ = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")[:limit].strip("-")
    return slug or None


def bundle_name(started: datetime, slug: str | None) -> str:
    base = started.strftime("%Y-%m-%d-%H%M")
    return f"{base}-{slug}" if slug else base


def new_bundle_dir(root: Path | str, started: datetime, title: str | None = None) -> Path:
    root = Path(root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    name = bundle_name(started, slugify(title))
    candidate, n = root / name, 1
    while True:
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            n += 1
            candidate = root / f"{name}-{n}"


@dataclass(frozen=True)
class Track:
    file: str
    device: str = ""
    start_epoch: float | None = None
    start_measured: bool = False
    sample_rate: int = 16000
    samples: int | None = None
    silent: bool | None = None

    @property
    def seconds(self) -> float | None:
        return None if self.samples is None else self.samples / float(self.sample_rate)

    def to_json(self) -> dict[str, Any]:
        return {"file": self.file, "device": self.device, "start_epoch": self.start_epoch,
                "start_measured": self.start_measured, "sample_rate": self.sample_rate,
                "samples": self.samples, "silent": self.silent}


@dataclass(frozen=True)
class BundleMeta:
    name: str
    created_at: str
    tracks: dict[str, Track]
    slug: str | None = None
    title: str | None = None
    stopped_at: str | None = None
    host: str = ""
    platform: str = ""
    recorder: dict[str, str] = field(default_factory=dict)
    language_requested: str = "pt-BR"
    language_detected: str | None = None
    speakers_hint: int | None = None
    engines: dict[str, str] = field(default_factory=dict)
    damage_reasons: tuple[str, ...] = ()
    source_schema: str = SCHEMA

    @property
    def language(self) -> str:
        """Idioma efetivo: detectado quando o pedido foi 'auto'."""
        if self.language_requested == "auto":
            return self.language_detected or "pt-BR"
        return self.language_requested

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA, "name": self.name, "slug": self.slug, "title": self.title,
            "created_at": self.created_at, "stopped_at": self.stopped_at, "host": self.host,
            "platform": self.platform, "recorder": dict(self.recorder),
            "language": {"requested": self.language_requested, "detected": self.language_detected},
            "speakers_hint": self.speakers_hint,
            "tracks": {k: v.to_json() for k, v in self.tracks.items()},
            "engines": dict(self.engines), "damage_reasons": list(self.damage_reasons),
        }

    def with_(self, **changes: Any) -> "BundleMeta":
        return replace(self, **changes)


def _expect(value: Any, types: type | tuple[type, ...], where: str, optional: bool = False) -> Any:
    if value is None and optional:
        return None
    if isinstance(value, bool) and bool not in (types if isinstance(types, tuple) else (types,)):
        raise BundleError(f"{where}: esperado {types}, veio bool")
    if not isinstance(value, types):
        raise BundleError(f"{where}: esperado {types}, veio {type(value).__name__}")
    return value


def _track_from_json(d: Any, where: str) -> Track:
    _expect(d, dict, where)
    rate = _expect(d.get("sample_rate", 16000), int, f"{where}.sample_rate")
    if not 0 < rate <= 384000:
        raise BundleError(f"{where}.sample_rate fora do intervalo")
    samples = _expect(d.get("samples"), int, f"{where}.samples", optional=True)
    if samples is not None and samples < 0:
        raise BundleError(f"{where}.samples negativo")
    epoch = _expect(d.get("start_epoch"), (int, float), f"{where}.start_epoch", optional=True)
    return Track(file=_expect(d.get("file"), str, f"{where}.file"),
                 device=_expect(d.get("device", ""), str, f"{where}.device"),
                 start_epoch=None if epoch is None else float(epoch),
                 start_measured=_expect(d.get("start_measured", False), bool, f"{where}.start_measured"),
                 sample_rate=rate, samples=samples,
                 silent=_expect(d.get("silent"), bool, f"{where}.silent", optional=True))


def meta_from_json(d: Any) -> BundleMeta:
    _expect(d, dict, "meta")
    if d.get("schema") != SCHEMA:
        raise BundleError(f"meta.schema: esperado {SCHEMA!r}, veio {d.get('schema')!r}")
    tracks_raw = _expect(d.get("tracks", {}), dict, "meta.tracks")
    tracks = {}
    for name, td in tracks_raw.items():
        if name not in TRACKS:
            raise BundleError(f"meta.tracks: faixa desconhecida {name!r}")
        tracks[name] = _track_from_json(td, f"meta.tracks.{name}")
    lang = _expect(d.get("language", {}), dict, "meta.language")
    reasons = _expect(d.get("damage_reasons", []), list, "meta.damage_reasons")
    for i, r in enumerate(reasons):
        _expect(r, str, f"meta.damage_reasons[{i}]")
    return BundleMeta(
        name=_expect(d.get("name"), str, "meta.name"),
        created_at=_expect(d.get("created_at"), str, "meta.created_at"),
        tracks=tracks,
        slug=_expect(d.get("slug"), str, "meta.slug", optional=True),
        title=_expect(d.get("title"), str, "meta.title", optional=True),
        stopped_at=_expect(d.get("stopped_at"), str, "meta.stopped_at", optional=True),
        host=_expect(d.get("host", ""), str, "meta.host"),
        platform=_expect(d.get("platform", ""), str, "meta.platform"),
        recorder=_expect(d.get("recorder", {}), dict, "meta.recorder"),
        language_requested=_expect(lang.get("requested", "pt-BR"), str, "meta.language.requested"),
        language_detected=_expect(lang.get("detected"), str, "meta.language.detected", optional=True),
        speakers_hint=_expect(d.get("speakers_hint"), int, "meta.speakers_hint", optional=True),
        engines=_expect(d.get("engines", {}), dict, "meta.engines"),
        damage_reasons=tuple(reasons),
    )


def _atomic_write(path: Path, text: str) -> Path:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def write_json(path: Path, obj: Any) -> Path:
    return _atomic_write(Path(path), json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def write_text(path: Path, text: str) -> Path:
    return _atomic_write(Path(path), text)


def write_meta(bundle_dir: Path | str, meta: BundleMeta) -> Path:
    return write_json(Path(bundle_dir) / META_FILE, meta.to_json())


def read_meta(bundle_dir: Path | str) -> BundleMeta:
    """Lê o meta. Bundles do CrunchLog (bundle_version 2) passam pelo leitor de compatibilidade."""
    p = Path(bundle_dir) / META_FILE
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BundleError(f"{p}: não existe") from None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"{p}: JSON inválido ({type(exc).__name__})") from None
    if isinstance(d, dict) and d.get("schema") != SCHEMA and "bundle_version" in d:
        from .compat.crunchlog import meta_from_crunchlog
        return meta_from_crunchlog(d)
    return meta_from_json(d)


def track_offsets(meta: BundleMeta) -> dict[str, float]:
    """Deslocamento de cada faixa no relógio do bundle (min start_epoch = 0). Sem epoch -> 0."""
    epochs = {k: t.start_epoch for k, t in meta.tracks.items() if t.start_epoch is not None}
    if not epochs:
        return {k: 0.0 for k in meta.tracks}
    t0 = min(epochs.values())
    return {k: (epochs[k] - t0 if k in epochs else 0.0) for k in meta.tracks}


def damage_report(meta: BundleMeta, bundle_dir: Path | str | None = None) -> list[str]:
    """Motivos (vocabulário fechado) pelos quais o bundle não é confiável; calculados + persistidos."""
    reasons: list[str] = []
    for name in TRACKS:
        tr = meta.tracks.get(name)
        exists = tr is not None and (bundle_dir is None or (Path(bundle_dir) / tr.file).is_file())
        if not exists:
            reasons.append(f"{name}_missing")
        elif tr.silent:
            reasons.append(f"{name}_silent")
    present = [t for t in meta.tracks.values() if t is not None]
    if any(not t.start_measured for t in present):
        reasons.append("start_unmeasured")
    secs = [t.seconds for t in present if t.seconds is not None]
    if len(secs) == 2 and abs(secs[0] - secs[1]) > MAX_LENGTH_DRIFT_S:
        reasons.append("length_drift")
    epochs = [t.start_epoch for t in present if t.start_epoch is not None]
    if len(epochs) == 2 and abs(epochs[0] - epochs[1]) > MAX_START_SEPARATION_S:
        reasons.append("start_separation")
    for r in meta.damage_reasons:
        if r not in reasons:
            reasons.append(r)
    return reasons


# ---- derivados -------------------------------------------------------------------------------------------

def write_words(bundle_dir: Path, words: dict[str, list[Word]]) -> Path:
    return write_json(Path(bundle_dir) / "words.json", {k: [w.to_json() for w in v] for k, v in words.items()})


def read_words(bundle_dir: Path) -> dict[str, list[Word]]:
    d = json.loads((Path(bundle_dir) / "words.json").read_text(encoding="utf-8"))
    return {k: [Word.from_json(w) for w in v] for k, v in d.items()}


def write_turns(bundle_dir: Path, turns: list[Turn], speakers: dict[str, Any] | None = None) -> Path:
    return write_json(Path(bundle_dir) / "turns.json",
                      {"turns": [t.to_json() for t in turns], "speakers": speakers or {}})


def read_turns(bundle_dir: Path) -> list[Turn]:
    d = json.loads((Path(bundle_dir) / "turns.json").read_text(encoding="utf-8"))
    return [Turn.from_json(t) for t in d["turns"]]


def read_speaker_names(bundle_dir: Path) -> dict[str, str]:
    p = Path(bundle_dir) / "speakers.json"
    if not p.is_file():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in d.get("names", {}).items() if v}


def write_speaker_names(bundle_dir: Path, names: dict[str, str]) -> Path:
    return write_json(Path(bundle_dir) / "speakers.json",
                      {"names": {k: v for k, v in names.items() if v},
                       "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")})


def read_summary(bundle_dir: Path) -> dict[str, Any] | None:
    p = Path(bundle_dir) / "summary.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def is_processed(bundle_dir: Path) -> bool:
    p = Path(bundle_dir) / "done.json"
    if not p.is_file():
        return False
    try:
        return bool(json.loads(p.read_text(encoding="utf-8")).get("ok"))
    except (OSError, ValueError):
        return False


def list_bundles(root: Path | str) -> list[Path]:
    """Pastas de reunião sob ``root``, mais recentes primeiro (pelo nome AAAA-MM-DD-HHMM)."""
    root = Path(root).expanduser()
    if not root.is_dir():
        return []
    out = [p for p in root.iterdir() if p.is_dir() and (p / META_FILE).is_file()]
    return sorted(out, key=lambda p: p.name, reverse=True)


def resolve(target: str | Path, recordings: Path) -> Path:
    """Aceita caminho de pasta, nome da pasta sob recordings, 'latest', ou caminho de nota com 'bundle:' no
    frontmatter. BundleError se não achar."""
    s = str(target)
    if s == "latest":
        found = list_bundles(recordings)
        if not found:
            raise BundleError("nenhuma gravação ainda")
        return found[0]
    p = Path(s).expanduser()
    if p.is_dir() and (p / META_FILE).is_file():
        return p
    if p.is_file() and p.suffix == ".md":
        m = re.search(r"^bundle:\s*\"?([^\"\n]+)\"?\s*$", p.read_text(encoding="utf-8"), re.M)
        if m:
            return resolve(m.group(1), recordings)
    q = Path(recordings).expanduser() / s
    if q.is_dir() and (q / META_FILE).is_file():
        return q
    raise BundleError(f"gravação não encontrada: {s}")
