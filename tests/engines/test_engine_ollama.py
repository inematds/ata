"""Cliente Ollama contra servidor HTTP fake."""

from __future__ import annotations

import json

import pytest

from ata.engines import ollama
from ata.engines.base import EngineError, Summarizer, TextEmbedder

SCHEMA = {"type": "object", "required": ["language", "tldr"],
          "properties": {"language": {"type": "string"}, "tldr": {"type": "string"}}}
GOOD = {"language": "pt-BR", "tldr": "lançamento no dia doze"}


def _cfg(config, port):
    return config.with_overrides(summary__ollama_host=f"http://127.0.0.1:{port}", summary__timeout_seconds=5)


def _chat(content):
    return 200, {"model": "m", "message": {"role": "assistant", "content": content}, "done": True}


def test_summarize_payload_and_parse(config, fake_http):
    srv = fake_http({("POST", "/api/chat"): _chat(json.dumps(GOOD))})
    s = ollama.make_summarizer(_cfg(config, srv.port), "")
    assert isinstance(s, Summarizer)
    assert s.summarize("LANGUAGE: pt-BR\n[00:01] Eu: oi", SCHEMA) == GOOD
    body = json.loads(srv.requests[0]["body"])
    assert body["model"] == "qwen3.6:35b-a3b"
    assert body["format"] == SCHEMA and body["stream"] is False and body["think"] is False
    assert body["options"]["temperature"] == 0.2
    assert body["messages"][-1] == {"role": "user", "content": "LANGUAGE: pt-BR\n[00:01] Eu: oi"}


def test_retry_once_then_success(config, fake_http):
    answers = iter([_chat("isso não é json"), _chat(json.dumps(GOOD))])
    srv = fake_http({("POST", "/api/chat"): lambda req: next(answers)})
    assert ollama.make_summarizer(_cfg(config, srv.port), "m").summarize("p", SCHEMA) == GOOD
    assert len(srv.requests) == 2


def test_two_invalid_raise_without_leaking(config, fake_http):
    srv = fake_http({("POST", "/api/chat"): _chat('{"language": "pt-BR", "x": "PALAVRASECRETA"}')})
    with pytest.raises(EngineError) as exc:
        ollama.make_summarizer(_cfg(config, srv.port), "m").summarize("PALAVRASECRETA no prompt", SCHEMA)
    assert "tldr" in str(exc.value) and "PALAVRASECRETA" not in str(exc.value)
    assert len(srv.requests) == 2


def test_refused(config, closed_port):
    with pytest.raises(EngineError) as exc:
        ollama.make_summarizer(_cfg(config, closed_port), "m").summarize("p", SCHEMA)
    assert str(exc.value) == "Ollama não está rodando (ollama serve)"


def test_model_missing_404(config, fake_http):
    srv = fake_http({("POST", "/api/chat"): (404, {"error": "model not found"})})
    with pytest.raises(EngineError) as exc:
        ollama.make_summarizer(_cfg(config, srv.port), "gemma4:26b").summarize("p", SCHEMA)
    assert "ollama pull gemma4:26b" in str(exc.value)


def test_embed(config, fake_http):
    srv = fake_http({("POST", "/api/embed"): (200, {"embeddings": [[0.1, 0.2], [0.3, 0.4]]})})
    e = ollama.make_text_embedder(_cfg(config, srv.port), "")
    assert isinstance(e, TextEmbedder)
    assert e.embed_texts(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]
    body = json.loads(srv.requests[0]["body"])
    assert body == {"model": "qwen3-embedding:0.6b", "input": ["a", "b"]}
    assert e.embed_texts([]) == []


def test_embed_count_mismatch(config, fake_http):
    srv = fake_http({("POST", "/api/embed"): (200, {"embeddings": [[0.1]]})})
    with pytest.raises(EngineError):
        ollama.make_text_embedder(_cfg(config, srv.port), "m").embed_texts(["a", "b"])


def test_list_models(config, fake_http, closed_port):
    srv = fake_http({("GET", "/api/tags"): (200, {"models": [{"name": "qwen3.6:35b-a3b"}, {"model": "bge-m3"}]})})
    assert ollama.list_models(_cfg(config, srv.port)) == ["qwen3.6:35b-a3b", "bge-m3"]
    with pytest.raises(EngineError):
        ollama.list_models(_cfg(config, closed_port))


def test_check_object():
    assert ollama.check_object([], SCHEMA) == ["não é objeto JSON"]
    assert ollama.check_object({"language": "en"}, SCHEMA) == ["falta a chave 'tldr'"]
    assert ollama.required_keys({"properties": {"a": {}, "b": {}}}) == ["a", "b"]
