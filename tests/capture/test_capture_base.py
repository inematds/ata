import numpy as np
import pytest

from ata import audio, bundle
from ata.capture import base
from ata.capture.base import UnsupportedPlatform, backend_for_platform


def test_backend_fake_from_env(monkeypatch):
    monkeypatch.setenv("ATA_CAPTURE", "fake")
    b = backend_for_platform("linux")
    assert b.name == "fake" and isinstance(b, base.CaptureBackend)


def test_backend_by_platform(monkeypatch):
    monkeypatch.delenv("ATA_CAPTURE", raising=False)
    assert backend_for_platform("linux").name == "linux-pipewire"
    assert backend_for_platform("win32").name == "windows-wasapi"
    assert backend_for_platform("darwin").name == "macos-coreaudio-tap"


def test_backend_forced_by_env(monkeypatch):
    monkeypatch.setenv("ATA_CAPTURE", "windows")
    assert backend_for_platform("linux").name == "windows-wasapi"


def test_backend_unsupported(monkeypatch):
    monkeypatch.delenv("ATA_CAPTURE", raising=False)
    with pytest.raises(UnsupportedPlatform):
        backend_for_platform("sunos5")
    monkeypatch.setenv("ATA_CAPTURE", "banana")
    with pytest.raises(UnsupportedPlatform, match="ATA_CAPTURE"):
        backend_for_platform("linux")


def test_unsupported_is_missing_dependency():
    # CLI mapeia CaptureMissing -> código 4
    assert issubclass(UnsupportedPlatform, base.CaptureMissing)
    assert issubclass(base.CaptureMissing, base.CaptureError)


def test_configured_target_precedence(config, monkeypatch):
    assert base.configured_target(config, "far") == (None, "default")
    cfg = config.with_overrides(**{"audio.far_device": "sink-da-config"})
    assert base.configured_target(cfg, "far") == ("sink-da-config", "config")
    monkeypatch.setenv("ATA_FAR_TARGET", "sink-do-env")
    assert base.configured_target(cfg, "far") == ("sink-do-env", "env")
    assert base.configured_target(cfg, "mic") == (None, "default")


def test_scan_wav_silence_and_sound(tmp_path):
    audio.write_wav(tmp_path / "s.wav", np.zeros(16000, np.int16))
    t = np.arange(16000) / 16000
    audio.write_wav(tmp_path / "v.wav", 0.3 * np.sin(2 * np.pi * 200 * t))
    assert base.scan_wav(tmp_path / "s.wav") == (16000, True)
    assert base.scan_wav(tmp_path / "v.wav") == (16000, False)
    assert base.scan_wav(tmp_path / "nao-existe.wav") == (0, True)


def test_finalize_track_repairs_header(tmp_path):
    p = audio.write_wav(tmp_path / "far.wav", 0.2 * np.ones(8000, np.float32))
    raw = bytearray(p.read_bytes())
    raw[4:8] = b"\0\0\0\0"
    raw[40:44] = b"\0\0\0\0"
    p.write_bytes(bytes(raw))
    tr = base.finalize_track(tmp_path, "far.wav", device="d", start_epoch=1.0, start_measured=False)
    assert tr == bundle.Track("far.wav", "d", 1.0, False, 16000, 8000, False)
    assert len(audio.read_wav(p)) == 8000
    assert base.finalize_track(tmp_path, "mic.wav", device="d", start_epoch=1.0, start_measured=False) is None


def test_finalize_force_silent(tmp_path):
    audio.write_wav(tmp_path / "far.wav", 0.2 * np.ones(8000, np.float32))
    tr = base.finalize_track(tmp_path, "far.wav", device="", start_epoch=None, start_measured=False,
                             force_silent=True)
    assert tr.silent is True


def test_clean_reasons_closed_vocabulary():
    assert base.clean_reasons(["recorder_killed", "permission_denied", "recorder_killed", "far_silent"]) == [
        "recorder_killed", "far_silent"]


def test_find_first_sound_beyond_first_window(tmp_path):
    x = np.zeros(16000 * 25, np.float32)
    x[16000 * 21:16000 * 22] = 0.3
    audio.write_wav(tmp_path / "a.wav", x)
    assert base.find_first_sound(tmp_path / "a.wav") == pytest.approx(21.0, abs=0.02)
    audio.write_wav(tmp_path / "z.wav", np.zeros(16000, np.int16))
    assert base.find_first_sound(tmp_path / "z.wav") is None
    assert len(base.read_window(tmp_path / "a.wav", 24.5, 10)) == 8000
