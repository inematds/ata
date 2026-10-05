"""Resumo pelos CLIs de assinatura: ``claude -p`` (Claude Code) e ``codex exec`` (Codex). Nunca API.

* claude: ``claude -p --output-format json [--model M] --tools "" --strict-mcp-config --no-session-persistence``,
  prompt + instrução do schema pelo stdin, cwd = pasta temporária vazia, timeout ``summary.timeout_seconds``.
  A saída é o envelope JSON do Claude Code (``{"type": "result", "result": "...", "is_error": false}``);
  o objeto é extraído do ``result``.
* codex: ``codex exec --skip-git-repo-check -s read-only [-m M] --output-last-message <tmp> -``, prompt pelo
  stdin (fechado no fim), mesma pasta temporária; o objeto vem da última mensagem (ou do stdout).
* Binários: ``ATA_CLAUDE_BIN`` / ``ATA_CODEX_BIN`` ou o PATH. Variáveis de chave de API (ANTHROPIC_API_KEY,
  OPENAI_API_KEY...) são REMOVIDAS do ambiente do filho para o CLI usar a assinatura, nunca cobrança por API.
* Erros nunca contêm o texto da reunião nem a saída do CLI.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..config import Config
from .base import EngineError, EngineMissing
from .ollama import check_object

API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY",
                "OPENROUTER_API_KEY", "GROQ_API_KEY")


def schema_instruction(schema: dict[str, Any]) -> str:
    return ("\n\n---\nResponda SOMENTE com um único objeto JSON (sem texto antes ou depois, sem cercas de "
            "código) que valide neste JSON Schema:\n" + json.dumps(schema, ensure_ascii=False))


def find_binary(env_var: str, name: str) -> str | None:
    override = os.environ.get(env_var)
    if override:
        return override if Path(override).is_file() else None
    return shutil.which(name)


def child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in API_KEY_VARS}
    env["NO_COLOR"] = "1"
    return env


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Primeiro objeto JSON do texto: inteiro, dentro de ```json```, ou o primeiro ``{...}`` decodificável."""
    if not isinstance(text, str):
        return None
    s = text.strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    for block in re.findall(r"```(?:json)?\s*(.*?)```", s, re.S):
        try:
            obj = json.loads(block.strip())
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    dec = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(s, i)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


class _CliSummarizer:
    name = "cli"
    env_var = ""
    binary = ""
    missing_hint = ""

    def __init__(self, config: Config, model: str, binary: str | None = None) -> None:
        self.model = model
        self.timeout = float(config.get("summary.timeout_seconds") or 600)
        self.bin = binary or find_binary(self.env_var, self.binary)
        if not self.bin:
            raise EngineMissing(f"{self.binary} não encontrado no PATH ({self.missing_hint}); "
                                f"ou defina {self.env_var}")

    def command(self, workdir: Path) -> list[str]:
        raise NotImplementedError

    def output_text(self, stdout: str, workdir: Path) -> str:
        return stdout

    def summarize(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="ata-llm-") as tmp:
            workdir = Path(tmp)
            try:
                proc = subprocess.run(self.command(workdir), input=prompt + schema_instruction(schema),
                                      capture_output=True, text=True, cwd=workdir, timeout=self.timeout,
                                      env=child_env(), encoding="utf-8", errors="replace")
            except subprocess.TimeoutExpired:
                raise EngineError(f"{self.name} excedeu o tempo limite ({self.timeout:.0f} s)") from None
            except OSError as exc:
                raise EngineMissing(f"não consegui executar {self.name} ({type(exc).__name__})") from None
            if proc.returncode != 0:
                raise EngineError(f"{self.name} saiu com código {proc.returncode} (veja se está logado: "
                                  f"{self.binary} --help)")
            text = self.output_text(proc.stdout, workdir)
        obj = extract_json_object(text)
        if obj is None:
            raise EngineError(f"{self.name} não devolveu um objeto JSON")
        problems = check_object(obj, schema)
        if problems:
            raise EngineError(f"{self.name} devolveu JSON fora do schema: " + "; ".join(problems[:3]))
        return obj


class ClaudeCli(_CliSummarizer):
    name = "claude"
    env_var = "ATA_CLAUDE_BIN"
    binary = "claude"
    missing_hint = "instale o Claude Code e faça login com a assinatura"

    def command(self, workdir: Path) -> list[str]:
        cmd = [str(self.bin), "-p", "--output-format", "json"]
        if self.model:
            cmd += ["--model", self.model]
        return cmd + ["--tools", "", "--strict-mcp-config", "--no-session-persistence"]

    def output_text(self, stdout: str, workdir: Path) -> str:
        try:
            env = json.loads(stdout)
        except json.JSONDecodeError:
            return stdout
        if isinstance(env, list):           # stream de mensagens: pega o último "result"
            results = [m for m in env if isinstance(m, dict) and m.get("type") == "result"]
            env = results[-1] if results else {}
        if not isinstance(env, dict):
            return stdout
        if env.get("is_error"):
            raise EngineError("claude reportou erro na execução (is_error)")
        if isinstance(env.get("structured_output"), dict):
            return json.dumps(env["structured_output"])
        res = env.get("result")
        if isinstance(res, str):
            return res
        if isinstance(res, dict):
            return json.dumps(res)
        return stdout


class CodexCli(_CliSummarizer):
    name = "codex"
    env_var = "ATA_CODEX_BIN"
    binary = "codex"
    missing_hint = "instale o Codex CLI e faça login com a assinatura"
    LAST = "last-message.txt"

    def command(self, workdir: Path) -> list[str]:
        cmd = [str(self.bin), "exec", "--skip-git-repo-check", "-s", "read-only"]
        if self.model:
            cmd += ["-m", self.model]
        return cmd + ["--output-last-message", str(workdir / self.LAST), "-"]

    def output_text(self, stdout: str, workdir: Path) -> str:
        last = workdir / self.LAST
        if last.is_file() and last.read_text(encoding="utf-8", errors="replace").strip():
            return last.read_text(encoding="utf-8", errors="replace")
        return stdout


def make_claude(config: Config, model: str) -> ClaudeCli:
    return ClaudeCli(config, model or str(config.get("summary.claude_model") or ""))


def make_codex(config: Config, model: str) -> CodexCli:
    return CodexCli(config, model or str(config.get("summary.codex_model") or ""))
