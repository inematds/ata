"""Cliente do sidecar NeMo-Speech.cpp contra servidores fake (HTTP e WebSocket). As fixtures JSON abaixo
são o contrato escrito do formato de resposta assumido (ver docstring de ata.engines.nemo)."""

from __future__ import annotations

import json
import sys
import threading

import numpy as np
import pytest

from ata import audio
from ata.engines import nemo
from ata.engines.base import AsrEngine, Diarizer, EngineError, EngineMissing, StreamingAsr

VERBOSE = {
    "text": "bom dia a todos",
    "language": "pt",
    "segments": [
        {"start": 0.5, "end": 1.4, "text": "bom dia", "speaker": 1,
         "words": [{"word": "bom", "start": 0.5, "end": 0.8, "confidence": 0.98},
                   {"word": "dia", "start": 0.85, "end": 1.4, "confidence": 0.95}]},
        {"start": 2.0, "end": 3.1, "text": "a todos", "speaker": 2,
         "words": [{"word": "a", "start": 2.0, "end": 2.1}, {"word": "todos", "start": 2.2, "end": 3.1}]},
    ],
}


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "far.wav"
    audio.write_wav(p, np.zeros(16000, np.float32))
    return p


def _cfg(config, port):
    return config.with_overrides(engine__host="127.0.0.1", engine__port=port)


def test_transcribe_parses_nested_words_and_sends_multipart(config, fake_http, wav):
    srv = fake_http({("POST", nemo.TRANSCRIBE_PATH): (200, VERBOSE)})
    asr = nemo.make_asr(_cfg(config, srv.port), "parakeet-tdt-0.6b-v3")
    assert isinstance(asr, AsrEngine)
    words = asr.transcribe(wav, "pt-BR")
    assert [w.text for w in words] == ["bom", "dia", "a", "todos"]
    assert words[0].start == 0.5 and words[0].confidence == 0.98
    req = srv.requests[0]
    assert req["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    body = req["body"]
    for needle in (b'name="model"\r\n\r\nparakeet-tdt-0.6b-v3', b'name="language"\r\n\r\npt\r\n',
                   b'name="response_format"\r\n\r\nverbose_json', b'name="diarize"\r\n\r\nfalse',
                   b'name="timestamp_granularities[]"\r\n\r\nword', b'filename="far.wav"', b"RIFF"):
        assert needle in body


def test_auto_language_is_omitted(config, fake_http, wav):
    srv = fake_http({("POST", nemo.TRANSCRIBE_PATH): (200, {"words": []})})
    nemo.make_asr(_cfg(config, srv.port), "").transcribe(wav, "auto")
    assert b'name="language"' not in srv.requests[0]["body"]


def test_diarize_one_based_speakers_from_segments(config, fake_http, wav):
    srv = fake_http({("POST", nemo.TRANSCRIBE_PATH): (200, VERBOSE)})
    d = nemo.make_diarizer(_cfg(config, srv.port), "")
    assert isinstance(d, Diarizer)
    spans = d.diarize(wav, max_speakers=3)
    assert [(s.speaker, s.start, s.end) for s in spans] == [("S0", 0.5, 1.4), ("S1", 2.0, 3.1)]
    body = srv.requests[0]["body"]
    assert b'name="diarize"\r\n\r\ntrue' in body and b'name="max_speakers"\r\n\r\n3' in body
    assert b"nemotron-3-diarization" in body


def test_spans_from_word_speakers_are_merged():
    resp = {"words": [
        {"text": "a", "start": 0.0, "end": 0.2, "speaker": "SPEAKER_00"},
        {"text": "b", "start": 0.3, "end": 0.5, "speaker": "SPEAKER_00"},
        {"text": "c", "start": 0.6, "end": 0.9, "speaker": "SPEAKER_01"},
        {"text": "d", "start": 3.0, "end": 3.2, "speaker": "SPEAKER_00"}]}
    spans = nemo.parse_spans(resp)
    assert [(s.speaker, s.start, s.end) for s in spans] == [("S0", 0.0, 0.5), ("S1", 0.6, 0.9),
                                                             ("S0", 3.0, 3.2)]


def test_field_variants_ms_and_alt_names():
    resp = {"words": [{"token": "olá", "start_ms": 1200, "end_ms": 1500, "probability": 0.7},
                      {"text": "mundo", "start_time": "1.6", "end_time": 2.0},
                      {"word": "x", "offset": 2.1, "duration": 0.3},
                      {"word": "", "start": 5, "end": 6},          # sem texto: ignorada
                      {"word": "sem-tempo"}]}                      # sem tempo: ignorada
    words = nemo.parse_words(resp)
    assert [(w.text, w.start, w.end) for w in words] == [("olá", 1.2, 1.5), ("mundo", 1.6, 2.0),
                                                          ("x", 2.1, pytest.approx(2.4))]
    assert words[0].confidence == 0.7
    big = nemo.parse_words({"words": [{"word": "y", "start": 12000, "end": 12500}]})
    assert (big[0].start, big[0].end) == (12.0, 12.5)


def test_speaker_map_variants():
    zero = nemo.SpeakerMap([0, 1])
    assert zero(0) == "S0" and zero(1) == "S1"
    one = nemo.SpeakerMap([1, 2])
    assert one(1) == "S0" and one("2") == "S1"
    names = nemo.SpeakerMap(["alice", "bob"])
    assert names("bob") == "S0" and names("alice") == "S1" and names("bob") == "S0"
    assert nemo.SpeakerMap(["speaker_3"])("speaker_3") == "S3"


def test_connection_refused_message(config, closed_port, wav):
    asr = nemo.make_asr(_cfg(config, closed_port), "")
    with pytest.raises(EngineError) as exc:
        asr.transcribe(wav, "en")
    assert str(exc.value) == "motor nemo não está rodando: ata engine start"


def test_http_error_and_non_json(config, fake_http, wav):
    srv = fake_http({("POST", nemo.TRANSCRIBE_PATH): (500, {"error": "segredo da reunião"})})
    with pytest.raises(EngineError) as exc:
        nemo.make_asr(_cfg(config, srv.port), "").transcribe(wav, "en")
    assert "HTTP 500" in str(exc.value) and "segredo" not in str(exc.value)
    srv2 = fake_http({("POST", nemo.TRANSCRIBE_PATH): (200, b"texto SIGILOSO solto")})
    with pytest.raises(EngineError) as exc2:
        nemo.make_asr(_cfg(config, srv2.port), "").transcribe(wav, "en")
    assert "SIGILOSO" not in str(exc2.value)


def test_health_falls_back_to_models(config, fake_http, closed_port):
    srv = fake_http({("GET", "/v1/models"): (200, {"data": [{"id": "parakeet"}]})})
    h = nemo.health(_cfg(config, srv.port))
    assert h["ok"] and h["path"] == "/v1/models"
    srv2 = fake_http({("GET", "/health"): (200, {"status": "ok"})})
    assert nemo.health(_cfg(config, srv2.port))["path"] == "/health"
    down = nemo.health(_cfg(config, closed_port))
    assert not down["ok"] and down["error"] == nemo.NOT_RUNNING


def test_normalize_event_variants():
    assert nemo.normalize_event({"type": "session.created"}) is None
    p = nemo.normalize_event({"type": "transcript.partial", "text": " oi "})
    assert p == {"type": "partial", "text": "oi", "start": None, "end": None}
    f = nemo.normalize_event({"is_final": True, "transcript": "tudo bem", "start": 1.0, "end": 2.0,
                              "speaker": 1})
    assert f["type"] == "final" and f["text"] == "tudo bem" and (f["start"], f["end"]) == (1.0, 2.0)
    w = nemo.normalize_event({"type": "final", "text": "a b",
                              "words": [{"word": "a", "start": 3, "end": 3.2}, {"word": "b", "start": 3.3,
                                                                                "end": 3.5}]})
    assert (w["start"], w["end"]) == (3, 3.5) and len(w["words"]) == 2
    assert nemo.normalize_event({"type": "error", "message": "x"})["type"] == "error"


def test_streaming_missing_websockets(config, monkeypatch):
    monkeypatch.setitem(sys.modules, "websockets", None)
    monkeypatch.setitem(sys.modules, "websockets.sync", None)
    monkeypatch.setitem(sys.modules, "websockets.sync.client", None)
    with pytest.raises(EngineMissing) as exc:
        nemo.make_streaming(config, "").open("pt-BR")
    assert "uv tool install 'ata[live]'" in str(exc.value)


def test_streaming_refused(config, closed_port):
    pytest.importorskip("websockets")
    with pytest.raises(EngineError) as exc:
        nemo.make_streaming(_cfg(config, closed_port), "").open("pt-BR")
    assert str(exc.value) == nemo.NOT_RUNNING


@pytest.fixture
def fake_ws():
    pytest.importorskip("websockets")
    from websockets.sync.server import serve

    log: dict = {"config": None, "bytes": 0, "path": None, "ended": False}

    def handler(ws):
        log["path"] = ws.request.path
        log["config"] = json.loads(ws.recv())
        ws.send(json.dumps({"type": "session.created"}))
        for msg in ws:
            if isinstance(msg, bytes):
                before = log["bytes"] // 32000
                log["bytes"] += len(msg)
                ws.send(json.dumps({"type": "partial", "text": "par"}))
                for sec in range(before, log["bytes"] // 32000):
                    ws.send(json.dumps({"type": "transcript.final", "text": f"seg {sec}", "start": sec,
                                        "end": sec + 1}))
            elif json.loads(msg).get("type") == "end":
                log["ended"] = True
                ws.send(json.dumps({"is_final": True, "text": "fim", "start": 9, "end": 10}))
                return

    server = serve(handler, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.socket.getsockname()[1], log
    server.shutdown()


def test_streaming_session_roundtrip(config, fake_ws):
    port, log = fake_ws
    st = nemo.make_streaming(_cfg(config, port), "")
    assert isinstance(st, StreamingAsr)
    sess = st.open("es", 16000)
    sess.send(b"\x00\x00" * 16000)        # 1 s
    sess.send(b"\x00\x00" * 16000)        # 2 s
    import time
    events: list = []
    deadline = time.time() + 3
    while time.time() < deadline and sum(e["type"] == "final" for e in events) < 2:
        events.extend(sess.events())
    finals = [e for e in events if e["type"] == "final"]
    assert [e["text"] for e in finals] == ["seg 0", "seg 1"]
    assert any(e["type"] == "partial" for e in events)
    sess.close()
    rest = list(sess.events())
    assert [e["text"] for e in rest] == ["fim"]
    assert log["path"] == nemo.REALTIME_PATH and log["ended"] and log["bytes"] == 64000
    assert log["config"]["language"] == "es" and log["config"]["sample_rate"] == 16000
    assert log["config"]["word_timestamps"] is True and log["config"]["encoding"] == "pcm_s16le"
