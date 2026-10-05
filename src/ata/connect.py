"""Conexões com o segundo cérebro, Obsidian e Agentic OS. Estado em ``<cache>/connections.json``.

- ``connect cerebro --dir P`` (kit astra-2cerebro): ``fontes/AAAA-MM-DD-slug.md`` com o frontmatter do kit
  (``tipo: fonte``, ``subtipo: reuniao``, ``idioma``, ``participants: ["[[nome]]"]``, ``fontes``,
  ``atualizado``) e o resumo + link para a nota; decisões acrescentadas em ``decisoes/registro.md`` no
  formato do kit, uma vez por decisão (marcador ``<!-- ata:<reunião>:dN -->``).
- ``connect obsidian --vault P``: cópia da nota em ``<vault>/Ata/``.
- ``connect agentic-os [--vault P] [--register]``: cópia das notas no vault e a linha
  ``claude mcp add --scope user ata -- ata mcp`` (só executada com ``--register``).

Nunca sobrescreve arquivo editado pelo usuário: o Ata guarda o hash do que escreveu; se o arquivo mudou
desde então (ou não é do Ata: sem marcador ``ata:``), grava ao lado como ``-2``, ``-3``...
``after_note`` aplica todas as conexões ativas + ``knowledge.prep.tick_prep``; erros são logados (só o tipo),
nunca levantados.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import bundle, i18n
from .config import Config

log = logging.getLogger("ata.connect")

TARGETS = ("cerebro", "obsidian", "agentic-os")
MCP_ADD = ["claude", "mcp", "add", "--scope", "user", "ata", "--", "ata", "mcp"]
MCP_REMOVE = ["claude", "mcp", "remove", "--scope", "user", "ata"]
MCP_LINE = "claude mcp add --scope user ata -- ata mcp"

_run: Callable[..., Any] = subprocess.run      # injetável nos testes
_which: Callable[[str], str | None] = shutil.which

_MARKER = re.compile(r"^ata:\s*\"?([^\"\n]*)\"?\s*$", re.M)
_REG_MARK = re.compile(r"<!-- ata:([^ ]+) -->")
_GENERIC = re.compile(r"^(Pessoa|Speaker|Persona) \d+$")

REGISTRO_HEADER = (
    "# Registro de decisões\n\nRegistro cronológico do que foi decidido e por quê.\n\n---\n"
)


# ---- estado ----------------------------------------------------------------------------------------------

def state_path(config: Config) -> Path:
    return config.cache / "connections.json"


def load_connections(config: Config) -> dict[str, dict[str, Any]]:
    p = state_path(config)
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("connections.json ilegível; ignorado")
        return {}
    return d if isinstance(d, dict) else {}


def save_connections(config: Config, data: dict[str, dict[str, Any]]) -> None:
    p = state_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_json(p, data)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---- escrita segura --------------------------------------------------------------------------------------

def _marker_of(text: str) -> str | None:
    head = text.split("\n---", 2)[0] if text.startswith("---") else ""
    m = _MARKER.search(head)
    return m.group(1).strip() if m else None


def _candidates(path: Path):
    yield path
    n = 2
    while True:
        yield path.with_name(f"{path.stem}-{n}{path.suffix}")
        n += 1


def safe_write(path: Path, content: str, meeting: str, files: dict[str, dict[str, str]],
               dry_run: bool = False) -> tuple[Path, str]:
    """Escreve ``content`` em ``path`` sem destruir edição do usuário. Devolve (caminho, ação) com ação em
    created | updated | unchanged | alternate. Atualiza ``files`` ({caminho: {sha, meeting}})."""
    for i, cand in enumerate(_candidates(path)):
        if i > 50:
            raise RuntimeError("muitos arquivos alternativos")
        key = str(cand)
        rec = files.get(key)
        if not cand.exists():
            action = "created" if i == 0 else "alternate"
        else:
            current = cand.read_text(encoding="utf-8", errors="replace")
            if current == content:
                if not dry_run:
                    files[key] = {"sha": _sha(content), "meeting": meeting}
                return cand, "unchanged"
            ours = (rec is not None and rec.get("meeting") == meeting and rec.get("sha") == _sha(current))
            if not ours and rec is None and _marker_of(current) == meeting:
                ours = True     # escrito pelo Ata, registro perdido
            if not ours:
                continue
            action = "updated" if i == 0 else "alternate"
        if not dry_run:
            cand.parent.mkdir(parents=True, exist_ok=True)
            bundle.write_text(cand, content)
            files[key] = {"sha": _sha(content), "meeting": meeting}
        return cand, action
    raise AssertionError("inalcançável")


def with_marker(note: str, meeting: str) -> str:
    """Põe ``ata: "<reunião>"`` no frontmatter (cria um se não houver)."""
    line = f'ata: "{meeting}"'
    if note.startswith("---\n"):
        end = note.find("\n---", 4)
        if end != -1:
            head = note[4:end]
            if _MARKER.search(head):
                head = _MARKER.sub(line, head)
                return "---\n" + head + note[end:]
            return "---\n" + head + "\n" + line + note[end:]
    return f"---\n{line}\n---\n\n" + note


# ---- conteúdo do cérebro ---------------------------------------------------------------------------------

def _yaml(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def participants(bundle_dir: Path) -> list[str]:
    """Nomes reais da reunião (exclui Eu/Me/Yo e rótulos genéricos Pessoa N)."""
    names = bundle.read_speaker_names(bundle_dir)
    found: list[str] = []
    if (bundle_dir / "turns.json").is_file():
        for t in bundle.read_turns(bundle_dir):
            n = names.get(t.speaker, t.speaker)
            if n not in found:
                found.append(n)
    me = {i18n.me_label(lang) for lang in i18n.LANGUAGES}
    return [n for n in found if n not in me and not _GENERIC.match(n)]


def fonte_name(meta: bundle.BundleMeta) -> str:
    slug = meta.slug or bundle.slugify(meta.title) or bundle.slugify(meta.name[11:]) or "reuniao"
    return f"{meta.created_at[:10]}-{slug}"


def render_fonte(bundle_dir: Path, note_path: Path | None, meta: bundle.BundleMeta) -> str:
    from .knowledge.summary import fmt_ts
    lang = meta.language if meta.language in i18n.LANGUAGES else "pt-BR"
    T = i18n.TEXTS[lang]
    summary = bundle.read_summary(bundle_dir) or {}
    people = participants(bundle_dir)
    title = meta.title or meta.name
    fm = ["---", "tipo: fonte", "subtipo: reuniao", f"titulo: {_yaml(title)}", f"data: {meta.created_at[:10]}",
          f"idioma: {lang}",
          "participants: [" + ", ".join(_yaml(f"[[{bundle.slugify(p) or p}]]") for p in people) + "]",
          f"fontes: [{_yaml(str(Path(bundle_dir).resolve()))}]",
          f"atualizado: {date.today().isoformat()}", "tags: [reuniao]", f"ata: {_yaml(meta.name)}", "---", ""]
    body = [f"# {title}", ""]
    if summary.get("tldr"):
        body += [f"## {T['summary']}", "", str(summary["tldr"]), ""]

    def ev(item: dict) -> str:
        e = (item.get("evidence") or [{}])[0] or {}
        return f" ({fmt_ts(float(e.get('t') or 0))})" if e else ""

    for key, head in (("decisions", "decisions"), ("actions", "actions"), ("questions", "questions")):
        items = summary.get(key) or []
        if not items:
            continue
        body += [f"## {T[head]}", ""]
        for it in items:
            extra = ""
            if key == "actions":
                if it.get("owner"):
                    extra += f" — {T['owner']}: {it['owner']}"
                if it.get("due"):
                    extra += f"; {T['due']}: {it['due']}"
            lead = "- [ ] " if key == "actions" else "- "
            body.append(f"{lead}{it.get('text', '')}{extra}{ev(it)}")
        body.append("")
    if not summary:
        body += [T["no_summary"], ""]
    if note_path:
        body += [f"[{T['transcript']}]({Path(note_path).resolve().as_uri()})", ""]
    return "\n".join(fm + body)


def _registro_entries(bundle_dir: Path, meta: bundle.BundleMeta) -> list[tuple[str, str]]:
    from .knowledge.summary import fmt_ts
    summary = bundle.read_summary(bundle_dir) or {}
    names = bundle.read_speaker_names(bundle_dir)
    out = []
    link = fonte_name(meta)
    title = meta.title or meta.name
    for i, d in enumerate(summary.get("decisions") or []):
        text = " ".join(str(d.get("text") or "").split())
        if not text:
            continue
        e = (d.get("evidence") or [{}])[0] or {}
        who = names.get(str(e.get("speaker") or ""), str(e.get("speaker") or "")) or "não informado."
        short = text if len(text) <= 60 else text[:59].rstrip() + "…"
        mark = f"{meta.name}:d{i}"
        block = (f"\n## {meta.created_at[:10]}: {short}\n\n**Decisão:** {text}\n\n"
                 f"**Por quê:** decidido na reunião [[{link}]] ([{title}, {fmt_ts(float(e.get('t') or 0))}]).\n\n"
                 f"**Alternativas consideradas:** não registradas.\n\n**Responsável:** {who}\n\n"
                 f"<!-- ata:{mark} -->\n")
        out.append((mark, block))
    return out


# ---- aplicadores -----------------------------------------------------------------------------------------

def _note_text(bundle_dir: Path, note_path: Path | None) -> str | None:
    for p in (note_path, bundle_dir / "note.md"):
        if p and Path(p).is_file():
            return Path(p).read_text(encoding="utf-8")
    return None


def apply_cerebro(conn: dict[str, Any], bundle_dir: Path, note_path: Path | None, dry_run: bool = False
                  ) -> list[str]:
    root = Path(conn["dir"]).expanduser()
    if not root.is_dir():
        raise FileNotFoundError("pasta do cérebro não existe")
    meta = bundle.read_meta(bundle_dir)
    files = conn.setdefault("files", {})
    report = []
    path, action = safe_write(root / "fontes" / f"{fonte_name(meta)}.md", render_fonte(bundle_dir, note_path, meta),
                              meta.name, files, dry_run)
    report.append(f"{action}: {path}")
    reg = root / "decisoes" / "registro.md"
    entries = _registro_entries(bundle_dir, meta)
    if entries:
        current = reg.read_text(encoding="utf-8") if reg.is_file() else REGISTRO_HEADER
        have = set(_REG_MARK.findall(current))
        new = [b for m, b in entries if m not in have]
        if new:
            if not dry_run:
                reg.parent.mkdir(parents=True, exist_ok=True)
                text = current if current.endswith("\n") else current + "\n"
                bundle.write_text(reg, text + "".join(new))
                conn["registro"] = str(reg)
            report.append(f"{len(new)} decisão(ões) em {reg}")
    return report


def _apply_copy(conn: dict[str, Any], folder: Path, bundle_dir: Path, note_path: Path | None,
                dry_run: bool = False) -> list[str]:
    if not folder.parent.is_dir() and not folder.is_dir():
        raise FileNotFoundError("pasta do vault não existe")
    text = _note_text(bundle_dir, note_path)
    if text is None:
        return []
    meta_name = Path(bundle_dir).name
    files = conn.setdefault("files", {})
    path, action = safe_write(folder / f"{meta_name}.md", with_marker(text, meta_name), meta_name, files, dry_run)
    return [f"{action}: {path}"]


def apply_obsidian(conn, bundle_dir, note_path, dry_run=False):
    return _apply_copy(conn, Path(conn["vault"]).expanduser() / "Ata", bundle_dir, note_path, dry_run)


def apply_agentic(conn, bundle_dir, note_path, dry_run=False):
    vault = Path(conn["vault"]).expanduser()
    if not dry_run:
        vault.mkdir(parents=True, exist_ok=True)
    return _apply_copy(conn, vault, bundle_dir, note_path, dry_run) if (vault.is_dir() or dry_run) else []


APPLIERS = {"cerebro": apply_cerebro, "obsidian": apply_obsidian, "agentic-os": apply_agentic}


def after_note(bundle_dir: Path | str, note_path: Path | str | None, config: Config) -> dict[str, Any]:
    """Aplica as conexões ativas à nota recém-escrita e marca a cola de preparação. Nunca levanta."""
    results: dict[str, Any] = {}
    bdir = Path(bundle_dir)
    note = Path(note_path) if note_path else None
    try:
        conns = load_connections(config)
    except Exception as exc:
        log.warning("conexões ilegíveis: %s", type(exc).__name__)
        conns = {}
    changed = False
    for name, conn in conns.items():
        if not conn.get("enabled", True) or name not in APPLIERS:
            continue
        try:
            results[name] = APPLIERS[name](conn, bdir, note)
            changed = True
        except Exception as exc:
            log.warning("conexão %s falhou: %s", name, type(exc).__name__)
            results[name] = f"erro: {type(exc).__name__}"
    if changed:
        try:
            save_connections(config, conns)
        except Exception as exc:
            log.warning("connections.json não gravado: %s", type(exc).__name__)
    try:
        from .knowledge.prep import tick_prep
        results["prep"] = tick_prep(config, bdir)
    except Exception as exc:
        log.warning("tick_prep falhou: %s", type(exc).__name__)
        results["prep"] = f"erro: {type(exc).__name__}"
    return results


# ---- undo ------------------------------------------------------------------------------------------------

def _strip_registro(text: str) -> tuple[str, int]:
    lines = text.split("\n")
    drop: set[int] = set()
    count = 0
    for i, ln in enumerate(lines):
        if _REG_MARK.search(ln):
            j = i
            while j > 0 and not lines[j].startswith("## "):
                j -= 1
            if lines[j].startswith("## "):
                if j > 0 and lines[j - 1] == "":
                    j -= 1
                drop.update(range(j, i + 1))
                count += 1
    return "\n".join(ln for k, ln in enumerate(lines) if k not in drop), count


def undo(config: Config, target: str, dry_run: bool = False) -> list[str]:
    conns = load_connections(config)
    conn = conns.get(target)
    if not conn:
        return []
    report = []
    for key, rec in sorted((conn.get("files") or {}).items()):
        p = Path(key)
        if not p.is_file():
            continue
        if _sha(p.read_text(encoding="utf-8", errors="replace")) == rec.get("sha"):
            if not dry_run:
                p.unlink()
            report.append(f"removido: {p}")
        else:
            report.append(f"mantido (editado por você): {p}")
    reg = conn.get("registro")
    if reg and Path(reg).is_file():
        text, n = _strip_registro(Path(reg).read_text(encoding="utf-8"))
        if n:
            if not dry_run:
                bundle.write_text(Path(reg), text)
            report.append(f"{n} decisão(ões) do Ata tiradas de {reg}")
    if not dry_run:
        del conns[target]
        save_connections(config, conns)
    return report


# ---- comandos --------------------------------------------------------------------------------------------

def _processed(config: Config) -> list[Path]:
    from .knowledge.index import note_path_for
    out = []
    for b in bundle.list_bundles(config.recordings):
        if note_path_for(b, config) or (b / "summary.json").is_file():
            out.append(b)
    return out


def _print_status(config: Config, only: str | None = None) -> int:
    conns = load_connections(config)
    rows = [(k, v) for k, v in conns.items() if not only or k == only]
    if not rows:
        print("nenhuma conexão ativa" if not only else f"{only}: não conectado")
        return 3
    for k, v in rows:
        where = v.get("dir") or v.get("vault") or ""
        state = "ativa" if v.get("enabled", True) else "pausada"
        extra = " (MCP registrado)" if v.get("registered") else ""
        print(f"{k}: {state} -> {where} · {len(v.get('files') or {})} arquivo(s){extra}")
    return 0


def cmd_connect(args: argparse.Namespace, config: Config) -> int:
    target = getattr(args, "target", None)
    if not target:
        return _print_status(config)
    if args.status:
        return _print_status(config, target)
    if args.undo:
        if target == "agentic-os" and args.register:
            if not _which("claude"):
                print("erro: claude CLI não encontrado no PATH", file=sys.stderr)
                return 4
            if not args.dry_run:
                _run(MCP_REMOVE, check=False)
            print(" ".join(MCP_REMOVE))
        report = undo(config, target, dry_run=args.dry_run)
        if not report and target not in load_connections(config):
            print(f"{target}: não conectado")
            return 3
        for line in report:
            print(("(simulação) " if args.dry_run else "") + line)
        print(f"{target}: desconectado" + (" (simulação)" if args.dry_run else ""))
        return 0

    conn: dict[str, Any] = dict(load_connections(config).get(target) or {})
    if target == "cerebro":
        where = args.dir or conn.get("dir") or config.get("cerebro.dir")
        if not where:
            print("erro: diga a pasta do cérebro: ata connect cerebro --dir <pasta>", file=sys.stderr)
            return 2
        root = Path(where).expanduser()
        if not root.is_dir():
            print(f"erro: pasta não existe: {root}", file=sys.stderr)
            return 2
        if not any((root / x).exists() for x in ("CLAUDE.md", "AGENTS.md", "fontes", "decisoes")):
            print("aviso: a pasta não parece um astra-2cerebro (sem CLAUDE.md/AGENTS.md/fontes)", file=sys.stderr)
        conn["dir"] = str(root)
    else:
        default = config.get("obsidian.vault") if target == "obsidian" else config.get("agentic_os.vault")
        where = args.vault or conn.get("vault") or default
        if not where:
            print(f"erro: diga o vault: ata connect {target} --vault <pasta>", file=sys.stderr)
            return 2
        vault = Path(where).expanduser()
        if target == "obsidian" and not vault.is_dir():
            print(f"erro: vault não existe: {vault}", file=sys.stderr)
            return 2
        conn["vault"] = str(vault)
    conn.setdefault("files", {})
    conn["enabled"] = True
    conn.setdefault("added_at", datetime.now().astimezone().isoformat(timespec="seconds"))

    prefix = "(simulação) " if args.dry_run else ""
    n = 0
    for bdir in _processed(config):
        from .knowledge.index import note_path_for
        try:
            for line in APPLIERS[target](conn, bdir, note_path_for(bdir, config), args.dry_run):
                print(prefix + line)
            n += 1
        except Exception as exc:
            print(f"aviso: {bdir.name}: {type(exc).__name__}", file=sys.stderr)
    if target == "agentic-os":
        print(MCP_LINE)
        if args.register:
            if not _which("claude"):
                print("erro: claude CLI não encontrado no PATH", file=sys.stderr)
                return 4
            if not args.dry_run:
                proc = _run(MCP_ADD, check=False)
                if getattr(proc, "returncode", 0) != 0:
                    print("erro: claude mcp add falhou", file=sys.stderr)
                    return 1
                conn["registered"] = True
        else:
            print("(rode a linha acima, ou repita com --register para o Ata rodar)")
    if not args.dry_run:
        conns = load_connections(config)
        conns[target] = conn
        save_connections(config, conns)
    print(f"{prefix}{target}: conectado ({n} reunião(ões) sincronizada(s))")
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("connect", help="liga o Ata ao segundo cérebro, Obsidian ou Agentic OS")
    p.set_defaults(func=cmd_connect, target=None)
    cs = p.add_subparsers(dest="target", metavar="destino")

    def common(q: argparse.ArgumentParser) -> None:
        q.add_argument("--dry-run", action="store_true", help="mostra o que faria, sem escrever")
        q.add_argument("--undo", action="store_true", help="desconecta e remove o que o Ata escreveu (não editado)")
        q.add_argument("--status", action="store_true", help="mostra o estado da conexão")
        q.set_defaults(func=cmd_connect, dir=None, vault=None, register=False)

    c = cs.add_parser("cerebro", help="kit astra-2cerebro: fontes/ + decisoes/registro.md")
    c.add_argument("--dir", help="pasta do cérebro")
    common(c)
    o = cs.add_parser("obsidian", help="copia as notas para <vault>/Ata/")
    o.add_argument("--vault", help="pasta do vault")
    common(o)
    a = cs.add_parser("agentic-os", help="vault da Memory + registro do MCP (com --register)")
    a.add_argument("--vault", help="pasta do vault (padrão: agentic_os.vault)")
    a.add_argument("--register", action="store_true", help="roda o claude mcp add/remove")
    common(a)
