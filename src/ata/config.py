"""Configuração do Ata: TOML em ~/.config/ata/config.toml (ou $ATA_CONFIG), mesclado sobre DEFAULTS.

Uso: ``cfg = load_config(); cfg.get("summary.provider"); cfg.path("paths.notes")``.
"""

from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, dict[str, Any]] = {
    "paths": {
        "recordings": "~/Ata/gravacoes",
        "notes": "~/Ata/notas",
        "models": "~/.cache/ata/models",
        "cache": "~/.cache/ata",
    },
    "language": {"default": "pt-BR"},
    "asr": {
        "pt-BR": "nemo:parakeet-tdt-0.6b-v3",
        "en": "nemo:parakeet-tdt-0.6b-v3",
        "es": "nemo:parakeet-tdt-0.6b-v3",
        "fallback": "onnx:nemo-parakeet-tdt-0.6b-v3",
    },
    "diarization": {"engine": "nemo", "fallback": "onnx", "max_speakers": 0, "mic_speakers": 1},
    "gate": {"enabled": True, "margin_db": 3.0},
    "cleanup": {"glossary": "~/.config/ata/glossario.txt", "extra_fillers": []},
    "summary": {
        "provider": "ollama",
        "ollama_model": "qwen3.6:35b-a3b",
        "ollama_host": "http://127.0.0.1:11434",
        "claude_model": "sonnet",
        "codex_model": "",
        "timeout_seconds": 600,
    },
    "embeddings": {"provider": "ollama", "model": "qwen3-embedding:0.6b"},
    "engine": {"prefer": "auto", "host": "127.0.0.1", "port": 47520, "memory_max": "24G"},
    "live": {"enabled": False, "engine": "nemo", "chunk_ms": 1120},
    "voices": {"enabled": False, "threshold": 0.72},
    "dashboard": {"port": 47530},
    "cerebro": {"dir": ""},
    "obsidian": {"vault": ""},
    "agentic_os": {"vault": "~/Documents/Obsidian/Ata"},
    "privacy": {"level": 0},
}

SUMMARY_PROVIDERS = ("none", "ollama", "claude", "codex")


def default_config_path() -> Path:
    env = os.environ.get("ATA_CONFIG")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "ata" / "config.toml"


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class ConfigError(ValueError):
    pass


class Config:
    def __init__(self, data: dict[str, Any], source: Path | None = None) -> None:
        self.data = data
        self.source = source

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, name: str) -> dict[str, Any]:
        return dict(self.data.get(name, {}))

    def path(self, dotted: str) -> Path:
        value = self.get(dotted)
        if not value:
            raise ConfigError(f"{dotted} não configurado")
        return Path(str(value)).expanduser()

    @property
    def recordings(self) -> Path:
        return self.path("paths.recordings")

    @property
    def notes(self) -> Path:
        return self.path("paths.notes")

    @property
    def cache(self) -> Path:
        return self.path("paths.cache")

    def with_overrides(self, **dotted: Any) -> "Config":
        data = copy.deepcopy(self.data)
        for key, value in dotted.items():
            sec, _, name = key.replace("__", ".").partition(".")
            data.setdefault(sec, {})[name] = value
        return Config(data, self.source)


def validate(data: dict[str, Any]) -> None:
    provider = data["summary"]["provider"]
    if provider not in SUMMARY_PROVIDERS:
        raise ConfigError(f"summary.provider inválido: {provider!r} (use {', '.join(SUMMARY_PROVIDERS)})")
    margin = data["gate"]["margin_db"]
    if not isinstance(margin, (int, float)) or isinstance(margin, bool) or not 0 <= margin <= 40:
        raise ConfigError("gate.margin_db deve ser número entre 0 e 40")
    level = data["privacy"]["level"]
    if level not in (0, 1):
        raise ConfigError("privacy.level deve ser 0 (tudo local) ou 1 (assinatura claude/codex)")
    if provider in ("claude", "codex") and level == 0:
        raise ConfigError(f"summary.provider = {provider!r} manda texto para fora: ajuste privacy.level = 1")


def load_config(path: Path | str | None = None) -> Config:
    p = Path(path).expanduser() if path else default_config_path()
    data = copy.deepcopy(DEFAULTS)
    if p.is_file():
        try:
            user = tomllib.loads(p.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{p}: TOML inválido ({exc})") from None
        data = _merge(data, user)
    validate(data)
    return Config(data, p if p.is_file() else None)


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def dumps(data: dict[str, Any]) -> str:
    lines = ["# Configuração do Ata — docs/CONTRATO.md §5", ""]
    for sec, values in data.items():
        lines.append(f"[{sec}]")
        for k, v in values.items():
            key = k if k.replace("_", "").isalnum() else f'"{k}"'
            lines.append(f"{key} = {_toml_value(v)}")
        lines.append("")
    return "\n".join(lines)


def write_config(data: dict[str, Any], path: Path | None = None) -> Path:
    p = path or default_config_path()
    validate(_merge(DEFAULTS, data))
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(dumps(_merge(DEFAULTS, data)), encoding="utf-8")
    os.replace(tmp, p)
    return p
