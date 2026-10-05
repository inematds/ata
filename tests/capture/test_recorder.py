import json
import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ata import bundle, recorder
from ata.capture.base import CaptureError
from ata.config import load_config


@pytest.fixture
def fake_capture(monkeypatch):
    monkeypatch.setenv("ATA_CAPTURE", "fake")
    yield
    # nunca deixar supervisor órfão entre testes
    for pid, child in list(recorder._CHILDREN.items()):
        if child.poll() is None:
            child.kill()
        child.wait()
    recorder._CHILDREN.clear()


@pytest.fixture
def pipeline_stub(monkeypatch):
    calls = []

    def process_bundle(bundle_dir, config, **kw):
        calls.append(bundle_dir)
        note = config.notes / f"{bundle_dir.name}.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# nota\n")
        return note

    monkeypatch.setitem(sys.modules, "ata.pipeline.run", SimpleNamespace(process_bundle=process_bundle))
    return calls


def cli(config, *argv):
    """Como `ata.cli.main`, mas só com os comandos do gravador (as outras partes podem ainda não existir)."""
    import argparse
    parser = argparse.ArgumentParser(prog="ata")
    recorder.add_parser(parser.add_subparsers(dest="command"))
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    return int(args.func(args, load_config(config.source)) or 0)


def test_start_status_stop_roundtrip(config, fake_capture, pipeline_stub):
    bdir = recorder.start(config, title="Planejamento Q4", speakers=3, language="en")
    assert bdir.parent == config.recordings and bdir.name.endswith("-planejamento-q4")
    st = recorder.status(config)
    assert st["phase"] == "recording" and st["bundle"] == str(bdir) and st["pid"] > 0
    assert st["elapsed_s"] >= 0 and st["started_at"]
    meta = bundle.read_meta(bdir)
    assert meta.stopped_at is None and meta.recorder["name"] == "ata-fake"
    res = recorder.stop(config)
    assert res == {"bundle": str(bdir), "note": str(config.notes / f"{bdir.name}.md")}
    assert pipeline_stub == [bdir]
    meta = bundle.read_meta(bdir)
    assert meta.title == "Planejamento Q4" and meta.slug == "planejamento-q4"
    assert meta.language_requested == "en" and meta.speakers_hint == 3 and meta.stopped_at
    assert meta.recorder == {"name": "ata-fake", "version": "0.1.0"}
    assert meta.tracks["far"].samples > 0 and meta.tracks["mic"].start_measured
    assert bundle.damage_report(meta, bdir) == []
    assert not (bdir / "STOP").exists() and (bdir / "recorder.log").is_file()
    assert json.loads((bdir / "meta.json").read_text())["schema"] == "ata/1"
    assert recorder.status(config)["phase"] == "idle" and recorder.read_state(config) is None


def test_language_defaults_to_config(config, fake_capture):
    cfg = config.with_overrides(**{"language.default": "es"})
    bdir = recorder.start(cfg)
    recorder.stop(cfg, process=False)
    assert bundle.read_meta(bdir).language_requested == "es"


def test_already_recording(config, fake_capture):
    bdir = recorder.start(config)
    with pytest.raises(recorder.AlreadyRecording) as exc:
        recorder.start(config)
    assert exc.value.bundle == str(bdir)
    assert cli(config, "start") == 3
    recorder.stop(config, process=False)


def test_nothing_recording(config, capsys):
    with pytest.raises(recorder.NothingRecording):
        recorder.stop(config)
    assert cli(config, "stop") == 3
    assert "nada gravando" in capsys.readouterr().err


def test_stop_without_process(config, fake_capture, pipeline_stub):
    recorder.start(config)
    res = recorder.stop(config, process=False)
    assert res["note"] is None and pipeline_stub == []


def test_pipeline_missing_is_not_fatal(config, fake_capture, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "ata.pipeline.run", None)   # força ModuleNotFoundError
    recorder.start(config)
    res = recorder.stop(config)
    assert res["note"] is None
    assert "ata process" in capsys.readouterr().err


def test_capture_failure_cleans_up(config, fake_capture, monkeypatch):
    monkeypatch.setenv("ATA_FAKE_CAPTURE", "fail")
    with pytest.raises(CaptureError, match="falhar"):
        recorder.start(config)
    assert recorder.read_state(config) is None
    assert bundle.list_bundles(config.recordings) == []
    assert list(config.recordings.iterdir()) == []
    assert cli(config, "start") == 1


def test_missing_backend_exit_4(config, monkeypatch):
    monkeypatch.setenv("ATA_CAPTURE", "banana")
    assert cli(config, "start") == 4
    assert recorder.read_state(config) is None


def test_invalid_language_exit_2(config, fake_capture, capsys):
    assert cli(config, "start", "--lang", "klingon") == 2
    assert "idioma" in capsys.readouterr().err
    assert recorder.read_state(config) is None
    with pytest.raises(ValueError):
        recorder.start(config, speakers=0)


def test_stale_pid_is_recovered(config, fake_capture):
    bdir = recorder.start(config)
    pid = recorder.read_state(config)["pid"]
    os.kill(pid, signal.SIGKILL)
    recorder._CHILDREN[pid].wait()
    st = recorder.status(config)
    assert st["phase"] == "idle" and recorder.read_state(config) is None
    meta = bundle.read_meta(bdir)
    assert meta.stopped_at and "recorder_killed" in meta.damage_reasons
    assert meta.tracks["far"].samples > 0


def test_stop_after_supervisor_killed(config, fake_capture, pipeline_stub):
    bdir = recorder.start(config)
    pid = recorder.read_state(config)["pid"]
    os.kill(pid, signal.SIGKILL)
    recorder._CHILDREN[pid].wait(timeout=10)
    with pytest.raises(recorder.NothingRecording, match="recuperada"):
        recorder.stop(config)
    assert "recorder_killed" in bundle.read_meta(bdir).damage_reasons
    assert pipeline_stub == []


def test_reused_pid_is_not_ours(config, tmp_path):
    # PID vivo de outro programa (este pytest) não é supervisor: estado velho, e NINGUÉM leva sinal
    meta_dir = bundle.new_bundle_dir(config.recordings, __import__("datetime").datetime.now(), "x")
    bundle.write_meta(meta_dir, bundle.BundleMeta(name=meta_dir.name, created_at="t", tracks={}))
    recorder._write_state(config, {"pid": os.getpid(), "bundle": str(meta_dir), "started_at": "t"})
    assert recorder.pid_alive(os.getpid()) is False
    assert recorder.status(config)["phase"] == "idle"
    assert recorder.read_state(config) is None
    assert "recorder_killed" in bundle.read_meta(meta_dir).damage_reasons


def test_corrupt_state_file(config):
    config.cache.mkdir(parents=True, exist_ok=True)
    recorder.state_path(config).write_text("{nao é json")
    assert recorder.status(config)["phase"] == "idle"
    assert not recorder.state_path(config).exists()


def test_toggle_api(config, fake_capture, pipeline_stub):
    r1 = recorder.toggle(config, title="t")
    assert r1["action"] == "started"
    r2 = recorder.toggle(config)
    assert r2["action"] == "stopped" and r2["bundle"] == r1["bundle"] and r2["note"]


def test_cli_flow(config, fake_capture, pipeline_stub, capsys):
    assert cli(config, "toggle", "--title", "cli", "--lang", "pt") == 0
    out = capsys.readouterr().out
    assert "gravando:" in out
    assert cli(config, "status", "--json") == 0
    st = json.loads(capsys.readouterr().out)
    assert st["phase"] == "recording" and set(st) == {"phase", "bundle", "started_at", "elapsed_s", "pid"}
    assert cli(config, "status") == 0 and "gravando há" in capsys.readouterr().out
    assert cli(config, "toggle") == 0
    out = capsys.readouterr().out
    assert "gravação encerrada" in out and "note: " in out
    assert bundle.read_meta(st["bundle"]).language_requested == "pt-BR"
    assert cli(config, "status") == 0 and "parado" in capsys.readouterr().out


def test_cli_start_no_process_is_remembered(config, fake_capture, pipeline_stub, capsys):
    assert cli(config, "start", "--no-process") == 0
    assert cli(config, "stop") == 0
    assert pipeline_stub == [] and "ata process" in capsys.readouterr().out


def test_supervisor_stops_on_sigterm(config, fake_capture):
    bdir = recorder.start(config)
    pid = recorder.read_state(config)["pid"]
    os.kill(pid, signal.SIGTERM)
    assert recorder._CHILDREN[pid].wait(timeout=10) == 0
    meta = bundle.read_meta(bdir)
    assert meta.stopped_at and "recorder_killed" not in meta.damage_reasons
    # stop() depois disso encontra o bundle fechado limpo e devolve normalmente
    assert recorder.stop(config, process=False)["bundle"] == str(bdir)


def test_supervisor_log_has_no_meeting_text(config, fake_capture):
    bdir = recorder.start(config, title="segredo")
    recorder.stop(config, process=False)
    logtxt = (bdir / "recorder.log").read_text()
    words = json.loads((bdir / "far.wav.fake-words.json").read_text())
    assert words and not any(w["text"] in logtxt.split() for w in words if len(w["text"]) > 4)


def test_supervise_module_entrypoint_rejects_bad_args():
    r = subprocess.run([sys.executable, "-m", "ata.recorder"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 2


def test_status_idle_shape(config):
    assert recorder.status(config) == {"phase": "idle", "bundle": None, "started_at": None, "elapsed_s": None,
                                       "pid": None}
    time.sleep(0)
