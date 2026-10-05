import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ata import audio, bundle
from ata.capture.base import CaptureError, CaptureMissing
from ata.capture.linux import PipeWireBackend, build_argv, parse_inspect

FAKE_PW = Path(__file__).with_name("fake_pw_record.py")

INSPECT_SINK = """id 52, type PipeWire:Interface:Node
    alsa.card = "0"
  * media.class = "Audio/Sink"
  * node.description = "Saída analógica"
  * node.name = "alsa_output.pci-0000_00_1f.3.analog-stereo"
    node.nick = "ALC897"
"""
INSPECT_SOURCE = INSPECT_SINK.replace("Audio/Sink", "Audio/Source").replace(
    "alsa_output.pci-0000_00_1f.3.analog-stereo", "alsa_input.usb-mic").replace("Saída", "Entrada")


def fake_runner(calls=None, *, missing=False, code=0):
    def run(argv, **kw):
        if calls is not None:
            calls.append(argv)
        if missing:
            raise FileNotFoundError(argv[0])
        out = INSPECT_SINK if "SINK" in argv[-1] else INSPECT_SOURCE
        return subprocess.CompletedProcess(argv, code, out, "")
    return run


def backend(config=None, **kw):
    return PipeWireBackend(config, pw_record=(sys.executable, str(FAKE_PW)), runner=fake_runner(),
                           startup_check_s=kw.pop("startup_check_s", 0.4), **kw)


def plan(monkeypatch, **p):
    monkeypatch.setenv("FAKE_PW_PLAN", json.dumps(p))


def test_parse_inspect():
    info = parse_inspect(INSPECT_SINK)
    assert info["node.name"] == "alsa_output.pci-0000_00_1f.3.analog-stereo"
    assert info["node.description"] == "Saída analógica" and info["media.class"] == "Audio/Sink"


def test_inspect_errors():
    b = PipeWireBackend(runner=fake_runner(missing=True))
    with pytest.raises(CaptureMissing, match="wpctl"):
        b.inspect("@DEFAULT_AUDIO_SINK@")
    b = PipeWireBackend(runner=fake_runner(code=1))
    with pytest.raises(CaptureError, match="PipeWire"):
        b.inspect("@DEFAULT_AUDIO_SINK@")
    b = PipeWireBackend(runner=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "nada aqui", ""))
    with pytest.raises(CaptureError, match="node.name"):
        b.inspect("@DEFAULT_AUDIO_SINK@")


def test_build_argv_monitor_only_on_far(tmp_path):
    far = build_argv(("pw-record",), "sink", tmp_path / "far.wav", monitor=True)
    mic = build_argv(("pw-record",), "src", tmp_path / "mic.wav", monitor=False)
    assert far[:9] == ["pw-record", "--target", "sink", "--rate", "16000", "--channels", "1", "--format", "s16"]
    assert far[9:11] == ["-P", "stream.capture.sink=true"] and far[-1].endswith("far.wav")
    assert "-P" not in mic and mic[2] == "src"


def test_resolve_precedence(config, monkeypatch):
    calls = []
    b = PipeWireBackend(config.with_overrides(**{"audio.mic_device": "mic-config"}), runner=fake_runner(calls))
    assert b.resolve("far") == ("alsa_output.pci-0000_00_1f.3.analog-stereo", "Saída analógica", "default")
    assert b.resolve("mic")[::2] == ("mic-config", "config")
    monkeypatch.setenv("ATA_MIC_TARGET", "mic-env")
    assert b.resolve("mic")[::2] == ("mic-env", "env")
    assert b.resolve("mic", "explicito")[::2] == ("explicito", "arg")
    assert calls == [["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"]]


def test_start_stop_measures_lag(tmp_path, monkeypatch):
    plan(monkeypatch, mic="bleed", mic_delay=0.15, argv_dir=str(tmp_path))
    bdir = tmp_path / "b"
    bdir.mkdir()
    h = backend().start(bdir)
    assert h.poll() is None
    assert set(h.preview()) == {"far", "mic"} and not h.preview()["far"].start_measured
    tracks = h.stop()
    far_argv = json.loads((tmp_path / "far.json").read_text())
    assert "stream.capture.sink=true" in far_argv and "alsa_output.pci-0000_00_1f.3.analog-stereo" in far_argv
    assert tracks["far"].samples == tracks["mic"].samples == 48000
    assert len(audio.read_wav(bdir / "far.wav")) == 48000          # cabeçalho consertado
    assert tracks["far"].start_measured and tracks["mic"].start_measured
    assert h.lag == pytest.approx(0.15, abs=0.002) and h.lag_strength >= 0.2
    meta = bundle.BundleMeta(name="x", created_at="t", tracks=tracks)
    off = bundle.track_offsets(meta)
    assert off["far"] - off["mic"] == pytest.approx(0.15, abs=0.05)
    assert "start_unmeasured" not in bundle.damage_report(meta, bdir)
    assert h.damage_reasons == []


def test_no_leak_means_unmeasured(tmp_path, monkeypatch):
    plan(monkeypatch, mic="own")
    h = backend().start(tmp_path)
    tracks = h.stop()
    assert not tracks["far"].start_measured and not tracks["mic"].start_measured
    meta = bundle.BundleMeta(name="x", created_at="t", tracks=tracks)
    assert "start_unmeasured" in bundle.damage_report(meta, tmp_path)


def test_silent_far(tmp_path, monkeypatch):
    plan(monkeypatch, far="silence", mic="own")
    tracks = backend().start(tmp_path).stop()
    assert tracks["far"].silent is True and tracks["mic"].silent is False
    meta = bundle.BundleMeta(name="x", created_at="t", tracks=tracks)
    assert "far_silent" in bundle.damage_report(meta, tmp_path)


def test_start_failure_reports_stderr(tmp_path, monkeypatch):
    plan(monkeypatch, die_mic=True)
    with pytest.raises(CaptureError, match="mic.*falha ao conectar no alvo alsa_input.usb-mic"):
        backend().start(tmp_path)


def test_pw_record_missing(tmp_path, monkeypatch):
    b = PipeWireBackend(pw_record=("/nao/existe/pw-record",), runner=fake_runner())
    with pytest.raises(CaptureMissing, match="pipewire-bin"):
        b.start(tmp_path)


def test_sigkill_when_sigint_ignored(tmp_path, monkeypatch):
    plan(monkeypatch, mode="ignore_sigint")
    h = backend(stop_timeout_s=0.5).start(tmp_path)
    tracks = h.stop()
    assert "recorder_killed" in h.damage_reasons
    assert tracks["far"].samples == 48000        # arquivo continua utilizável


def test_poll_detects_dead_track(tmp_path, monkeypatch):
    plan(monkeypatch)
    h = backend().start(tmp_path)
    h.procs["mic"].kill()
    h.procs["mic"].wait()
    assert "mic" in (h.poll() or "")
    h.stop()
    assert "recorder_killed" in h.damage_reasons


def test_devices_listing(config):
    d = PipeWireBackend(config, pw_record=(sys.executable,), runner=fake_runner()).devices()
    assert d["available"] is True and d["backend"] == "linux-pipewire"
    assert d["far"]["id"].startswith("alsa_output") and d["mic"]["id"] == "alsa_input.usb-mic"
    d = PipeWireBackend(config, pw_record=("/nao/existe",), runner=fake_runner(missing=True)).devices()
    assert d["available"] is False and any("wpctl" in p for p in d["problems"])


@pytest.mark.linux_audio
@pytest.mark.skipif(not shutil.which("pw-record"), reason="sem pw-record")
def test_real_pipewire_short_recording(tmp_path):
    import time
    h = PipeWireBackend().start(tmp_path)
    time.sleep(1.5)
    tracks = h.stop()
    assert set(tracks) == {"far", "mic"}
    assert all(t.samples and t.samples > 8000 for t in tracks.values())
