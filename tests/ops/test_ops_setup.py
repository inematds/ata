"""ata setup: grava config, privacidade nível 1 para claude/codex, modo interativo."""

from __future__ import annotations

import argparse

import pytest

from ata import config as cfgmod
from ata import setup as setupmod


def _args(**kw):
    base = dict(config=None, yes=True, lang=None, notes=None, recordings=None, summary=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_setup_yes_writes_config(run, config, tmp_path, capsys):
    target = tmp_path / "novo" / "config.toml"
    notes = tmp_path / "n"
    rc = run("ata.setup", ["--config", str(target), "setup", "--yes", "--lang", "es", "--notes", str(notes),
                           "--recordings", str(tmp_path / "g"), "--summary", "none"], config)
    assert rc == 0
    cfg = cfgmod.load_config(target)
    assert cfg.get("language.default") == "es" and cfg.get("summary.provider") == "none"
    assert cfg.get("privacy.level") == 0 and notes.is_dir()
    assert "config gravada em" in capsys.readouterr().out


@pytest.mark.parametrize("provider,dest", [("claude", "Anthropic"), ("codex", "OpenAI")])
def test_setup_cli_provider_sets_privacy_and_warns(run, config, tmp_path, capsys, provider, dest):
    target = tmp_path / "c.toml"
    assert run("ata.setup", ["--config", str(target), "setup", "--yes", "--summary", provider], config) == 0
    cfg = cfgmod.load_config(target)
    assert cfg.get("privacy.level") == 1 and cfg.get("summary.provider") == provider
    err = capsys.readouterr().err
    assert "TEXTO da transcrição" in err and "nunca o áudio" in err and dest in err


def test_setup_keeps_existing_values(run, config, tmp_path):
    target = tmp_path / "c.toml"
    cfgmod.write_config({"summary": {"provider": "ollama", "ollama_model": "gemma4:26b"},
                         "engine": {"port": 50000}}, target)
    assert run("ata.setup", ["--config", str(target), "setup", "--yes", "--lang", "en"], config) == 0
    cfg = cfgmod.load_config(target)
    assert cfg.get("summary.ollama_model") == "gemma4:26b" and cfg.get("engine.port") == 50000
    assert cfg.get("language.default") == "en"


def test_setup_invalid_lang(run, config, tmp_path):
    assert run("ata.setup", ["--config", str(tmp_path / "c.toml"), "setup", "--yes", "--lang", "fr"],
               config) == 2


def test_setup_interactive(config, tmp_path):
    answers = iter(["en", str(tmp_path / "notas"), "", "none"])
    target = tmp_path / "i.toml"
    rc = setupmod.cmd_setup(_args(config=str(target), yes=False), config, ask=lambda p: next(answers),
                            isatty=lambda: True)
    assert rc == 0
    cfg = cfgmod.load_config(target)
    assert cfg.get("language.default") == "en" and cfg.get("summary.provider") == "none"
    assert cfg.get("paths.notes") == str(tmp_path / "notas")
    assert cfg.get("paths.recordings") == config.get("paths.recordings")


def test_setup_not_tty_does_not_ask(config, tmp_path):
    def boom(prompt):
        raise AssertionError("não devia perguntar")
    rc = setupmod.cmd_setup(_args(config=str(tmp_path / "x.toml"), yes=False), config, ask=boom,
                            isatty=lambda: False)
    assert rc == 0
