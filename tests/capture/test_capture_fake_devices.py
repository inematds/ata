import json

import pytest

from ata import audio, bundle
from ata.capture import devices
from ata.capture.base import CaptureError
from ata.capture.fake import FakeBackend


def test_fake_writes_tracks_and_fixtures(tmp_path):
    h = FakeBackend().start(tmp_path)
    assert (tmp_path / "far.wav.fake-words.json").is_file() and (tmp_path / "mic.wav.fake-spans.json").is_file()
    tracks = h.stop()
    assert tracks["far"].samples == tracks["mic"].samples > 16000
    assert tracks["far"].start_measured and not tracks["far"].silent
    meta = bundle.BundleMeta(name="x", created_at="t", tracks=tracks)
    assert bundle.damage_report(meta, tmp_path) == []
    words = json.loads((tmp_path / "far.wav.fake-words.json").read_text())
    assert words and all({"text", "start", "end"} <= set(w) for w in words)


def test_fake_silent_far(tmp_path):
    tracks = FakeBackend("silent_far").start(tmp_path).stop()
    assert tracks["far"].silent and not (tmp_path / "far.wav.fake-words.json").exists()
    assert audio.is_silent(audio.read_wav(tmp_path / "far.wav"))


def test_fake_unmeasured_and_fail(tmp_path):
    tracks = FakeBackend("unmeasured").start(tmp_path).stop()
    assert not tracks["mic"].start_measured
    with pytest.raises(CaptureError):
        FakeBackend("fail").start(tmp_path)
    with pytest.raises(ValueError):
        FakeBackend("xyz")


def test_fake_mode_from_env(monkeypatch):
    monkeypatch.setenv("ATA_FAKE_CAPTURE", "silent_far")
    assert FakeBackend.from_env().mode == "silent_far"


def test_list_devices_fake(config, monkeypatch):
    monkeypatch.setenv("ATA_CAPTURE", "fake")
    d = devices.list_devices(config)
    assert d["available"] and d["backend"] == "fake" and d["far"]["id"] == "fake-far"
    assert set(d) >= {"backend", "platform", "available", "far", "mic", "problems"}


def test_list_devices_never_raises(config, monkeypatch):
    monkeypatch.delenv("ATA_CAPTURE", raising=False)
    d = devices.list_devices(config, platform="sunos5")
    assert d["available"] is False and d["problems"] and d["platform"] == "sunos5"

    class Boom:
        name = "boom"

        def devices(self):
            raise RuntimeError("driver")

    monkeypatch.setattr(devices, "backend_for_platform", lambda *a, **k: Boom())
    d = devices.list_devices(config)
    assert d["available"] is False and d["backend"] == "boom" and "RuntimeError" in d["problems"][0]
