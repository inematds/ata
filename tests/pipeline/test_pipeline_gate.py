import numpy as np
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ata import audio
from ata.pipeline.gate import estimate_leak_db, gate_mic_words
from ata.types import Word

SR = audio.SAMPLE_RATE
PROPS = settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])


def tone(seconds, freq=220.0, amp=0.25):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def place(n, segs):
    x = np.zeros(n, np.float32)
    for start, sig in segs:
        a = int(start * SR)
        x[a:a + len(sig)] += sig[: max(0, n - a)]
    return x


def to16(x):
    return audio.to_int16(x)


def scene(leak_db=-18.0):
    """far fala 1..3 s; você fala 4..5 s; o mic ouve o far atenuado."""
    n = 6 * SR
    far = place(n, [(1.0, tone(2.0, 220))])
    mine = place(n, [(4.0, tone(1.0, 140))])
    mic = mine + far * 10 ** (leak_db / 20)
    return to16(far), to16(mic)


def test_echo_dropped_own_kept():
    far, mic = scene()
    words = [Word("eco", 1.5, 2.0), Word("meu", 4.2, 4.6)]
    kept, dropped = gate_mic_words(words, far, mic, 3.0)
    assert [w.text for w in kept] == ["meu"] and [w.text for w in dropped] == ["eco"]


def test_far_silent_keeps_everything():
    n = 3 * SR
    mic = to16(place(n, [(0.5, tone(1.0))]))
    words = [Word("a", 0.5, 0.9), Word("b", 2.0, 2.5)]
    kept, dropped = gate_mic_words(words, np.zeros(n, np.int16), mic, 3.0)
    assert kept == words and dropped == []


def test_missing_far_audio_keeps_everything():
    words = [Word("a", 0.0, 0.5)]
    assert gate_mic_words(words, None, None, 3.0) == (words, [])


def test_window_where_far_silent_is_kept_even_if_mic_quiet():
    far, mic = scene()
    w = Word("x", 5.5, 5.9)   # nada em lugar nenhum: sem -inf - -inf = nan
    kept, _ = gate_mic_words([w], far, mic, 3.0)
    assert kept == [w]


def test_estimate_leak_matches_bleed():
    far, mic = scene(-18.0)
    leak = estimate_leak_db(far, mic)
    assert leak is not None and abs(leak + 18.0) < 1.0


def test_estimate_leak_none_without_far_activity():
    assert estimate_leak_db(np.zeros(SR, np.int16), np.zeros(SR, np.int16)) is None


def test_own_voice_over_louder_far_survives():
    """Você fala junto com alguém 6 dB mais alto: o gate antigo (far >= mic + 3) jogaria fora; aqui fica."""
    n = 6 * SR
    far = place(n, [(0.5, tone(4.0, 220, 0.4))])
    mine = place(n, [(3.0, tone(1.0, 140, 0.2))])
    mic = to16(mine + far * 10 ** (-18 / 20))
    kept, dropped = gate_mic_words([Word("eco", 1.0, 1.5), Word("meu", 3.2, 3.7)], to16(far), mic, 3.0)
    assert [w.text for w in kept] == ["meu"] and [w.text for w in dropped] == ["eco"]


def test_offsets_are_honoured():
    """mic começou 0,5 s depois: a palavra no relógio do bundle cai no lugar certo do arquivo."""
    far, mic = scene()
    shifted_mic = mic[int(0.5 * SR):]      # arquivo do mic começa 0,5 s depois do far
    words = [Word("eco", 1.5, 2.0), Word("meu", 4.2, 4.6)]
    kept, dropped = gate_mic_words(words, far, shifted_mic, 3.0, far_offset=0.0, mic_offset=0.5)
    assert [w.text for w in kept] == ["meu"] and [w.text for w in dropped] == ["eco"]


def test_fallback_threshold_without_leak_estimate():
    """Pouco far ativo: sem estimativa, cai só o que tem far >= mic + margem."""
    n = 2 * SR
    far = to16(place(n, [(0.5, tone(0.3, 220))]))     # 0,3 s < mínimo para estimar
    mic = to16(place(n, [(0.5, tone(0.3, 220)) ]) * 10 ** (-18 / 20))
    kept, dropped = gate_mic_words([Word("eco", 0.5, 0.8)], far, mic, 3.0)
    assert dropped and not kept


@PROPS
@given(st.lists(st.tuples(st.floats(0, 5.5), st.floats(0.05, 0.5)), max_size=12), st.floats(0, 20))
def test_prop_partition_preserves_order(spans, margin):
    far, mic = scene()
    words = [Word(f"w{i}", a, a + d) for i, (a, d) in enumerate(spans)]
    kept, dropped = gate_mic_words(words, far, mic, margin)
    assert sorted(kept + dropped, key=words.index) == words
    assert [w for w in words if w in kept] == kept


@PROPS
@given(st.lists(st.tuples(st.floats(0, 5.5), st.floats(0.05, 0.5)), min_size=1, max_size=12), st.floats(0, 20))
def test_prop_far_silent_keeps_all(spans, margin):
    rng = np.random.default_rng(0)
    mic = (rng.standard_normal(6 * SR) * 2000).astype(np.int16)
    words = [Word("w", a, a + d) for a, d in spans]
    kept, dropped = gate_mic_words(words, np.zeros(6 * SR, np.int16), mic, margin)
    assert kept == words and dropped == []


@PROPS
@given(st.lists(st.tuples(st.floats(0, 5.5), st.floats(0.05, 0.5)), max_size=12),
       st.floats(0, 10), st.floats(0, 10))
def test_prop_bigger_margin_never_keeps_more(spans, m1, m2):
    far, mic = scene()
    lo, hi = sorted((m1, m2))
    words = [Word(f"w{i}", a, a + d) for i, (a, d) in enumerate(spans)]
    kept_lo, _ = gate_mic_words(words, far, mic, lo, leak_db=-18.0)
    kept_hi, _ = gate_mic_words(words, far, mic, hi, leak_db=-18.0)
    assert set(kept_hi) <= set(kept_lo)


@PROPS
@given(st.lists(st.tuples(st.floats(1.0, 2.6), st.floats(0.1, 0.4)), min_size=1, max_size=8),
       st.floats(0.5, 10))
def test_prop_identical_tracks_drop_overlapping(spans, margin):
    """mic == far (eco puro, vazamento 0 dB) com margem > 0: tudo onde o far fala cai."""
    far, _ = scene()
    words = [Word("w", a, a + d) for a, d in spans]
    kept, dropped = gate_mic_words(words, far, far.copy(), margin)
    assert kept == [] and len(dropped) == len(words)
