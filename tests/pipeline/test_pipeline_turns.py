from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ata.pipeline.turns import (
    FLIP_MAX_S,
    MAX_GAP_S,
    assign_speaker,
    build_turns,
    first_appearance_labels,
    smooth_turns,
    speaker_stats,
)
from ata.types import Span, Turn, Word

PROPS = settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])


def words_at(*items):
    return [Word(t, a, b) for t, a, b in items]


def test_first_appearance_labels_order():
    spans = [Span(5, 6, "S1"), Span(1, 2, "S3"), Span(7, 8, "S0"), Span(9, 10, "S1")]
    assert first_appearance_labels(spans, "pt-BR") == {"S3": "Pessoa 2", "S1": "Pessoa 3", "S0": "Pessoa 4"}
    assert first_appearance_labels(spans, "en")["S3"] == "Speaker 2"
    assert first_appearance_labels(spans, "es")["S0"] == "Persona 4"


def test_assign_by_overlap_then_nearest():
    spans = [Span(0, 1, "S0"), Span(1, 2, "S1")]
    assert assign_speaker(Word("x", 0.8, 1.6), spans) == "S1"
    assert assign_speaker(Word("x", 2.5, 2.7), spans) == "S1"
    assert assign_speaker(Word("x", 9.0, 9.2), spans) is None


def test_mic_is_me_and_far_is_labeled():
    far = words_at(("oi", 0.0, 0.3), ("tudo", 0.4, 0.7))
    mic = words_at(("bom", 1.0, 1.3), ("dia", 1.4, 1.7))
    turns = build_turns(far, [Span(0, 0.8, "S0")], mic, {"S0": "Pessoa 2"}, "pt-BR")
    assert [(t.speaker, t.text, t.track) for t in turns] == [("Pessoa 2", "oi tudo", "far"),
                                                             ("Eu", "bom dia", "mic")]


def test_split_on_long_gap():
    far = words_at(("a", 0.0, 0.2), ("b", 0.3, 0.5), ("c", 0.5 + MAX_GAP_S + 0.1, 2.5))
    turns = build_turns(far, [Span(0, 3, "S0")], [], {"S0": "Pessoa 2"}, "pt-BR")
    assert [t.text for t in turns] == ["a b", "c"]


def test_tiny_flip_is_smoothed():
    far = words_at(("um", 0.0, 0.4), ("dois", 0.5, 0.9), ("tres", 1.0, 1.3), ("quatro", 1.4, 1.8))
    spans = [Span(0, 0.95, "S0"), Span(0.95, 1.35, "S1"), Span(1.35, 2.0, "S0")]
    turns = build_turns(far, spans, [], {"S0": "Pessoa 2", "S1": "Pessoa 3"}, "pt-BR")
    assert [(t.speaker, t.text) for t in turns] == [("Pessoa 2", "um dois tres quatro")]


def test_long_flip_is_kept():
    far = words_at(("um", 0.0, 0.4), ("dois", 0.5, 1.5), ("tres", 1.6, 2.0))
    spans = [Span(0, 0.45, "S0"), Span(0.45, 1.55, "S1"), Span(1.55, 2.0, "S0")]
    turns = build_turns(far, spans, [], {"S0": "Pessoa 2", "S1": "Pessoa 3"}, "pt-BR")
    assert [t.speaker for t in turns] == ["Pessoa 2", "Pessoa 3", "Pessoa 2"]


def test_no_spans_defaults_to_person_2():
    turns = build_turns(words_at(("x", 0, 0.3)), [], [], {}, "en")
    assert turns[0].speaker == "Speaker 2"


def test_mic_diarization_labels():
    mic = words_at(("eu", 0.0, 0.3), ("tu", 3.0, 3.3))
    spans = [Span(0, 1, "S0"), Span(2.5, 3.5, "S1")]
    turns = build_turns([], [], mic, {"mic:S0": "Eu", "mic:S1": "Pessoa 3"}, "pt-BR", mic_spans=spans)
    assert [t.speaker for t in turns] == ["Eu", "Pessoa 3"]


def test_speaker_stats():
    turns = [Turn(0, 2, "Eu", "a", "mic"), Turn(3, 4, "Pessoa 2", "b c", "far"), Turn(5, 6, "Eu", "d", "mic")]
    s = speaker_stats(turns)
    assert s["Eu"]["seconds"] == 3.0 and s["Eu"]["turns"] == 2 and s["Pessoa 2"]["words"] == 2


def test_smooth_does_not_touch_long_middle():
    a = Turn(0, 1, "A", "", "far", [Word("a", 0, 1)])
    b = Turn(1, 1 + FLIP_MAX_S + 0.1, "B", "", "far", [Word("b", 1, 1 + FLIP_MAX_S + 0.1)])
    c = Turn(2, 3, "A", "", "far", [Word("c", 2, 3)])
    assert [t.speaker for t in smooth_turns([a, b, c])] == ["A", "B", "A"]


_word_lists = st.lists(st.tuples(st.floats(0, 60), st.floats(0.05, 1.0), st.sampled_from(["S0", "S1", "S2"])),
                       max_size=40)


def _mk(items):
    words = [Word(f"w{i}", a, a + d) for i, (a, d, _) in enumerate(items)]
    spans = [Span(a, a + d, s) for a, d, s in items]
    return words, spans


@PROPS
@given(_word_lists, _word_lists)
def test_prop_every_word_lands_in_exactly_one_turn(far_items, mic_items):
    far, spans = _mk(far_items)
    mic = [Word(f"m{i}", a, a + d) for i, (a, d, _) in enumerate(mic_items)]
    labels = first_appearance_labels(spans, "pt-BR")
    turns = build_turns(far, spans, mic, labels, "pt-BR")
    got = sorted((w.text, w.start) for t in turns for w in t.words)
    assert got == sorted((w.text, w.start) for w in far + mic)
    assert all(t.track == "mic" and t.speaker == "Eu" for t in turns if any(w.text.startswith("m") for w in t.words))


@PROPS
@given(_word_lists)
def test_prop_turns_sorted_and_bounded(items):
    far, spans = _mk(items)
    turns = build_turns(far, spans, [], first_appearance_labels(spans, "pt-BR"), "pt-BR")
    assert [t.start for t in turns] == sorted(t.start for t in turns)
    for t in turns:
        assert t.start <= t.end
        assert t.start == min(w.start for w in t.words) and t.end == max(w.end for w in t.words)
        assert t.text == " ".join(w.text for w in t.words)


@PROPS
@given(_word_lists)
def test_prop_no_tiny_sandwiched_flip_left(items):
    far, spans = _mk(items)
    turns = [t for t in build_turns(far, spans, [], first_appearance_labels(spans, "pt-BR"), "pt-BR")]
    for a, b, c in zip(turns, turns[1:], turns[2:]):
        # um turno curto entre dois da mesma pessoa só sobra se as pausas forem grandes (turnos separados)
        if a.speaker == c.speaker != b.speaker and b.end - b.start < FLIP_MAX_S:
            assert b.start - a.end > MAX_GAP_S or c.start - b.end > MAX_GAP_S or a.end > b.start
