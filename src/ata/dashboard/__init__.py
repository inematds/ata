"""Dashboard local do Ata (`ata dashboard`): http.server em 127.0.0.1, token na 1ª visita, página única."""

from __future__ import annotations

from .server import add_parser

__all__ = ["add_parser"]
