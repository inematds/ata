"""Voiceprints (opt-in): reconhecer quem fala entre reuniões.

Dado biométrico (LGPD/GDPR): fica só em ``<cache>/voices.json`` (permissão 0600), nunca é exportado (``list``
e ``--json`` mostram nome, amostras e data, nunca o vetor), e ``ata voices forget`` apaga. A rotulagem
automática só roda com ``[voices] enabled = true``.

Formato: ``{nome: {"centroid": [...], "n": int, "updated_at": ISO, "track": "far"|"mic", "model": str}}``.
O centróide é a média dos embeddings L2-normalizados (renormalizada). Atribui um nome a um rótulo só quando
o cosseno ≥ ``voices.threshold`` e a vantagem sobre o 2º candidato ≥ ``MARGIN``; um nome por rótulo.

Base de tempo: ``auto_label`` recebe ``spans_by_track`` no RELÓGIO DO BUNDLE (como ``pipeline.run`` passa, já
com o offset somado) e ``enroll`` lê ``turns.json`` (também relógio do bundle); os dois descontam o
deslocamento da faixa antes de chamar o embedder (que trabalha em tempo do arquivo).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from . import bundle
from .config import Config
from .types import Span

log = logging.getLogger("ata.voices")

MARGIN = 0.05
MIC_PREFIX = "mic:"     # = ata.pipeline.turns.MIC_PREFIX (rótulos do mic no mapa ``labels``)
MIN_SPEECH_S = 1.0

PRIVACY_WARNING = (
    "aviso de privacidade: uma impressão de voz é dado biométrico (LGPD art. 5º, II; GDPR art. 9).\n"
    "  O Ata guarda só um vetor numérico, localmente, em {path} (nunca sai da máquina).\n"
    "  Cadastre apenas quem consentiu. Para apagar: ata voices forget <nome> | ata voices forget --all"
)


class VoiceError(ValueError):
    pass


def voices_path(config: Config) -> Path:
    return config.cache / "voices.json"


def load(config: Config) -> dict[str, dict[str, Any]]:
    p = voices_path(config)
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("voices.json ilegível; ignorado")
        return {}
    return d if isinstance(d, dict) else {}


def save(config: Config, data: dict[str, dict[str, Any]]) -> Path:
    p = voices_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_json(p, data)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def _unit(v: Any) -> np.ndarray:
    a = np.asarray(v, dtype=np.float64)
    n = float(np.linalg.norm(a))
    return a / n if n else a


def cosine(a: Any, b: Any) -> float:
    x, y = _unit(a), _unit(b)
    if x.shape != y.shape:
        return -1.0
    return float(np.dot(x, y))


def _windows_for(bundle_dir: Path, label: str) -> tuple[str, list[tuple[float, float]]]:
    """Janelas (relógio do bundle) e faixa do rótulo. Aceita o rótulo ("Pessoa 2") ou o nome já dado."""
    turns = bundle.read_turns(bundle_dir)
    names = bundle.read_speaker_names(bundle_dir)
    wanted = {label} | {k for k, v in names.items() if v == label}
    mine = [t for t in turns if t.speaker in wanted or names.get(t.speaker) == label]
    if not mine:
        raise VoiceError(f"rótulo {label!r} não aparece em turns.json")
    tracks: dict[str, float] = {}
    for t in mine:
        tracks[t.track] = tracks.get(t.track, 0.0) + (t.end - t.start)
    track = max(tracks, key=lambda k: tracks[k])
    return track, [(t.start, t.end) for t in mine if t.track == track]


def enroll(config: Config, name: str, bundle_dir: Path | str, label: str) -> dict[str, Any]:
    """Cadastra (ou reforça) a voz ``name`` a partir das falas de ``label`` no bundle. Devolve a entrada
    sem o vetor: {name, n, track, updated_at, model, first}."""
    from .engines import registry

    name = name.strip()
    if not name:
        raise VoiceError("nome vazio")
    bdir = Path(bundle_dir)
    if not (bdir / "turns.json").is_file():
        raise VoiceError("o bundle ainda não foi processado (falta turns.json)")
    track, windows = _windows_for(bdir, label)
    if sum(b - a for a, b in windows) < MIN_SPEECH_S:
        raise VoiceError(f"fala insuficiente para {label!r} (mínimo {MIN_SPEECH_S:.0f} s)")
    meta = bundle.read_meta(bdir)
    tr = meta.tracks.get(track)
    if tr is None or not (bdir / tr.file).is_file():
        raise VoiceError(f"faixa {track} sem áudio no bundle")
    off = bundle.track_offsets(meta).get(track, 0.0)
    spans = [Span(max(0.0, a - off), max(0.0, b - off), label) for a, b in windows]
    embedder = registry.build("speaker_embedder", str(config.get("diarization.engine")), config)
    vecs = embedder.embed(bdir / tr.file, spans)
    if label not in vecs:
        raise VoiceError("o motor não devolveu embedding para esse rótulo")
    v = _unit(vecs[label])
    model = str(getattr(embedder, "name", "?"))
    data = load(config)
    first = not voices_path(config).is_file()
    old = data.get(name)
    if old and old.get("model") == model and len(old.get("centroid", [])) == len(v):
        n = int(old.get("n", 1))
        centroid = _unit(_unit(old["centroid"]) * n + v)
        n += 1
    else:
        if old:
            log.warning("voices: cadastro anterior de outro motor substituído")
        centroid, n = v, 1
    entry = {"centroid": [round(float(x), 6) for x in centroid], "n": n, "track": track, "model": model,
             "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    data[name] = entry
    save(config, data)
    return {"name": name, "n": n, "track": track, "model": model, "updated_at": entry["updated_at"],
            "first": first}


def list_voices(config: Config) -> list[dict[str, Any]]:
    """Cadastros SEM os vetores."""
    return [{"name": k, "n": int(v.get("n", 0)), "track": v.get("track"), "model": v.get("model"),
             "updated_at": v.get("updated_at")} for k, v in sorted(load(config).items())]


def forget(config: Config, name: str | None = None, *, all: bool = False) -> list[str]:
    """Apaga um nome (ou todos). Devolve os nomes apagados. ``all`` apaga também o arquivo."""
    data = load(config)
    if all:
        removed = sorted(data)
        voices_path(config).unlink(missing_ok=True)
        return removed
    if not name or name not in data:
        return []
    del data[name]
    save(config, data)
    return [name]


def _flat_labels(labels: Any, track: str) -> dict[str, str]:
    if not labels:
        return {}
    if isinstance(labels.get(track), dict):
        return dict(labels[track])
    return {k: v for k, v in labels.items() if isinstance(v, str)}


def auto_label(bundle_dir: Path | str, config: Config, spans_by_track: dict[str, list[Span]],
               labels: dict[str, Any]) -> dict[str, str]:
    """Rótulo final ("Pessoa 2") -> nome cadastrado, só para casamentos confiáveis. Desligado -> {}.

    ``spans_by_track``: {"far": [Span(start, end, "S0"), ...], "mic": [...]} no relógio do bundle.
    ``labels``: rótulo do diarizador -> rótulo final, plano ({"S0": "Pessoa 2", "mic:S0": "Eu"}; no mic
    procura primeiro ``mic:<rótulo>``) ou por faixa ({"far": {"S0": "Pessoa 2"}, "mic": {"S0": "Eu"}}).
    Rótulo sem mapeamento usa o próprio rótulo.
    Nunca levanta exceção: erro do motor -> {} (loga só o tipo)."""
    if not config.get("voices.enabled"):
        return {}
    data = load(config)
    if not data:
        return {}
    threshold = float(config.get("voices.threshold") or 0.72)
    try:
        from .engines import registry
        meta = bundle.read_meta(bundle_dir)
        embedder = registry.build("speaker_embedder", str(config.get("diarization.engine")), config)
        model = str(getattr(embedder, "name", "?"))
        offsets = bundle.track_offsets(meta)
        candidates: list[tuple[float, str, str]] = []   # (score, final_label, name)
        for track, spans in (spans_by_track or {}).items():
            tr = meta.tracks.get(track)
            if not spans or tr is None or not (Path(bundle_dir) / tr.file).is_file():
                continue
            mapping = _flat_labels(labels, track)
            off = offsets.get(track, 0.0)
            file_spans = [Span(max(0.0, s.start - off), max(0.0, s.end - off), s.speaker) for s in spans]
            vecs = embedder.embed(Path(bundle_dir) / tr.file, file_spans)
            for diar_label, vec in vecs.items():
                final = mapping.get(f"{MIC_PREFIX}{diar_label}" if track == "mic" else diar_label) \
                    or mapping.get(diar_label, diar_label)
                scored = sorted(((cosine(vec, e["centroid"]), n) for n, e in data.items()
                                 if e.get("model", model) == model and e.get("centroid")), reverse=True)
                if not scored:
                    continue
                best, second = scored[0][0], (scored[1][0] if len(scored) > 1 else -1.0)
                if best >= threshold and best - second >= MARGIN:
                    candidates.append((best, final, scored[0][1]))
    except Exception as exc:
        log.warning("voices: rotulagem automática falhou: %s", type(exc).__name__)
        return {}
    out: dict[str, str] = {}
    used: set[str] = set()
    for score, final, name in sorted(candidates, reverse=True):
        if final in out or name in used:
            continue
        out[final] = name
        used.add(name)
    return out


# ---- comandos --------------------------------------------------------------------------------------------

def cmd_voices(args: argparse.Namespace, config: Config) -> int:
    action = getattr(args, "voices_cmd", None)
    if action == "enroll":
        try:
            bdir = bundle.resolve(args.target, config.recordings)
        except bundle.BundleError as exc:
            print(f"erro: {exc}", file=sys.stderr)
            return 2
        if not voices_path(config).is_file():
            print(PRIVACY_WARNING.format(path=voices_path(config)), file=sys.stderr)
        try:
            res = enroll(config, args.name, bdir, args.label)
        except VoiceError as exc:
            print(f"erro: {exc}", file=sys.stderr)
            return 2
        print(f"voz cadastrada: {res['name']} ({res['n']} amostra(s), faixa {res['track']})")
        if not config.get("voices.enabled"):
            print("aviso: [voices] enabled = false — a rotulagem automática só roda com enabled = true",
                  file=sys.stderr)
        return 0
    if action == "list":
        rows = list_voices(config)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for r in rows:
                print(f"{r['name']}\t{r['n']} amostra(s)\t{r['track']}\t{r['updated_at']}")
            if not rows:
                print("nenhuma voz cadastrada", file=sys.stderr)
        return 0 if rows else 3
    if action == "forget":
        if not args.all and not args.name:
            print("erro: diga o nome ou use --all", file=sys.stderr)
            return 2
        removed = forget(config, args.name, all=args.all)
        if not removed:
            print("nada a apagar", file=sys.stderr)
            return 3
        print(f"apagado: {', '.join(removed)}")
        return 0
    print("uso: ata voices enroll|list|forget", file=sys.stderr)
    return 2


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("voices", help="impressões de voz (opt-in) para reconhecer quem fala")
    vs = p.add_subparsers(dest="voices_cmd", metavar="ação")
    e = vs.add_parser("enroll", help="cadastra a voz de um rótulo de uma reunião")
    e.add_argument("name", metavar="nome")
    e.add_argument("target", metavar="alvo", help="pasta da reunião, nome ou latest")
    e.add_argument("label", metavar="rótulo", help='ex.: "Pessoa 2"')
    lst = vs.add_parser("list", help="lista os cadastros (sem os vetores)")
    lst.add_argument("--json", action="store_true")
    f = vs.add_parser("forget", help="apaga um cadastro (ou todos)")
    f.add_argument("name", metavar="nome", nargs="?")
    f.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_voices)
