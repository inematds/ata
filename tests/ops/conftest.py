"""Helpers dos testes de operação: roda um comando de um módulo sem passar por ata.cli (que importa módulos
de outras partes ainda em construção)."""

from __future__ import annotations

import argparse
import importlib

import pytest


def run_command(module: str, argv: list[str], config) -> int:
    parser = argparse.ArgumentParser(prog="ata")
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command")
    importlib.import_module(module).add_parser(sub)
    args = parser.parse_args(argv)
    return int(args.func(args, config) or 0)


@pytest.fixture
def run():
    return run_command
