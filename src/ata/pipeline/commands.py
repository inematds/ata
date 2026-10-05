"""Comandos do pipeline: process, import, export, rerender, speakers (ver docs/INTERFACES.md §B)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import bundle, i18n
from ..bundle import BundleError
from ..config import Config
from . import export as exporter
from . import run


def _resolve(target: str, config: Config) -> Path | None:
    try:
        return bundle.resolve(target, config.recordings)
    except BundleError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return None


def _speakers_arg(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--speakers precisa ser um número inteiro") from None
    if not 1 <= n <= 20:
        raise argparse.ArgumentTypeError("--speakers entre 1 e 20 (conta você também)")
    return n


def _lang_arg(value: str) -> str:
    try:
        return i18n.normalize(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


# ---- process ------------------------------------------------------------------------------------------------

def dry_run_report(bdir: Path) -> list[str]:
    meta = bundle.read_meta(bdir)
    offsets = {k: round(v, 3) for k, v in bundle.track_offsets(meta).items()}
    damage = run.effective_damage(meta, bdir)
    return [f"bundle {meta.source_schema} lido: {len(meta.tracks)} tracks, offsets={json.dumps(offsets)}",
            f"danos: {', '.join(damage) if damage else 'nenhum'}"]


def cmd_process(args: argparse.Namespace, config: Config) -> int:
    bdir = _resolve(args.target, config)
    if bdir is None:
        return 2
    try:
        if args.dry_run:
            for line in dry_run_report(bdir):
                print(line)
            return 0
        note = run.process_bundle(bdir, config, language=args.lang, speakers=args.speakers,
                                  summarize=not args.no_summary)
    except BundleError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    print(f"note: {note}")
    return 0


# ---- import -------------------------------------------------------------------------------------------------

def cmd_import(args: argparse.Namespace, config: Config) -> int:
    from .importer import import_file

    src = Path(args.file).expanduser()
    if not src.is_file():
        print(f"erro: arquivo não encontrado: {src}", file=sys.stderr)
        return 2
    bdir = import_file(src, config, title=args.title, language=args.lang)
    print(f"bundle: {bdir}")
    if args.no_process:
        return 0
    note = run.process_bundle(bdir, config, language=args.lang, speakers=args.speakers,
                              summarize=not args.no_summary)
    print(f"note: {note}")
    return 0


# ---- export -------------------------------------------------------------------------------------------------

def cmd_export(args: argparse.Namespace, config: Config) -> int:
    bdir = _resolve(args.target, config)
    if bdir is None:
        return 2
    try:
        text = exporter.export(bdir, args.format)
    except BundleError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 3
    if args.out == "-":
        sys.stdout.write(text)
        return 0
    out = Path(args.out).expanduser() if args.out else bdir / f"{bdir.name}.{args.format}"
    if out.is_dir():
        out = out / f"{bdir.name}.{args.format}"
    out.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text(out, text)
    print(f"exportado: {out}")
    return 0


# ---- rerender / speakers ------------------------------------------------------------------------------------

def cmd_rerender(args: argparse.Namespace, config: Config) -> int:
    bdir = _resolve(args.target, config)
    if bdir is None:
        return 2
    try:
        note = run.rerender(bdir, config)
    except BundleError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 3
    print(f"note: {note}")
    return 0


def parse_assignments(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"use RÓTULO=NOME (ex.: \"Pessoa 2=Ana\"), veio {item!r}")
        label, _, name = item.partition("=")
        label, name = label.strip(), name.strip()
        if not label:
            raise ValueError("rótulo vazio antes do '='")
        out[label] = name
    return out


def cmd_speakers(args: argparse.Namespace, config: Config) -> int:
    bdir = _resolve(args.target, config)
    if bdir is None:
        return 2
    if not (bdir / "turns.json").is_file():
        print(f"erro: {bdir.name}: ainda não processado; rode `ata process`", file=sys.stderr)
        return 3
    turns = bundle.read_turns(bdir)
    labels: list[str] = []
    for t in sorted(turns, key=lambda t: t.start):
        if t.speaker not in labels:
            labels.append(t.speaker)
    names = bundle.read_speaker_names(bdir)
    if not args.assign:
        for label in labels:
            print(f"{label} = {names[label]}" if label in names else f"{label} (sem nome)")
        return 0
    try:
        wanted = parse_assignments(args.assign)
    except ValueError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    unknown = [k for k in wanted if k not in labels]
    if unknown:
        print(f"erro: rótulo inexistente nesta reunião: {', '.join(unknown)} "
              f"(existem: {', '.join(labels)})", file=sys.stderr)
        return 2
    for label, name in wanted.items():
        if name:
            names[label] = name
        else:
            names.pop(label, None)
    bundle.write_speaker_names(bdir, names)
    note = run.rerender(bdir, config)
    print(f"note: {note}")
    return 0


# ---- registro -----------------------------------------------------------------------------------------------

def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("process", help="transcreve e escreve a nota de uma gravação")
    p.add_argument("target", metavar="alvo", help="pasta, nome da gravação, 'latest' ou caminho da nota")
    p.add_argument("--lang", type=_lang_arg, help="pt-BR, en, es ou auto (padrão: o do bundle)")
    p.add_argument("--speakers", type=_speakers_arg, help="total de pessoas, contando você")
    p.add_argument("--dry-run", action="store_true", help="só lê o bundle e mostra offsets e danos")
    p.add_argument("--no-summary", action="store_true", help="não gera resumo")
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("import", help="importa um arquivo de áudio/vídeo como gravação e processa")
    p.add_argument("file", metavar="arquivo")
    p.add_argument("--lang", type=_lang_arg)
    p.add_argument("--title", help="título da reunião (padrão: nome do arquivo)")
    p.add_argument("--speakers", type=_speakers_arg)
    p.add_argument("--no-summary", action="store_true")
    p.add_argument("--no-process", action="store_true", help="só cria o bundle")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("export", help="exporta a transcrição (srt, vtt, txt, json, md, csv)")
    p.add_argument("target", metavar="alvo")
    p.add_argument("--format", required=True, choices=exporter.FORMATS)
    p.add_argument("--out", help="arquivo ou pasta de saída ('-' = tela)")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("rerender", help="refaz a nota sem transcrever de novo")
    p.add_argument("target", metavar="alvo")
    p.set_defaults(func=cmd_rerender)

    p = sub.add_parser("speakers", help="mostra ou define nomes: \"Pessoa 2=Ana\"")
    p.add_argument("target", metavar="alvo")
    p.add_argument("assign", nargs="*", metavar="RÓTULO=NOME")
    p.set_defaults(func=cmd_speakers)
