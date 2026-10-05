"""claude/codex CLI com binários FAKE (scripts em tmp). Nenhum binário real é chamado."""

from __future__ import annotations

import json
import os
import stat
import sys
import textwrap

import pytest

from ata.engines import cli_llm
from ata.engines.base import EngineError, EngineMissing, Summarizer

SCHEMA = {"type": "object", "required": ["language", "tldr"]}
GOOD = {"language": "en", "tldr": "ship on the 12th"}

FAKE = textwrap.dedent("""\
    #!{python}
    import json, os, sys, time
    data = sys.stdin.read()
    log = os.environ["FAKE_LOG"]
    json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(), "cwd_files": os.listdir(os.getcwd()),
               "stdin": data, "api_key": "ANTHROPIC_API_KEY" in os.environ or "OPENAI_API_KEY" in os.environ}},
              open(log, "w"))
    mode = os.environ.get("FAKE_MODE", "ok")
    good = {good!r}
    if mode == "sleep":
        time.sleep(10)
    if mode == "fail":
        print("SEGREDO no stderr", file=sys.stderr); sys.exit(3)
    if mode == "claude":
        print(json.dumps({{"type": "result", "is_error": False,
                          "result": "Aqui está:\\n```json\\n" + json.dumps(good) + "\\n```"}}))
    elif mode == "claude_error":
        print(json.dumps({{"type": "result", "is_error": True, "result": "SEGREDO"}}))
    elif mode == "prose":
        print("SEGREDO sem json nenhum")
    elif mode == "codex_file":
        out = sys.argv[sys.argv.index("--output-last-message") + 1]
        open(out, "w").write("Resposta final: " + json.dumps(good) + " fim")
        print("logs do codex")
    elif mode == "codex_stdout":
        print("pensando... " + json.dumps(good))
    elif mode == "missing_key":
        print(json.dumps({{"type": "result", "result": json.dumps({{"language": "en", "x": "SEGREDO"}})}}))
""")


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    def make(name: str, mode: str):
        p = tmp_path / f"fake-{name}"
        p.write_text(FAKE.format(python=sys.executable, good=GOOD), encoding="utf-8")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        log = tmp_path / f"{name}.log.json"
        monkeypatch.setenv("FAKE_LOG", str(log))
        monkeypatch.setenv("FAKE_MODE", mode)
        monkeypatch.setenv(f"ATA_{name.upper()}_BIN", str(p))
        return log
    return make


def test_claude_flags_cwd_stdin_and_envelope(config, fake_bin, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-nao-usar")
    log = fake_bin("claude", "claude")
    s = cli_llm.make_claude(config, "")
    assert isinstance(s, Summarizer)
    assert s.summarize("LANGUAGE: en\n[00:01] Me: hi", SCHEMA) == GOOD
    seen = json.load(open(log))
    argv = seen["argv"]
    assert argv[:3] == ["-p", "--output-format", "json"]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv
    assert seen["cwd_files"] == [] and "ata-llm-" in seen["cwd"]
    assert not os.path.exists(seen["cwd"])               # pasta temporária removida
    assert seen["stdin"].startswith("LANGUAGE: en") and '"required": ["language", "tldr"]' in seen["stdin"]
    assert seen["api_key"] is False                      # chave de API nunca chega ao CLI


def test_codex_reads_last_message_file(config, fake_bin):
    log = fake_bin("codex", "codex_file")
    assert cli_llm.make_codex(config, "gpt-6-luna").summarize("p", SCHEMA) == GOOD
    argv = json.load(open(log))["argv"]
    assert argv[:4] == ["exec", "--skip-git-repo-check", "-s", "read-only"]
    assert argv[argv.index("-m") + 1] == "gpt-6-luna" and argv[-1] == "-"


def test_codex_falls_back_to_stdout(config, fake_bin):
    fake_bin("codex", "codex_stdout")
    s = cli_llm.make_codex(config, "")
    assert s.summarize("p", SCHEMA) == GOOD
    assert "-m" not in s.command(__import__("pathlib").Path("/tmp"))


@pytest.mark.parametrize("mode", ["fail", "claude_error", "prose", "missing_key"])
def test_claude_failures_never_leak(config, fake_bin, mode):
    fake_bin("claude", mode)
    with pytest.raises(EngineError) as exc:
        cli_llm.make_claude(config, "m").summarize("SEGREDO no prompt", SCHEMA)
    assert "SEGREDO" not in str(exc.value)


def test_timeout(config, fake_bin):
    fake_bin("claude", "sleep")
    cfg = config.with_overrides(summary__timeout_seconds=1)
    with pytest.raises(EngineError) as exc:
        cli_llm.make_claude(cfg, "m").summarize("p", SCHEMA)
    assert "tempo limite" in str(exc.value)


def test_missing_binary(config, monkeypatch, tmp_path):
    monkeypatch.delenv("ATA_CLAUDE_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "vazio"))
    with pytest.raises(EngineMissing):
        cli_llm.make_claude(config, "")
    monkeypatch.setenv("ATA_CODEX_BIN", str(tmp_path / "nao-existe"))
    with pytest.raises(EngineMissing):
        cli_llm.make_codex(config, "")


def test_extract_json_object():
    assert cli_llm.extract_json_object('{"a": 1}') == {"a": 1}
    assert cli_llm.extract_json_object("texto ```json\n{\"a\": 2}\n``` fim") == {"a": 2}
    assert cli_llm.extract_json_object("x {quebrado} y {\"a\": {\"b\": 3}} z") == {"a": {"b": 3}}
    assert cli_llm.extract_json_object("[1, 2]") is None
    assert cli_llm.extract_json_object("nada") is None
