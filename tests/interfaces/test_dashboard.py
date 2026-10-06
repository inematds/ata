import http.client
import json
import socket
import threading

import pytest

from ata import bundle
from ata.dashboard import api, server


@pytest.fixture
def dash(config):
    srv = server.DashboardServer(config, 0, token="T" * 40, sse_interval=0.05)
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    th.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def req(srv, method, path, *, auth=True, host=None, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    h = {"Host": host or f"127.0.0.1:{srv.port}"}
    if auth:
        h["Cookie"] = f"{server.COOKIE}={srv.token}"
    if body is not None:
        body = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r, data


def js(data):
    return json.loads(data.decode("utf-8"))


def test_token_flow_sets_cookie(dash):
    r, _ = req(dash, "GET", f"/?t={dash.token}", auth=False)
    assert r.status == 303 and r.getheader("Location") == "/"
    cookie = r.getheader("Set-Cookie")
    assert f"{server.COOKIE}={dash.token}" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    r2, _ = req(dash, "GET", "/?t=errado", auth=False)
    assert r2.status == 403


def test_requires_token(dash):
    assert req(dash, "GET", "/api/status", auth=False)[0].status == 403
    assert req(dash, "GET", "/", auth=False)[0].status == 403
    r, _ = req(dash, "GET", "/api/status", auth=False, headers={server.TOKEN_HEADER: dash.token})
    assert r.status == 200


def test_bad_host_refused(dash):
    assert req(dash, "GET", "/api/status", host="evil.example")[0].status == 403
    assert req(dash, "GET", "/api/status", host=f"localhost:{dash.port}")[0].status == 200


def test_bad_origin_refused_on_post(dash, processed):
    r, _ = req(dash, "POST", f"/api/meetings/{processed.name}/speakers", body={"names": {"Pessoa 2": "Ana"}},
               headers={"Origin": "http://evil.example"})
    assert r.status == 403


def test_static_and_csp(dash):
    r, data = req(dash, "GET", "/")
    assert r.status == 200 and b"<title>Ata</title>" in data
    assert "default-src 'self'" in r.getheader("Content-Security-Policy")
    for p, ct in (("/app.js", "javascript"), ("/style.css", "text/css")):
        r, data = req(dash, "GET", p)
        assert r.status == 200 and ct in r.getheader("Content-Type") and data
    assert req(dash, "GET", "/../pyproject.toml")[0].status == 404


def test_status_shape(dash, no_parts):
    r, data = req(dash, "GET", "/api/status")
    d = js(data)
    assert r.status == 200 and d["recorder"]["phase"] == "idle" and "live_bundle" in d


def test_meetings_list_and_detail(dash, processed):
    d = js(req(dash, "GET", "/api/meetings")[1])
    assert d["total"] == 1 and d["items"][0]["id"] == processed.name and d["items"][0]["processed"]
    r, data = req(dash, "GET", f"/api/meetings/{processed.name}")
    m = js(data)
    assert r.status == 200
    assert m["meta"]["title"] == "lançamento beta" and m["audio"] == {"far": True, "mic": True}
    assert m["summary"]["decisions"][0]["text"].startswith("Lançar")
    assert len(m["transcript"]["items"]) == 7 and m["offsets"] == {"far": 0.0, "mic": 0.0}


def test_path_traversal_blocked(dash, processed, config):
    secret = config.recordings.parent / "segredo"
    secret.mkdir()
    (secret / "meta.json").write_text("{}")
    for bad in ("%2e%2e", "..%2fsegredo", "%2e%2e%2fsegredo", ".hidden"):
        r, _ = req(dash, "GET", f"/api/meetings/{bad}")
        assert r.status in (400, 404), bad
    r, _ = req(dash, "GET", f"/api/meetings/{processed.name}/audio/..%2f..%2fconfig.toml")
    assert r.status == 400
    # bypass por socket cru (sem normalização do cliente)
    s = socket.create_connection(("127.0.0.1", dash.port), timeout=5)
    s.sendall(f"GET /api/meetings/../segredo HTTP/1.1\r\nHost: 127.0.0.1:{dash.port}\r\n"
              f"Cookie: {server.COOKIE}={dash.token}\r\nConnection: close\r\n\r\n".encode())
    raw = s.recv(4096).decode("latin-1")
    s.close()
    assert raw.split()[1] in ("400", "404")


def test_audio_range(dash, processed):
    size = (processed / "far.wav").stat().st_size
    r, data = req(dash, "GET", f"/api/meetings/{processed.name}/audio/far")
    assert r.status == 200 and len(data) == size and data[:4] == b"RIFF"
    r, data = req(dash, "GET", f"/api/meetings/{processed.name}/audio/mic", headers={"Range": "bytes=0-99"})
    assert r.status == 206 and len(data) == 100
    assert r.getheader("Content-Range") == f"bytes 0-99/{(processed / 'mic.wav').stat().st_size}"
    r, data = req(dash, "GET", f"/api/meetings/{processed.name}/audio/far", headers={"Range": "bytes=-10"})
    assert r.status == 206 and len(data) == 10
    r, _ = req(dash, "GET", f"/api/meetings/{processed.name}/audio/far",
               headers={"Range": f"bytes={size + 5}-"})
    assert r.status == 416
    assert req(dash, "GET", f"/api/meetings/{processed.name}/audio/voz")[0].status == 400


def test_search_endpoint(dash, processed, no_parts):
    d = js(req(dash, "GET", "/api/search?q=tablets")[1])
    assert d["total"] >= 1 and d["items"][0]["meeting"] == processed.name and "t" in d["items"][0]
    assert req(dash, "GET", "/api/search?q=")[0].status == 400


def test_search_endpoint_real_index(dash, processed):
    d = js(req(dash, "GET", "/api/search?q=tablets&mode=lexical")[1])
    assert d["engine"] == "index" and any("tablets" in h["text"] for h in d["items"])


def test_rename_speakers_post(dash, processed, monkeypatch):
    monkeypatch.setattr(api, "rerender", lambda config, bdir: {"ok": True})
    r, data = req(dash, "POST", f"/api/meetings/{processed.name}/speakers",
                  body={"names": {"Pessoa 2": "Ana"}})
    assert r.status == 200 and js(data)["names"] == {"Pessoa 2": "Ana"}
    m = js(req(dash, "GET", f"/api/meetings/{processed.name}")[1])
    assert m["transcript"]["items"][1]["speaker"] == "Ana"
    r, _ = req(dash, "POST", "/api/speakers", body={"meeting": processed.name, "names": {}})
    assert r.status == 400
    assert req(dash, "POST", f"/api/meetings/{processed.name}/speakers", auth=False,
               body={"names": {"a": "b"}})[0].status == 403


def test_record_start_stop(dash, fakes):
    r, data = req(dash, "POST", "/api/record/start", body={"title": "daily"})
    assert r.status == 200 and js(data)["ok"]
    assert js(req(dash, "GET", "/api/status")[1])["recorder"]["phase"] == "recording"
    d = js(req(dash, "POST", "/api/record/stop", body={})[1])
    assert d["ok"] and d["stopping"]
    job = dash.jobs.wait(d["job_id"], 10)
    assert job["status"] == "done" and ("recorder.stop", True) in fakes.calls
    assert js(req(dash, "GET", f"/api/jobs/{d['job_id']}")[1])["status"] == "done"


def test_record_unavailable(dash, no_parts):
    r, data = req(dash, "POST", "/api/record/start", body={})
    assert r.status == 503 and "gravador" in js(data)["error"]


def test_bad_json_and_unknown_route(dash):
    c = http.client.HTTPConnection("127.0.0.1", dash.port, timeout=5)
    c.request("POST", "/api/record/start", body=b"{nao json", headers={
        "Host": f"127.0.0.1:{dash.port}", "Cookie": f"{server.COOKIE}={dash.token}"})
    assert c.getresponse().status == 400
    c.close()
    assert req(dash, "GET", "/api/nada")[0].status == 404


def test_config_view(dash, config):
    d = js(req(dash, "GET", "/api/config")[1])
    assert d["source"] == str(config.source) and d["config"]["paths"]["recordings"] == str(config.recordings)


def test_sse_status_and_live_turns(dash, processed, no_parts):
    live = processed / "live"
    live.mkdir()
    (live / "turns.jsonl").write_text(json.dumps({"start": 1.0, "end": 2.0, "speaker": "Eu", "text": "olá",
                                                  "track": "mic"}) + "\n", encoding="utf-8")
    c = http.client.HTTPConnection("127.0.0.1", dash.port, timeout=5)
    c.request("GET", f"/api/events?meeting={processed.name}", headers={
        "Host": f"127.0.0.1:{dash.port}", "Cookie": f"{server.COOKIE}={dash.token}"})
    r = c.getresponse()
    assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")
    seen = []
    while len(seen) < 2:
        line = r.fp.readline().decode()
        if line.startswith("event:"):
            seen.append(line.split(":", 1)[1].strip())
        if line.startswith("data:") and seen[-1] == "turn":
            assert json.loads(line[5:])["text"] == "olá"
    assert seen == ["status", "turn"]
    c.close()


def test_token_persisted(config):
    t1 = server.load_token(config)
    t2 = server.load_token(config)
    assert t1 == t2 and len(t1) >= 32
    assert ((config.cache / "dashboard-token").stat().st_mode & 0o777) == 0o600


def test_dashboard_add_parser():
    import argparse

    from ata import dashboard
    p = argparse.ArgumentParser()
    dashboard.add_parser(p.add_subparsers())
    a = p.parse_args(["dashboard", "--port", "1234", "--no-browser"])
    assert a.port == 1234 and a.no_browser


def test_meeting_unknown_404(dash):
    r, data = req(dash, "GET", "/api/meetings/2020-01-01-0000-nada")
    assert r.status == 404 and js(data)["ok"] is False


def test_bundle_without_turns_lists(dash, meeting):
    d = js(req(dash, "GET", "/api/meetings")[1])
    assert d["items"][0]["processed"] is False and d["items"][0]["turns"] == 0
    m = js(req(dash, "GET", f"/api/meetings/{meeting.name}")[1])
    assert m["transcript"]["items"] == [] and m["summary"] is None
    assert bundle.read_meta(meeting).name == meeting.name
