import io
import json
import threading
import time

import numpy as np
import pytest

from ata import audio, bundle, live
from ata.engines.fake import FakeStreamingAsr

from .conftest import run_cmd

SEC = b"\x01\x00" * 16000  # 1 s de PCM16


@pytest.fixture
def growing(meeting):
    """Bundle "em gravação": WAVs só com cabeçalho, meta sem stopped_at."""
    for t in ("far", "mic"):
        audio.write_wav(meeting / f"{t}.wav", np.zeros(0, np.int16))
    meta = bundle.read_meta(meeting)
    bundle.write_meta(meeting, meta.with_(stopped_at=None))
    return meeting


def append(path, data):
    with open(path, "ab") as f:
        f.write(data)


def test_follower_skips_header(tmp_path):
    p = tmp_path / "x.wav"
    audio.write_wav(p, np.zeros(0, np.int16))
    f = live.TrackFollower(p)
    assert f.read() == b""
    append(p, b"\x05\x00\x06")
    assert f.read() == b"\x05\x00"  # só múltiplos de 2
    append(p, b"\x00")
    assert f.read() == b"\x06\x00"


def test_step_writes_turns_per_track(growing, config):
    s = live.LiveSession(growing, config, streaming=FakeStreamingAsr(), chunk_ms=1000)
    append(growing / "mic.wav", SEC * 2)
    append(growing / "far.wav", SEC)
    assert s.step() == 3
    rows = [json.loads(x) for x in (growing / "live" / "turns.jsonl").read_text().splitlines()]
    far = [r for r in rows if r["track"] == "far"]
    mic = [r for r in rows if r["track"] == "mic"]
    assert [r["speaker"] for r in mic] == ["Eu", "Eu"] and far[0]["speaker"] == "Pessoa"
    assert mic[1]["start"] == 1.0 and mic[1]["t"] == "00:01" and mic[1]["text"] == "segundo 2"


def test_partial_chunk_waits_then_finish_flushes(growing, config):
    s = live.LiveSession(growing, config, streaming=FakeStreamingAsr(), chunk_ms=1000)
    append(growing / "mic.wav", SEC[: 16000])  # meio segundo
    assert s.step() == 0
    append(growing / "mic.wav", SEC[16000:])
    s.finish()
    assert len(s.turns) == 1


def test_language_labels_en(growing, config):
    meta = bundle.read_meta(growing)
    bundle.write_meta(growing, meta.with_(language_requested="en"))
    s = live.LiveSession(growing, config, streaming=FakeStreamingAsr(), chunk_ms=1000)
    append(growing / "mic.wav", SEC)
    append(growing / "far.wav", SEC)
    s.step()
    assert {t["speaker"] for t in s.turns} == {"Me", "Speaker"}


def test_offsets_applied(growing, config):
    meta = bundle.read_meta(growing)
    tr = dict(meta.tracks)
    tr["mic"] = bundle.Track(**{**tr["mic"].to_json(), "start_epoch": tr["far"].start_epoch + 0.5})
    bundle.write_meta(growing, meta.with_(tracks=tr))
    s = live.LiveSession(growing, config, streaming=FakeStreamingAsr(), chunk_ms=1000)
    append(growing / "mic.wav", SEC)
    s.step()
    assert s.turns[0]["start"] == 0.5


def test_run_while_appending_until_stop(growing, config):
    s = live.LiveSession(growing, config, streaming=FakeStreamingAsr(), chunk_ms=1000)

    def writer():
        for _ in range(3):
            append(growing / "far.wav", SEC)
            append(growing / "mic.wav", SEC)
            time.sleep(0.05)
        time.sleep(0.2)
        (growing / "live" / "STOP").touch()

    th = threading.Thread(target=writer)
    th.start()
    total = s.run(poll=0.02, max_seconds=10)
    th.join()
    assert total == 6
    st = json.loads((growing / "live" / "status.json").read_text())
    assert st["phase"] == "stopped" and st["turns"] == 6


def test_run_stops_when_recording_finished(growing, config):
    s = live.LiveSession(growing, config, streaming=FakeStreamingAsr(), chunk_ms=1000)
    append(growing / "mic.wav", SEC)
    meta = bundle.read_meta(growing)
    bundle.write_meta(growing, meta.with_(stopped_at="2026-10-05T10:00:00-03:00"))
    assert s.run(poll=0.01, max_seconds=5) == 1


def test_prep_ticks_overlapping_question(growing, config):
    class Stream:
        def __init__(self):
            self.ev = []

        def send(self, pcm):
            self.ev.append({"type": "final", "text": "o lançamento do beta fica para o dia doze",
                            "start": 0.0, "end": 1.0})

        def events(self):
            while self.ev:
                yield self.ev.pop(0)

        def close(self):
            pass

    class Asr:
        name = "x"

        def open(self, language, sample_rate=16000):
            return Stream()

    s = live.LiveSession(growing, config, streaming=Asr(), chunk_ms=1000)
    s.set_prep([{"q": "q1", "text": "Quando é o lançamento do beta?"},
                {"q": "q2", "text": "Quem corrige os tablets antigos?"}])
    append(growing / "far.wav", SEC)
    s.step()
    prep = json.loads((growing / "live" / "prep.json").read_text())
    assert [i["done"] for i in prep["items"]] == [True, False]
    assert prep["items"][0]["turn"] == 0 and s.turns[0]["answers"] == ["q1"]


def test_summary_every_n(growing, config):
    s = live.LiveSession(growing, config, streaming=FakeStreamingAsr(), summary_every=2, chunk_ms=1000)
    append(growing / "mic.wav", SEC * 4)
    s.step()
    rows = (growing / "live" / "summaries.jsonl").read_text().splitlines()
    assert len(rows) == 2 and json.loads(rows[0])["at_turn"] == 2
    assert "tldr" in json.loads(rows[0])["summary"]


def test_tail_prints(growing, config):
    s = live.LiveSession(growing, config, streaming=FakeStreamingAsr(), chunk_ms=1000)
    append(growing / "mic.wav", SEC)
    s.step()
    out = io.StringIO()
    assert live.tail(growing, once=True, out=out) == 1
    assert out.getvalue().strip() == "[00:00] Eu: segundo 1"
    s._status(phase="stopped")
    out2 = io.StringIO()
    assert live.tail(growing, poll=0.01, max_seconds=2, out=out2) == 1


def test_cli_start_stop_tail(growing, config, capsys, no_parts):
    append(growing / "mic.wav", SEC)
    assert run_cmd(live, ["live", "stop", "--meeting", str(growing)], config) == 3  # sem live/ ainda
    assert run_cmd(live, ["live", "start", "--meeting", str(growing), "--idle-timeout", "0.2"], config) == 0
    assert run_cmd(live, ["live", "tail", "--meeting", growing.name, "--once"], config) == 0
    assert "[00:00] Eu: segundo 1" in capsys.readouterr().out
    assert run_cmd(live, ["live", "stop", "--meeting", str(growing)], config) == 0
    assert (growing / "live" / "STOP").exists()


def test_cli_start_nothing_recording(config, no_parts):
    assert run_cmd(live, ["live", "start"], config) == 3


def test_overlap_fallback():
    assert live.overlap("Quando é o lançamento?", "o lançamento sai quando der") == 1.0
    assert live.overlap("tablets antigos", "nada a ver") == 0.0
