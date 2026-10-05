"""Resumo e embeddings pelo Ollama local (``summary.ollama_host``, padrão http://127.0.0.1:11434).

* ``POST /api/chat`` com ``format`` = JSON Schema, ``stream: false``, ``think: false``, temperatura 0.2;
  o conteúdo de ``message.content`` é JSON; valida as chaves exigidas; uma nova tentativa se vier inválido.
* ``POST /api/embed`` com ``input`` = lista de textos -> ``embeddings``.

Erros nunca carregam o texto da reunião nem o corpo da resposta.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ..config import Config
from .base import EngineError

NOT_RUNNING = "Ollama não está rodando (ollama serve)"
SYSTEM_PROMPT = ("Você resume reuniões. Responda SOMENTE com um objeto JSON que valide no schema pedido, "
                 "no idioma indicado em LANGUAGE. Não invente fatos fora da transcrição.")


def ollama_host(config: Config) -> str:
    return str(config.get("summary.ollama_host") or "http://127.0.0.1:11434").rstrip("/")


def _post(url: str, payload: dict[str, Any], timeout: float, model: str) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise EngineError(f"modelo {model!r} não está no Ollama: ollama pull {model}") from None
        raise EngineError(f"Ollama respondeu HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (ConnectionRefusedError, ConnectionResetError)) or \
                getattr(reason, "errno", None) in (111, 61, 10061):
            raise EngineError(NOT_RUNNING) from None
        if isinstance(reason, TimeoutError) or isinstance(exc, TimeoutError):
            raise EngineError(f"Ollama excedeu o tempo limite ({timeout:.0f} s)") from None
        raise EngineError(f"Ollama inacessível ({type(exc).__name__})") from None
    try:
        out = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EngineError("Ollama devolveu resposta que não é JSON") from None
    if not isinstance(out, dict):
        raise EngineError("Ollama devolveu JSON inesperado")
    return out


def required_keys(schema: dict[str, Any]) -> list[str]:
    req = schema.get("required")
    if isinstance(req, list):
        return [str(k) for k in req]
    props = schema.get("properties")
    return list(props) if isinstance(props, dict) else []


def check_object(obj: Any, schema: dict[str, Any]) -> list[str]:
    """Problemas (sem conteúdo) do objeto frente ao schema: tipo e chaves exigidas."""
    if not isinstance(obj, dict):
        return ["não é objeto JSON"]
    return [f"falta a chave {k!r}" for k in required_keys(schema) if k not in obj]


class OllamaSummarizer:
    name = "ollama"

    def __init__(self, config: Config, model: str) -> None:
        self.host = ollama_host(config)
        self.model = model or str(config.get("summary.ollama_model"))
        self.timeout = float(config.get("summary.timeout_seconds") or 600)

    def payload(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {"model": self.model, "stream": False, "think": False, "format": schema,
                "options": {"temperature": 0.2},
                "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": prompt}]}

    def summarize(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        problems: list[str] = []
        for _attempt in range(2):
            out = _post(self.host + "/api/chat", self.payload(prompt, schema), self.timeout, self.model)
            content = (out.get("message") or {}).get("content") if isinstance(out.get("message"), dict) else None
            if not isinstance(content, str):
                problems = ["resposta sem message.content"]
                continue
            try:
                obj = json.loads(content)
            except json.JSONDecodeError:
                problems = ["conteúdo não é JSON"]
                continue
            problems = check_object(obj, schema)
            if not problems:
                return obj
        raise EngineError(f"Ollama ({self.model}) não devolveu JSON válido no schema após 2 tentativas: "
                          + "; ".join(problems[:3]))


class OllamaTextEmbedder:
    name = "ollama"

    def __init__(self, config: Config, model: str) -> None:
        self.host = ollama_host(config)
        self.model = model or str(config.get("embeddings.model"))
        self.timeout = float(config.get("summary.timeout_seconds") or 600)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out = _post(self.host + "/api/embed", {"model": self.model, "input": list(texts)}, self.timeout,
                    self.model)
        embs = out.get("embeddings")
        if not isinstance(embs, list) or len(embs) != len(texts):
            raise EngineError("Ollama /api/embed devolveu número de vetores diferente do pedido")
        return [[float(x) for x in v] for v in embs]


def list_models(config: Config, timeout: float = 3.0) -> list[str]:
    """Nomes em ``/api/tags`` (usado pelo doctor). EngineError se não responder."""
    req = urllib.request.Request(ollama_host(config) + "/api/tags")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        raise EngineError(NOT_RUNNING) from None
    return [str(m.get("name") or m.get("model")) for m in data.get("models", []) if isinstance(m, dict)]


def make_summarizer(config: Config, model: str) -> OllamaSummarizer:
    return OllamaSummarizer(config, model)


def make_text_embedder(config: Config, model: str) -> OllamaTextEmbedder:
    return OllamaTextEmbedder(config, model)
