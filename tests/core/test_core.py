import json
import numpy as np
import pytest
from ata import audio, bundle, config as cfgmod, i18n
from ata.engines import registry
from ata.engines.fake import FakeAsr, FakeDiarizer, FakeSummarizer


def test_meta_roundtrip(meeting):
    meta = bundle.read_meta(meeting)
    assert meta.language == "pt-BR" and set(meta.tracks) == {"far", "mic"}
    bundle.write_meta(meeting, meta)
    assert bundle.read_meta(meeting) == meta


def test_meta_strict_types(meeting):
    d = json.loads((meeting / "meta.json").read_text())
    d["tracks"]["far"]["sample_rate"] = "16000"
    (meeting / "meta.json").write_text(json.dumps(d))
    with pytest.raises(bundle.BundleError, match="tracks.far.sample_rate"):
        bundle.read_meta(meeting)


def test_damage_report_clean_and_missing(meeting):
    meta = bundle.read_meta(meeting)
    assert bundle.damage_report(meta, meeting) == []
    (meeting / "mic.wav").unlink()
    assert "mic_missing" in bundle.damage_report(meta, meeting)


def test_offsets():
    m = bundle.BundleMeta(name="x", created_at="t", tracks={
        "far": bundle.Track("far.wav", start_epoch=100.0), "mic": bundle.Track("mic.wav", start_epoch=100.25)})
    assert bundle.track_offsets(m) == {"far": 0.0, "mic": 0.25}


def test_config_defaults_and_validation(tmp_path):
    c = cfgmod.load_config(tmp_path / "nada.toml")
    assert c.get("summary.provider") == "ollama" and c.get("language.default") == "pt-BR"
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.write_config({"summary": {"provider": "claude"}}, tmp_path / "c.toml")
    p = cfgmod.write_config({"summary": {"provider": "claude"}, "privacy": {"level": 1}}, tmp_path / "c.toml")
    assert cfgmod.load_config(p).get("summary.provider") == "claude"


def test_i18n():
    assert i18n.normalize("pt") == "pt-BR" and i18n.me_label("es") == "Yo"
    assert i18n.other_label("en", 2) == "Speaker 2"
    with pytest.raises(ValueError):
        i18n.normalize("fr")


def test_estimate_lag():
    rng = np.random.default_rng(1)
    far = (rng.standard_normal(16000 * 5) * 3000).astype(np.int16)
    mic = np.concatenate([np.zeros(4000, np.int16), (far[:-4000] * 0.2).astype(np.int16)])
    lag, strength = audio.estimate_lag(far, mic)
    assert abs(lag - 0.25) < 0.002 and strength > 0.5


def test_fake_engines(meeting, config):
    words = registry.asr_for(config, "pt-BR").transcribe(meeting / "far.wav", "pt-BR")
    assert any(w.text == "Decidimos" for w in words)
    spans = registry.diarizer_for(config).diarize(meeting / "far.wav")
    assert {s.speaker for s in spans} == {"S0", "S1"}
    s = FakeSummarizer().summarize("LANGUAGE: pt-BR\n[00:12] Pessoa 2: Decidimos lançar dia doze\n", {})
    assert s["decisions"][0]["evidence"][0]["t"] == 12.0


def test_wav_silence_and_repair(tmp_path):
    p = audio.write_wav(tmp_path / "s.wav", np.zeros(16000, np.int16))
    assert audio.is_silent(audio.read_wav(p))
    with open(p, "r+b") as f:
        f.seek(40); f.write(b"\x00\x00\x00\x00")
    assert audio.repair_header(p) and len(audio.read_wav(p)) == 16000
