"""Comandos de conhecimento: ``search``, ``ask``, ``prep``, ``actions``, ``reindex``.

Códigos de saída (CONTRATO §6): 0 ok · 2 uso inválido · 3 nada encontrado (busca/pergunta sem evidência,
lista vazia, item inexistente em ``--done``).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ..config import Config


def _dump(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_search(args: argparse.Namespace, config: Config) -> int:
    from . import index
    filters = {"since": args.since, "until": args.until, "speaker": args.speaker, "language": args.language,
               "meeting": index.resolve_meeting_id(config, args.meeting) if args.meeting else None}
    try:
        hits = index.search(config, args.query, mode=args.mode, limit=args.limit,
                            filters={k: v for k, v in filters.items() if v})
    except ValueError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    if args.json:
        _dump([h.to_json() for h in hits])
    else:
        for h in hits:
            print(h.format())
        if not hits:
            print("nenhum resultado", file=sys.stderr)
    return 0 if hits else 3


def cmd_ask(args: argparse.Namespace, config: Config) -> int:
    from .ask import ask
    res = ask(config, args.question, meeting=args.meeting, limit=args.limit)
    if args.json:
        _dump(res)
    else:
        if res["answer"]:
            print(res["answer"])
            print()
        elif res["evidence"]:
            print("(sem síntese do modelo; evidências abaixo)")
        for e in res["evidence"]:
            print(e["line"])
        if not res["evidence"]:
            print("nenhuma evidência encontrada", file=sys.stderr)
    return 0 if res["evidence"] else 3


def cmd_prep(args: argparse.Namespace, config: Config) -> int:
    from .prep import build_prep, prep_path
    if args.days < 1:
        print("erro: --days precisa ser >= 1", file=sys.stderr)
        return 2
    md = build_prep(config, args.query, days=args.days, calendar=args.calendar, dry_run=args.dry_run)
    if args.dry_run:
        print(md, end="")
    else:
        print(f"prep: {prep_path(config, args.query)}")
    return 0


def cmd_actions(args: argparse.Namespace, config: Config) -> int:
    from . import actions as A
    if args.done:
        full = A.set_done(config, args.done, done=not args.undo)
        if not full:
            print(f"erro: item não encontrado (ou prefixo ambíguo): {args.done}", file=sys.stderr)
            return 3
        print(f"{'reaberto' if args.undo else 'feito'}: {full}")
        return 0
    kind = "decision" if args.decisions else ("question" if args.questions else "action")
    status = "all" if args.all else args.status
    try:
        items = A.list_items(config, kind=kind, owner=args.owner, since=args.since, status=status)
    except ValueError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    if args.json:
        _dump(items)
    else:
        for it in items:
            print(A.format_item(it))
        if not items:
            print("nada em aberto" if status == "open" else "nada encontrado", file=sys.stderr)
    return 0 if items else 3


def cmd_reindex(args: argparse.Namespace, config: Config) -> int:
    from .index import db_path, reindex
    res = reindex(config)
    print(f"índice: {res['meetings']} reuniões, {res['chunks']} trechos ({db_path(config)})")
    if res["failed"]:
        print(f"aviso: {res['failed']} reuniões não puderam ser indexadas (veja o log)", file=sys.stderr)
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("search", help="busca nas reuniões (híbrida: palavras + significado)")
    p.add_argument("query", metavar="consulta")
    p.add_argument("--mode", choices=("hybrid", "lexical", "semantic"), default="hybrid")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--since", help="AAAA-MM-DD ou 7d/2w/3m")
    p.add_argument("--until", help="AAAA-MM-DD")
    p.add_argument("--speaker", help="só falas desta pessoa")
    p.add_argument("--language", choices=("pt-BR", "en", "es"))
    p.add_argument("--meeting", help="só nesta reunião (pasta, nome ou latest)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("ask", help="pergunta sobre as reuniões, com citação [título, mm:ss]")
    p.add_argument("question", metavar="pergunta")
    p.add_argument("--meeting", metavar="alvo", help="só nesta reunião (grava em ask.md do bundle)")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("prep", help="cola pré-reunião a partir das reuniões anteriores")
    p.add_argument("query", metavar="consulta", help="título, pessoa ou assunto")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--calendar", metavar="TEXTO", help="texto do evento (descrição, convidados)")
    p.add_argument("--dry-run", action="store_true", help="só mostra, não grava")
    p.set_defaults(func=cmd_prep)

    p = sub.add_parser("actions", help="ações (ou decisões) entre reuniões")
    p.add_argument("--decisions", action="store_true", help="lista decisões em vez de ações")
    p.add_argument("--questions", action="store_true", help="lista perguntas em aberto")
    p.add_argument("--owner", help="responsável (ou falante, para decisões)")
    p.add_argument("--since", help="AAAA-MM-DD ou 7d/2w/3m")
    p.add_argument("--status", choices=("open", "done", "all"), default="open")
    p.add_argument("--all", action="store_true", help="= --status all")
    p.add_argument("--done", metavar="ID", help="marca o item como feito")
    p.add_argument("--undo", action="store_true", help="com --done: reabre o item")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_actions)

    p = sub.add_parser("reindex", help="reconstrói o índice de busca a partir das gravações")
    p.set_defaults(func=cmd_reindex)
