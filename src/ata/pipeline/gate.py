"""Gate de eco: tira do mic as palavras que eram só o som da caixa (far) vazando no microfone.

Regra (uma frase): uma palavra do mic fica quando, na janela da palavra, a energia do mic supera a do far por
pelo menos ``margin_db - vazamento`` dB, onde o vazamento é a atenuação típica far->mic (ex.: 18 dB). Far
silencioso na janela (ou na faixa toda) -> fica. Assim só cai o que tem o nível exato de eco; a sua voz por cima
de alguém mais alto continua (fraqueza do gate "far >= mic + margem").

Puro: recebe arrays int16 em TEMPO DO ARQUIVO e palavras no RELÓGIO DO BUNDLE, com os offsets por kwarg.
"""

from __future__ import annotations

import math

import numpy as np

from .. import audio
from ..types import Word

SILENCE_DB = -60.0          # abaixo disso a janela do far é silêncio
ACTIVE_DB = -45.0           # bloco do far "falando" para estimar o vazamento
MIN_ACTIVE_BLOCKS = 25      # 0,5 s de far ativo; menos que isso não estima
LEAK_PERCENTILE = 20.0
BLOCK = 320                 # 20 ms


def _db(x: np.ndarray, a: int, b: int) -> float:
    return audio.rms_db(x[max(0, a):max(0, b)])


def estimate_leak_db(far_audio: np.ndarray, mic_audio: np.ndarray, *, far_offset: float = 0.0,
                     mic_offset: float = 0.0, rate: int = audio.SAMPLE_RATE) -> float | None:
    """Ganho típico do far no mic (dB, <= 0) nos blocos em que o far fala; None se não dá para estimar."""
    far = np.asarray(far_audio)
    mic = np.asarray(mic_audio)
    if len(far) == 0 or len(mic) == 0:
        return None
    # amostra i do far <-> amostra i - shift do mic (ambos no relógio do bundle)
    shift = int(round((mic_offset - far_offset) * rate))
    start = max(0, shift)
    end = min(len(far), len(mic) + shift)
    n = (end - start) // BLOCK
    if n <= 0:
        return None
    f = audio.to_float(far[start:start + n * BLOCK]).astype(np.float64).reshape(n, BLOCK)
    m = audio.to_float(mic[start - shift:start - shift + n * BLOCK]).astype(np.float64).reshape(n, BLOCK)
    with np.errstate(divide="ignore"):
        fdb = 10.0 * np.log10(np.mean(f * f, axis=1))
        mdb = 10.0 * np.log10(np.mean(m * m, axis=1))
    active = fdb >= ACTIVE_DB
    if int(active.sum()) < MIN_ACTIVE_BLOCKS:
        return None
    diffs = np.maximum(mdb[active], -90.0) - fdb[active]
    return float(min(0.0, max(-60.0, np.percentile(diffs, LEAK_PERCENTILE))))


def gate_mic_words(mic_words: list[Word], far_audio: np.ndarray | None, mic_audio: np.ndarray | None,
                   margin_db: float, *, far_offset: float = 0.0, mic_offset: float = 0.0,
                   leak_db: float | None = None, rate: int = audio.SAMPLE_RATE) -> tuple[list[Word], list[Word]]:
    """Separa as palavras do mic em (mantidas, descartadas), preservando a ordem.

    ``leak_db``: ganho far->mic em dB (<= 0, ex.: -18). None = estima do áudio; sem estimativa, usa
    ``-2 * margin_db`` (equivale a descartar só quando o far é ``margin_db`` mais alto que o mic).
    """
    words = list(mic_words)
    if far_audio is None or mic_audio is None or len(far_audio) == 0 or not words:
        return words, []
    far = np.asarray(far_audio)
    mic = np.asarray(mic_audio)
    if audio.rms_db(far) < SILENCE_DB:
        return words, []
    if leak_db is None:
        leak_db = estimate_leak_db(far, mic, far_offset=far_offset, mic_offset=mic_offset, rate=rate)
    if leak_db is None:
        leak_db = -2.0 * float(margin_db)
    threshold = float(margin_db) + min(0.0, float(leak_db))
    kept: list[Word] = []
    dropped: list[Word] = []
    for w in words:
        fdb = _db(far, int((w.start - far_offset) * rate), int((w.end - far_offset) * rate))
        if not fdb > SILENCE_DB:          # inclui -inf e janela fora do arquivo
            kept.append(w)
            continue
        mdb = _db(mic, int((w.start - mic_offset) * rate), int((w.end - mic_offset) * rate))
        if math.isinf(mdb):
            mdb = -120.0
        (kept if mdb - fdb >= threshold else dropped).append(w)
    return kept, dropped
