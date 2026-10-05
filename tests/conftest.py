"""Fixtures comuns: config isolada em tmp, motores fake, reunião sintética de duas faixas."""

from __future__ import annotations

from pathlib import Path

import pytest

from ata import config as cfgmod
from ata.testing import DEFAULT_LINES_PT, synth_meeting


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Nenhum teste toca ~/.config, ~/Ata ou a rede; motores sempre fake."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("ATA_ENGINES", "fake")
    monkeypatch.delenv("ATA_CONFIG", raising=False)
    yield


@pytest.fixture
def config(tmp_path):
    data = {
        "paths": {"recordings": str(tmp_path / "gravacoes"), "notes": str(tmp_path / "notas"),
                  "models": str(tmp_path / "models"), "cache": str(tmp_path / "cache")},
        "summary": {"provider": "ollama"},
    }
    path = cfgmod.write_config(data, tmp_path / "config.toml")
    return cfgmod.load_config(path)


@pytest.fixture
def meeting(config) -> Path:
    """Bundle ata/1 sintético em pt-BR (Eu + 2 pessoas no far, vazamento -18 dB)."""
    return synth_meeting(config.recordings, DEFAULT_LINES_PT, title="lançamento beta")
