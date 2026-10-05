"""Leitor de compatibilidade: ``meta.json`` de bundles CrunchLog (``bundle_version: 2``) -> ``BundleMeta``.

Só lê o FORMATO DE DADOS (documentado em pesquisa/ANALISE-CRUNCHLOG.md §2.2); converte em memória e nunca
reescreve o meta original. Leitura estrita como ``bundle.meta_from_json``: tipo errado -> ``BundleError`` com o
caminho do campo, sem ecoar o valor. Chaves desconhecidas são ignoradas (o formato é aditivo).

Mapeamento:
- sem campo de idioma no CrunchLog -> ``language_requested = "en"``;
- ``start_measured`` não existe: o gravador do CrunchLog mede as duas faixas no mesmo relógio, então
  ``start_epoch`` presente = medido;
- ``dropped_frames > 0`` em alguma faixa -> motivo ``dropped_frames``;
- ``speakers`` (total, incluindo você) -> ``speakers_hint``;
- ``source_schema = "crunchlog/2"``.
"""

from __future__ import annotations

from typing import Any

from ..bundle import DAMAGE_REASONS, BundleError, BundleMeta, Track, _expect
from ..types import TRACKS

SOURCE_SCHEMA = "crunchlog/2"


def _track(d: Any, where: str) -> tuple[Track, bool]:
    _expect(d, dict, where)
    rate = _expect(d.get("sample_rate", 16000), int, f"{where}.sample_rate")
    if not 0 < rate <= 384000:
        raise BundleError(f"{where}.sample_rate fora do intervalo")
    samples = _expect(d.get("samples"), int, f"{where}.samples", optional=True)
    if samples is not None and samples < 0:
        raise BundleError(f"{where}.samples negativo")
    epoch = _expect(d.get("start_epoch"), (int, float), f"{where}.start_epoch", optional=True)
    dropped = _expect(d.get("dropped_frames", 0), int, f"{where}.dropped_frames", optional=True) or 0
    if dropped < 0:
        raise BundleError(f"{where}.dropped_frames negativo")
    track = Track(file=_expect(d.get("file"), str, f"{where}.file"),
                  device=_expect(d.get("device", ""), str, f"{where}.device", optional=True) or "",
                  start_epoch=None if epoch is None else float(epoch),
                  start_measured=epoch is not None,
                  sample_rate=rate, samples=samples,
                  silent=_expect(d.get("silent"), bool, f"{where}.silent", optional=True))
    return track, dropped > 0


def meta_from_crunchlog(d: Any) -> BundleMeta:
    _expect(d, dict, "meta")
    version = _expect(d.get("bundle_version"), int, "meta.bundle_version")
    if version != 2:
        raise BundleError(f"meta.bundle_version: esperado 2, veio {version}")
    tracks_raw = _expect(d.get("tracks", {}), dict, "meta.tracks")
    tracks: dict[str, Track] = {}
    reasons: list[str] = []
    for name, td in tracks_raw.items():
        if name not in TRACKS:
            raise BundleError(f"meta.tracks: faixa desconhecida {name!r}")
        if td is None:
            continue
        tracks[name], dropped = _track(td, f"meta.tracks.{name}")
        if dropped and "dropped_frames" not in reasons:
            reasons.append("dropped_frames")
    raw_reasons = _expect(d.get("damage_reasons", []), list, "meta.damage_reasons")
    for i, r in enumerate(raw_reasons):
        _expect(r, str, f"meta.damage_reasons[{i}]")
        # o vocabulário do CrunchLog é texto livre; só aproveitamos o que casa com o nosso
        if r in DAMAGE_REASONS and r not in reasons:
            reasons.append(r)
    _expect(d.get("damaged", False), bool, "meta.damaged", optional=True)
    recorder = _expect(d.get("recorder", {}), dict, "meta.recorder", optional=True) or {}
    for k, v in recorder.items():
        _expect(v, str, f"meta.recorder.{k}")
    speakers = _expect(d.get("speakers"), int, "meta.speakers", optional=True)
    return BundleMeta(
        name=_expect(d.get("name"), str, "meta.name"),
        created_at=_expect(d.get("created_at"), str, "meta.created_at"),
        tracks=tracks,
        slug=_expect(d.get("slug"), str, "meta.slug", optional=True),
        title=_expect(d.get("title"), str, "meta.title", optional=True),
        stopped_at=_expect(d.get("stopped_at"), str, "meta.stopped_at", optional=True),
        host=_expect(d.get("host", ""), str, "meta.host", optional=True) or "",
        platform=_expect(d.get("platform", ""), str, "meta.platform", optional=True) or "",
        recorder={str(k): v for k, v in recorder.items()},
        language_requested="en",
        language_detected=None,
        speakers_hint=speakers if speakers and speakers > 0 else None,
        engines={},
        damage_reasons=tuple(reasons),
        source_schema=SOURCE_SCHEMA,
    )
