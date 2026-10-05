import json
import os
import stat

import pytest

from ata import voices as V
from ata.engines import registry
from ata.types import Span
from knowledge.helpers import make_meeting, run_cmd


def _spans(bdir):
    rows = json.loads((bdir / "far.wav.fake-spans.json").read_text())
    return {"far": [Span.from_json(r) for r in rows if r["speaker"].startswith("S")]}


LABELS = {"S0": "Pessoa 2", "S1": "Pessoa 3"}


def _on(config):
    return config.with_overrides(**{"voices.enabled": True})


def test_auto_label_disabled_is_noop(config):
    b = make_meeting(config, voices={"S0": "ana"})
    V.enroll(config, "Ana", b, "Pessoa 2")
    assert V.auto_label(b, config, _spans(b), LABELS) == {}


def test_enroll_and_auto_label_across_meetings(config):
    m1 = make_meeting(config, voices={"S0": "ana"})
    res = V.enroll(config, "Ana", m1, "Pessoa 2")
    assert res["n"] == 1 and res["track"] == "far" and res["first"] is True and "centroid" not in res
    m2 = make_meeting(config, title="segunda", voices={"S0": "ana"})
    assert V.auto_label(m2, _on(config), _spans(m2), LABELS) == {"Pessoa 2": "Ana"}


def test_auto_label_nested_labels_shape(config):
    m1 = make_meeting(config, voices={"S1": "bruno"})
    V.enroll(config, "Bruno", m1, "Pessoa 3")
    m2 = make_meeting(config, title="outra", voices={"S1": "bruno"})
    out = V.auto_label(m2, _on(config), _spans(m2), {"far": LABELS})
    assert out == {"Pessoa 3": "Bruno"}


def test_auto_label_below_threshold(config):
    m1 = make_meeting(config, voices={"S0": "ana"})
    V.enroll(config, "Ana", m1, "Pessoa 2")
    m2 = make_meeting(config, title="x", voices={"S0": "carla"})
    assert V.auto_label(m2, _on(config), _spans(m2), LABELS) == {}


def test_auto_label_one_name_per_label(config):
    m1 = make_meeting(config, voices={"S0": "ana"})
    V.enroll(config, "Ana", m1, "Pessoa 2")
    m2 = make_meeting(config, title="x", voices={"S0": "ana", "S1": "ana"})
    out = V.auto_label(m2, _on(config), _spans(m2), LABELS)
    assert list(out.values()) == ["Ana"] and len(out) == 1


def test_auto_label_engine_error_returns_empty(config, monkeypatch):
    m1 = make_meeting(config, voices={"S0": "ana"})
    V.enroll(config, "Ana", m1, "Pessoa 2")

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(registry, "build", boom)
    assert V.auto_label(m1, _on(config), _spans(m1), LABELS) == {}


def test_enroll_twice_updates_centroid(config):
    m1 = make_meeting(config, voices={"S0": "ana"})
    V.enroll(config, "Ana", m1, "Pessoa 2")
    res = V.enroll(config, "Ana", m1, "Pessoa 2")
    assert res["n"] == 2 and res["first"] is False
    c = V.load(config)["Ana"]["centroid"]
    assert abs(sum(x * x for x in c) - 1.0) < 1e-3


def test_enroll_by_given_name(config):
    m1 = make_meeting(config, voices={"S0": "ana"}, names={"Pessoa 2": "Ana"})
    assert V.enroll(config, "Ana", m1, "Ana")["track"] == "far"


def test_enroll_errors(config):
    m1 = make_meeting(config)
    with pytest.raises(V.VoiceError):
        V.enroll(config, "X", m1, "Pessoa 9")
    with pytest.raises(V.VoiceError):
        V.enroll(config, " ", m1, "Pessoa 2")
    (m1 / "turns.json").unlink()
    with pytest.raises(V.VoiceError):
        V.enroll(config, "X", m1, "Pessoa 2")


def test_enroll_subtracts_track_offset(config, monkeypatch):
    m = make_meeting(config, mic_offset=0.5)
    seen = {}

    class Cap:
        name = "cap"

        def embed(self, wav, spans):
            seen["wav"], seen["spans"] = wav, spans
            return {spans[0].speaker: [1.0, 0.0, 0.0]}
    monkeypatch.setattr(registry, "build", lambda role, ref, cfg: Cap())
    from ata import bundle
    first_mic = [t for t in bundle.read_turns(m) if t.track == "mic"][0]
    V.enroll(config, "Eu mesmo", m, "Eu")
    assert seen["wav"].name == "mic.wav"
    assert seen["spans"][0].start == pytest.approx(first_mic.start - 0.5)


def test_list_hides_vectors_and_file_private(config):
    m1 = make_meeting(config, voices={"S0": "ana"})
    V.enroll(config, "Ana", m1, "Pessoa 2")
    rows = V.list_voices(config)
    assert rows == [{"name": "Ana", "n": 1, "track": "far", "model": "fake", "updated_at": rows[0]["updated_at"]}]
    mode = stat.S_IMODE(os.stat(V.voices_path(config)).st_mode)
    assert mode == 0o600


def test_forget(config):
    m1 = make_meeting(config, voices={"S0": "ana", "S1": "bruno"})
    V.enroll(config, "Ana", m1, "Pessoa 2")
    V.enroll(config, "Bruno", m1, "Pessoa 3")
    assert V.forget(config, "Ana") == ["Ana"]
    assert [r["name"] for r in V.list_voices(config)] == ["Bruno"]
    assert V.forget(config, "Ninguém") == []
    assert V.forget(config, all=True) == ["Bruno"]
    assert not V.voices_path(config).exists()


def test_voices_commands(config, capsys):
    m1 = make_meeting(config, voices={"S0": "ana"})
    assert run_cmd(V, ["voices", "enroll", "Ana", str(m1), "Pessoa 2"], config) == 0
    err = capsys.readouterr().err
    assert "LGPD" in err and "enabled = false" in err
    assert run_cmd(V, ["voices", "enroll", "Ana", m1.name, "Pessoa 2"], config) == 0
    assert "LGPD" not in capsys.readouterr().err          # aviso só no primeiro cadastro
    assert run_cmd(V, ["voices", "list", "--json"], config) == 0
    out = capsys.readouterr().out
    assert "centroid" not in out and json.loads(out)[0]["n"] == 2
    assert run_cmd(V, ["voices", "enroll", "Ana", "nao-existe", "Pessoa 2"], config) == 2
    assert run_cmd(V, ["voices", "forget"], config) == 2
    assert run_cmd(V, ["voices", "forget", "--all"], config) == 0
    assert run_cmd(V, ["voices", "list"], config) == 3


def test_auto_label_bundle_clock_and_mic_prefix(config, monkeypatch):
    """Como o pipeline chama: spans no relógio do bundle, rótulos do mic com prefixo 'mic:'."""
    m = make_meeting(config, mic_offset=0.5)
    V.save(config, {"Nei": {"centroid": [1.0, 0.0, 0.0], "n": 1, "track": "mic", "model": "cap",
                            "updated_at": "2026-10-05T00:00:00-03:00"}})
    seen = {}

    class Cap:
        name = "cap"

        def embed(self, wav, spans):
            seen[wav.name] = spans
            return {s.speaker: ([1.0, 0.0, 0.0] if wav.name == "mic.wav" else [0.0, 1.0, 0.0]) for s in spans}
    monkeypatch.setattr(registry, "build", lambda role, ref, cfg: Cap())
    spans = {"far": [Span(2.0, 4.0, "S0")], "mic": [Span(1.5, 3.0, "S0")]}
    out = V.auto_label(m, _on(config), spans, {"S0": "Pessoa 2", "mic:S0": "Eu"})
    assert out == {"Eu": "Nei"}
    assert seen["mic.wav"][0].start == pytest.approx(1.0) and seen["far.wav"][0].start == pytest.approx(2.0)
