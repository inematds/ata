"""Dados empacotados: manifesto de motores/modelos (`models.toml`)."""

from __future__ import annotations

import os
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any


def manifest_path() -> Path | None:
    """Caminho do manifesto; ``ATA_MODELS_MANIFEST`` sobrepõe (testes / manifesto local)."""
    env = os.environ.get("ATA_MODELS_MANIFEST")
    if env:
        return Path(env).expanduser()
    return None


def load_manifest(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else manifest_path()
    if p is not None:
        text = p.read_text(encoding="utf-8")
    else:
        text = resources.files(__name__).joinpath("models.toml").read_text(encoding="utf-8")
    data = tomllib.loads(text)
    data.setdefault("release", {})
    data.setdefault("archive", [])
    data.setdefault("model", [])
    return data


def models_by_role(manifest: dict[str, Any], role: str) -> list[dict[str, Any]]:
    return [m for m in manifest.get("model", []) if m.get("role") == role]


def model_entry(manifest: dict[str, Any], name: str) -> dict[str, Any] | None:
    for m in manifest.get("model", []):
        if m.get("name") == name:
            return m
    return None
