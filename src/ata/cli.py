"""CLI `ata`. Cada módulo de comando expõe ``add_parser(subparsers)`` e define ``func(args, config) -> int``.

Códigos de saída (docs/CONTRATO.md §6): 0 ok · 1 falha · 2 uso · 3 nada a fazer · 4 dependência ausente.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Sequence

from . import __version__
from .config import ConfigError, load_config
from .engines.base import EngineError, EngineMissing

# ordem = ordem no --help
COMMAND_MODULES = (
    "ata.setup",            # setup
    "ata.recorder",         # start stop toggle status
    "ata.pipeline.commands",  # process import export rerender speakers
    "ata.demo",             # demo
    "ata.knowledge.commands",  # search ask prep actions reindex
    "ata.voices",           # voices
    "ata.connect",          # connect
    "ata.live",             # live
    "ata.mcp_server",       # mcp
    "ata.dashboard",        # dashboard
    "ata.engine_cmd",       # engine
    "ata.doctor",           # doctor privacy
    "ata.bench",            # bench
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ata",
        description="Ata: notas de reunião local-first. Grava em duas faixas, transcreve na sua máquina "
                    "(pt-BR, en, es) e escreve uma nota com cada fala por pessoa.")
    parser.add_argument("--version", action="version", version=f"ata {__version__}")
    parser.add_argument("--config", help="arquivo de config (padrão: $ATA_CONFIG ou ~/.config/ata/config.toml)")
    sub = parser.add_subparsers(dest="command", metavar="comando")
    for name in COMMAND_MODULES:
        importlib.import_module(name).add_parser(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"erro: config: {exc}", file=sys.stderr)
        return 2
    try:
        return int(args.func(args, config) or 0)
    except EngineMissing as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 4
    except (EngineError, ConfigError) as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
