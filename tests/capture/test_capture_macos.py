import json
import sys
from pathlib import Path

import pytest

from ata import audio, bundle
from ata.capture.base import CaptureError, UnsupportedPlatform
from ata.capture.macos import HelperState, MacHelperBackend, feed, parse_event

SESSION_OK = [
    '{"event":"started","track":"far","start_epoch":1791234567.120,"device":"System Audio"}',
    '{"event":"started","track":"mic","start_epoch":1791234567.125,"device":"MacBook Pro Microphone"}',
    '{"event":"level","track":"far","db":-23.5}',
    '{"event":"level","track":"mic","db":-31.0}',
    '{"event":"stopped","track":"far","samples":32000}',
    '{"event":"stopped","track":"mic","samples":31990}',
]
SESSION_DENIED = [
    '{"event":"error","track":"far","code":"permission_denied","message":"tap negado"}',
    '{"event":"started","track":"mic","start_epoch":1791234567.5,"device":"Mic"}',
    '{"event":"stopped"}',
]


def test_parse_valid_events():
    ev = parse_event(SESSION_OK[0])
    assert ev.event == "started" and ev.track == "far" and ev.data["start_epoch"] == 1791234567.12
    assert parse_event('{"event":"stopped"}').track is None
    assert parse_event('{"event":"devices","far":{"id":"tap"},"mic":{"id":"m"}}').data["far"] == {"id": "tap"}


@pytest.mark.parametrize("line", ["", "   ", "not json", "[1,2]", '{"event":"dance"}',
                                  '{"event":"level","track":"left","db":1}', '{"track":"far"}'])
def test_parse_garbage_is_ignored(line):
    assert parse_event(line) is None


def test_state_ok_session():
    st = feed(HelperState(), SESSION_OK + ["lixo no stdout"])
    assert st.start_epoch == {"far": 1791234567.12, "mic": 1791234567.125}
    assert st.samples == {"far": 32000, "mic": 31990} and st.stopped == {"far", "mic"}
    assert st.level_db["far"] == -23.5 and st.fatal_error() is None and st.warnings() == []


def test_state_permission_denied_is_not_fatal():
    st = feed(HelperState(), SESSION_DENIED)
    assert st.denied == {"far"} and st.fatal_error() is None
    w = st.warnings()
    assert len(w) == 1 and "far" in w[0] and "Privacidade" in w[0]
    assert st.stopped == {"far", "mic"}


def test_state_fatal_error():
    st = feed(HelperState(), ['{"event":"error","code":"unsupported_os"}'])
    assert st.fatal_error() == "unsupported_os"


def test_state_ignores_bad_types():
    st = feed(HelperState(), ['{"event":"started","track":"far","start_epoch":"ontem"}',
                              '{"event":"level","track":"far","db":true}'])
    assert st.start_epoch == {} and st.level_db == {}


def test_backend_requires_macos(tmp_path):
    if sys.platform == "darwin":
        pytest.skip("só fora do macOS")
    with pytest.raises(UnsupportedPlatform):
        MacHelperBackend().start(tmp_path)
    assert MacHelperBackend().devices()["available"] is False


FAKE_HELPER = r'''
import json, signal, sys, time, wave
args = sys.argv[1:]
mode = __MODE__
def emit(**d):
    print(json.dumps(d), flush=True)
if args[0] == "devices":
    emit(event="devices", far={"id": "tap"}, mic={"id": "mic0"}); sys.exit(0)
far, mic = args[args.index("--far") + 1], args[args.index("--mic") + 1]
if mode == "fatal":
    emit(event="error", code="tap_failed"); sys.exit(3)
stop = False
def on_int(*_):
    global stop; stop = True
signal.signal(signal.SIGINT, on_int)
def write(path, value):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes((int(value).to_bytes(2, "little", signed=True)) * 16000)
if mode == "denied":
    emit(event="error", track="far", code="permission_denied")
else:
    write(far, 3000); emit(event="started", track="far", start_epoch=1000.0, device="tap")
write(mic, 3000); emit(event="started", track="mic", start_epoch=1000.01, device="mic0")
while not stop:
    time.sleep(0.02)
emit(event="stopped")
'''


def helper(tmp_path, mode):
    p = tmp_path / f"helper_{mode}.py"
    p.write_text(FAKE_HELPER.replace("__MODE__", repr(mode)))
    return (sys.executable, str(p))


def test_backend_with_fake_helper_ok(tmp_path):
    b = MacHelperBackend(helper=helper(tmp_path, "ok"))
    bdir = tmp_path / "b"
    bdir.mkdir()
    h = b.start(bdir)
    assert h.poll() is None and h.preview()["far"].start_epoch == 1000.0
    tracks = h.stop()
    assert tracks["far"].start_measured and tracks["mic"].start_epoch == 1000.01
    assert tracks["far"].samples == 16000 and not tracks["far"].silent
    assert h.damage_reasons == [] and b.devices()["far"] == {"id": "tap"}


def test_backend_permission_denied_marks_far_silent(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    h = MacHelperBackend(helper=helper(tmp_path, "denied")).start(bdir)
    tracks = h.stop()
    assert tracks["far"].silent is True and (bdir / "far.wav").is_file()
    assert audio.duration(bdir / "far.wav") == pytest.approx(1.0)
    assert "far_silent" in h.damage_reasons and any("Privacidade" in w for w in h.warnings)
    meta = bundle.BundleMeta(name="x", created_at="t", tracks=tracks)
    assert "far_silent" in bundle.damage_report(meta, bdir)


def test_backend_fatal_error_raises(tmp_path):
    with pytest.raises(CaptureError, match="tap_failed"):
        MacHelperBackend(helper=helper(tmp_path, "fatal")).start(tmp_path)


def test_swift_helper_source_present():
    root = Path(__file__).resolve().parents[2] / "helpers" / "macos"
    src = (root / "AtaAudio.swift").read_text()
    for needle in ("AudioHardwareCreateProcessTap", '"started"', '"permission_denied"', '"stopped"', "16000"):
        assert needle in src
    assert "swiftc" in (root / "README.md").read_text()
    json.loads(SESSION_OK[0])
