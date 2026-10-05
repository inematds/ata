"""Comando ``ata setup``: escreve a config (``config.write_config``) com idioma, pastas e provedor de resumo.

``--summary claude|codex`` liga ``privacy.level = 1`` e avisa que o TEXTO da transcrição sai da máquina
(nunca o áudio). Perguntas interativas só sem ``--yes`` e com stdin num terminal.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Any, Callable

from . import i18n
from .config import SUMMARY_PROVIDERS, Config, DEFAULTS, default_config_path, write_config

DESTINATIONS = {"claude": "Anthropic (claude CLI, assinatura)", "codex": "OpenAI (codex CLI, assinatura)"}


def privacy_warning(provider: str) -> str:
    return (f"aviso: com summary = {provider}, o TEXTO da transcrição (nunca o áudio) sai da máquina para "
            f"{DESTINATIONS[provider]}. privacy.level = 1. Para voltar ao cofre local: "
            "ata setup --summary ollama")


def _existing(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _ask(prompt: str, default: str, ask: Callable[[str], str]) -> str:
    try:
        ans = ask(f"{prompt} [{default}]: ").strip()
    except EOFError:
        ans = ""
    return ans or default


def build_data(base: dict[str, Any], *, lang: str, notes: str, recordings: str, summary: str) -> dict[str, Any]:
    data = {k: dict(v) for k, v in base.items() if isinstance(v, dict)}
    data.setdefault("language", {})["default"] = lang
    data.setdefault("paths", {})["notes"] = notes
    data["paths"]["recordings"] = recordings
    data.setdefault("summary", {})["provider"] = summary
    data.setdefault("privacy", {})["level"] = 1 if summary in ("claude", "codex") else 0
    return data


def cmd_setup(args: Any, config: Config, *, ask: Callable[[str], str] = input,
              isatty: Callable[[], bool] | None = None) -> int:
    target = Path(getattr(args, "config", None) or config.source or default_config_path()).expanduser()
    base = _existing(target)
    cur = lambda dotted: config.get(dotted)  # noqa: E731
    lang = args.lang or str(cur("language.default") or "pt-BR")
    notes = args.notes or str(cur("paths.notes") or DEFAULTS["paths"]["notes"])
    recordings = args.recordings or str(cur("paths.recordings") or DEFAULTS["paths"]["recordings"])
    summary = args.summary or str(cur("summary.provider") or "ollama")
    interactive = not args.yes and (isatty or sys.stdin.isatty)()
    if interactive:
        lang = _ask("idioma padrão (pt-BR, en, es)", lang, ask) if not args.lang else lang
        notes = _ask("pasta das notas", notes, ask) if not args.notes else notes
        recordings = _ask("pasta das gravações", recordings, ask) if not args.recordings else recordings
        summary = _ask("resumo (none, ollama, claude, codex)", summary, ask) if not args.summary else summary
    try:
        lang = i18n.normalize(lang)
    except ValueError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    if summary not in SUMMARY_PROVIDERS:
        print(f"erro: --summary inválido: {summary!r} (use {', '.join(SUMMARY_PROVIDERS)})", file=sys.stderr)
        return 2
    data = build_data(base, lang=lang, notes=notes, recordings=recordings, summary=summary)
    path = write_config(data, target)
    for p in (notes, recordings):
        Path(p).expanduser().mkdir(parents=True, exist_ok=True)
    print(f"config gravada em {path}")
    print(f"idioma: {lang} · notas: {notes} · gravações: {recordings} · resumo: {summary}")
    if summary in ("claude", "codex"):
        print(privacy_warning(summary), file=sys.stderr)
    elif summary == "ollama":
        model = (data.get("summary") or {}).get("ollama_model") or DEFAULTS["summary"]["ollama_model"]
        print(f"resumo local pelo Ollama ({model}); se faltar: ollama pull {model}")
    print("próximo passo: ata doctor")
    return 0


def add_parser(sub: Any) -> None:
    p = sub.add_parser("setup", help="configura idioma, pastas e resumo")
    p.add_argument("--yes", "-y", action="store_true", help="não pergunta nada (usa padrões e flags)")
    p.add_argument("--lang", help="pt-BR, en ou es")
    p.add_argument("--notes", help="pasta das notas Markdown")
    p.add_argument("--recordings", help="pasta das gravações")
    p.add_argument("--summary", choices=SUMMARY_PROVIDERS, help="provedor do resumo")
    p.set_defaults(func=cmd_setup)
