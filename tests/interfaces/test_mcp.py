import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ata import bundle, mcp_server
from ata.dashboard import api

from .conftest import make_processed


@pytest.fixture
def tools(config):
    return mcp_server.Tools(config)


# ---- stdio real ---------------------------------------------------------------------------------------------

class StdioClient:
    def __init__(self, config, tmp_path):
        env = {**os.environ, "ATA_CONFIG": str(config.source), "ATA_ENGINES": "fake",
               "HOME": str(tmp_path / "home"), "PYTHONUNBUFFERED": "1"}
        self.p = subprocess.Popen([sys.executable, "-m", "ata.mcp_server"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True,
                                  cwd=str(tmp_path))
        self.n = 0

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        if notify:
            return None
        while True:
            line = self.p.stdout.readline()
            assert line, "servidor MCP fechou a saída"
            d = json.loads(line)
            if d.get("id") == self.n:
                return d

    def init(self):
        r = self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "pytest", "version": "1"}})
        self.send("notifications/initialized", notify=True)
        return r

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


@pytest.fixture
def stdio(config, tmp_path):
    c = StdioClient(config, tmp_path)
    yield c
    c.close()


def test_stdio_initialize_and_tools_list(stdio):
    r = stdio.init()
    assert r["result"]["serverInfo"]["name"] == "ata"
    tools = stdio.send("tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert len(names) >= 18
    assert set(mcp_server.TOOL_NAMES) <= names
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"


def test_stdio_list_synthetic_meeting(stdio, meeting):
    stdio.init()
    r = stdio.send("tools/call", {"name": "ata_list", "arguments": {"limit": 5}})["result"]
    data = r["structuredContent"]
    assert data["total"] == 1
    assert data["items"][0]["id"] == meeting.name
    assert data["items"][0]["title"] == "lançamento beta"


def test_stdio_status_degrades_and_resources(stdio, processed):
    stdio.init()
    st = stdio.send("tools/call", {"name": "ata_status", "arguments": {}})["result"]["structuredContent"]
    assert st["recorder"]["phase"] in ("idle", "recording")
    tpl = stdio.send("resources/templates/list")["result"]["resourceTemplates"]
    uris = {t["uriTemplate"] for t in tpl}
    assert {"ata://{meeting_id}", "ata://{meeting_id}/transcript"} <= uris
    rr = stdio.send("resources/read", {"uri": f"ata://{processed.name}/transcript"})["result"]
    assert "[00:01] Eu: Bom dia" in rr["contents"][0]["text"]


def test_stdio_read_tool(stdio, processed):
    stdio.init()
    r = stdio.send("tools/call", {"name": "ata_read",
                                  "arguments": {"meeting_id": processed.name, "parts": ["summary"]}})
    d = r["result"]["structuredContent"]
    assert d["summary"]["tldr"].startswith("Lançamento")


# ---- Tools em processo ---------------------------------------------------------------------------------------

def test_tool_names_complete():
    assert len(mcp_server.TOOL_NAMES) == 18 and len(set(mcp_server.TOOL_NAMES)) == 18


def test_build_server_registers_all(config):
    srv = mcp_server.build_server(config)
    assert srv is not None


def test_list_pagination(tools, config):
    from ata.testing import Line, synth_meeting
    from datetime import datetime, timedelta
    base = datetime(2026, 10, 1, 9, 0).astimezone()
    for i in range(5):
        synth_meeting(config.recordings, [Line("Eu", "oi"), Line("A", "olá")], title=f"r{i}",
                      started=base + timedelta(days=i))
    p1 = tools.list(limit=2)
    assert p1["total"] == 5 and len(p1["items"]) == 2 and p1["next_cursor"] == "2"
    p3 = tools.list(limit=2, cursor="4")
    assert len(p3["items"]) == 1 and p3["next_cursor"] is None
    assert p1["items"][0]["date"] > p1["items"][1]["date"]
    assert tools.list(date_from="2026-10-04")["total"] == 2
    assert tools.list(query="r3")["total"] == 1


def test_read_transcript_window_and_names(tools, processed):
    bundle.write_speaker_names(processed, {"Pessoa 2": "Ana"})
    d = tools.read(processed.name, parts=["transcript"], from_s=10.0, to_s=14.0)
    items = d["transcript"]["items"]
    assert [t["start"] for t in items] == [10.5, 13.5]
    assert items[1]["speaker"] == "Ana" and items[1]["label"] == "Pessoa 2"
    d2 = tools.read(processed.name, parts=["transcript"], limit=3)
    assert d2["transcript"]["next_cursor"] == "3"


def test_read_bad_part_and_unknown(tools, processed):
    wrapped = mcp_server._wrap(tools.read)
    assert wrapped(processed.name, parts=["xx"])["ok"] is False
    r = wrapped("nao-existe")
    assert r["ok"] is False and r["status"] == 404
    assert wrapped("../etc")["status"] == 400


def test_search_uses_index(tools, fakes):
    r = tools.search("lançamento", limit=3)
    assert r["engine"] == "index" and len(r["items"]) == 3 and r["next_cursor"] == "3"
    assert r["items"][0]["t"] == "00:12"
    assert ("index.search", "lançamento", "hybrid", 3) in fakes.calls
    r2 = tools.search("lançamento", limit=3, cursor="3")
    assert r2["items"][0]["text"].startswith("trecho 3")


def test_search_fallback_lexical(tools, processed, no_parts):
    r = tools.search("tablets")
    assert r["engine"] == "lexical-fallback"
    assert {h["meeting"] for h in r["items"]} == {processed.name}
    assert all("tablets" in h["text"] for h in r["items"])


def test_actions_and_decisions(tools, processed):
    a = tools.actions()
    assert a["total"] == 1 and a["items"][0]["owner"] == "Pessoa 3" and a["items"][0]["due"] == "quarta"
    bundle.write_speaker_names(processed, {"Pessoa 3": "Bruno"})
    assert tools.actions(owner="bruno")["total"] == 1
    assert tools.actions(owner="ninguem")["total"] == 0
    d = tools.decisions()
    assert d["items"][0]["t"] == "00:13"


def test_person_timeline(tools, processed):
    bundle.write_speaker_names(processed, {"Pessoa 3": "Bruno"})
    p = tools.person("bruno")
    assert p["total"] == 1
    row = p["items"][0]
    assert row["turns"] == 2 and row["actions"] == ["Corrigir o erro dos tablets"]


def test_ask_and_prep(tools, fakes, processed):
    r = tools.ask("quando lança?", processed.name)
    assert r["answer"] == "dia doze"
    assert ("ask", "quando lança?", processed.name) in fakes.calls
    p = tools.prep("beta", days=30)
    assert [i["id"] for i in p["items"]] == ["q1", "q2"]
    assert ("build_prep", "beta", 30) in fakes.calls


def test_record_lane(tools, fakes):
    assert tools.status()["recorder"]["phase"] == "idle"
    r = tools.record_start(title="planejamento")
    assert r["ok"] and fakes.calls[0] == ("recorder.start", "planejamento", None, None)
    assert tools.record_start()["ok"] is False  # já gravando
    t = tools.record_toggle()
    assert t["action"] == "stop" and ("recorder.stop", True) in fakes.calls
    assert tools.record_stop()["ok"] is False  # nada gravando


def test_record_unavailable(tools, no_parts):
    r = mcp_server._wrap(tools.record_start)()
    assert r["ok"] is False and r["status"] == 503
    assert tools.status()["recorder"]["available"] is False


def test_process_job(tools, fakes, meeting, config):
    j = tools.process(meeting.name, language="pt-BR")
    assert j["status"] == "queued" and j["job_id"]
    done = tools.jobs.wait(j["job_id"], 10)
    assert done["status"] == "done" and done["progress"] == 1.0
    assert done["note"].endswith(f"{meeting.name}.md")
    assert ("process_bundle", meeting.name, "pt-BR", None, True) in fakes.calls
    assert tools.job()["jobs"][0]["job_id"] == j["job_id"]


def test_process_job_error(tools, no_parts, meeting):
    j = tools.process(meeting.name)
    done = tools.jobs.wait(j["job_id"], 10)
    assert done["status"] == "error" and "pipeline" in done["error"]
    assert mcp_server._wrap(tools.job)("zzz")["status"] == 404


def test_rename_and_export(tools, processed, monkeypatch):
    monkeypatch.setattr(api, "rerender", lambda config, bdir: {"ok": True, "fake": True})
    r = tools.rename_speakers(processed.name, {"Pessoa 2": "Ana"})
    assert r["names"] == {"Pessoa 2": "Ana"} and r["rerender"]["ok"]
    assert bundle.read_speaker_names(processed) == {"Pessoa 2": "Ana"}
    for fmt in api.EXPORT_FORMATS:
        e = tools.export(processed.name, fmt)
        text = Path(e["path"]).read_text(encoding="utf-8")
        assert "Ana" in text, fmt
    srt = (processed / "export.srt").read_text(encoding="utf-8")
    assert "00:00:04,500 --> 00:00:08,000" in srt
    assert (processed / "export.vtt").read_text(encoding="utf-8").startswith("WEBVTT")


def test_export_needs_transcript(tools, meeting):
    r = mcp_server._wrap(tools.export)(meeting.name, "srt")
    assert r["ok"] is False and r["status"] == 409


def test_reindex_and_doctor(tools, fakes, monkeypatch):
    assert tools.reindex()["ok"] and ("index.reindex",) in fakes.calls
    monkeypatch.setattr(api, "_cli", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    d = tools.doctor()
    assert d["source"] == "básico" and "checks" in d["report"]


def test_mcp_add_parser():
    import argparse
    p = argparse.ArgumentParser()
    mcp_server.add_parser(p.add_subparsers())
    a = p.parse_args(["mcp", "--http", "--port", "9999"])
    assert a.http and a.port == 9999 and a.func
