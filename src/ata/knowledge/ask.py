"""Perguntas sobre as reuniões, com citação ``[título, mm:ss]``.

``ask`` busca evidências (híbrida; restrita a uma reunião quando ``meeting`` é dado), monta um prompt com as
linhas de evidência e pede ao summarizer uma resposta em ``ANSWER_SCHEMA``.

Quando o provedor é ``none`` ou o motor é o ``fake`` (testes, ``ATA_ENGINES=fake``), NÃO há síntese:
``answer`` volta ``None`` e as citações são as próprias evidências — quem chama (agente via MCP, usuário)
lê as evidências. O mesmo acontece se o provedor falhar ou responder fora do schema.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import bundle
from ..config import Config
from . import index
from .summary import _lang, validate

log = logging.getLogger("ata.knowledge.ask")

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {
            "type": "object",
            "properties": {"meeting": {"type": "string"}, "t": {"type": "number"}, "speaker": {"type": "string"}},
            "required": ["meeting", "t", "speaker"], "additionalProperties": False}},
    },
    "required": ["answer", "citations"],
    "additionalProperties": False,
}

_INSTR = {
    "pt-BR": ("Responda à pergunta usando SOMENTE as evidências abaixo, tiradas de reuniões gravadas. Cite cada "
              "afirmação no formato [título, mm:ss]. Se as evidências não bastarem, diga isso. Devolva só o JSON "
              "do schema (answer + citations com meeting = título, t = segundos, speaker)."),
    "en": ("Answer the question using ONLY the evidence below, taken from recorded meetings. Cite every claim as "
           "[title, mm:ss]. If the evidence is not enough, say so. Return only the schema JSON (answer + "
           "citations with meeting = title, t = seconds, speaker)."),
    "es": ("Responde la pregunta usando SOLO la evidencia de abajo, tomada de reuniones grabadas. Cita cada "
           "afirmación como [título, mm:ss]. Si la evidencia no alcanza, dilo. Devuelve solo el JSON del schema "
           "(answer + citations con meeting = título, t = segundos, speaker)."),
}
_WORDS = {
    "pt-BR": {"question": "Pergunta", "evidence": "Evidências", "no_answer": "(sem síntese; veja as evidências)",
              "none": "(nenhuma evidência encontrada)"},
    "en": {"question": "Question", "evidence": "Evidence", "no_answer": "(no synthesis; see the evidence)",
           "none": "(no evidence found)"},
    "es": {"question": "Pregunta", "evidence": "Evidencias", "no_answer": "(sin síntesis; ver las evidencias)",
           "none": "(no se encontró evidencia)"},
}


def build_ask_prompt(question: str, hits: list[index.Hit], language: str) -> str:
    lang = _lang(language)
    lines = [_INSTR[lang], "", f"LANGUAGE: {lang}", "", f"## {_WORDS[lang]['question']}", question.strip(), "",
             f"## {_WORDS[lang]['evidence']}"]
    lines += [h.format(width=600) for h in hits]
    return "\n".join(lines) + "\n"


def _cite(c: dict[str, Any]) -> str:
    from .summary import fmt_ts
    return f"[{c['meeting']}, {fmt_ts(float(c.get('t') or 0))}]"


def _synthesize(config: Config, prompt: str) -> dict[str, Any] | None:
    from ..engines import registry
    if str(config.get("summary.provider")) == "none" or registry.forced_fake():
        return None
    try:
        summarizer = registry.summarizer_for(config)
        if summarizer is None or getattr(summarizer, "name", "") == "fake":
            return None
        out = summarizer.summarize(prompt, ANSWER_SCHEMA)
    except Exception as exc:
        log.warning("ask: resposta falhou: %s", type(exc).__name__)
        return None
    errors = validate(out, ANSWER_SCHEMA)
    if errors:
        log.warning("ask: resposta inválida (%d erros)", len(errors))
        return None
    return out


def ask(config: Config, question: str, *, meeting: str | None = None, limit: int = 8) -> dict[str, Any]:
    """{question, answer (str|None), citations [{meeting, t, speaker, ref}], evidence [Hit.to_json()]}."""
    filters: dict[str, Any] = {}
    bundle_dir: Path | None = None
    if meeting:
        mid = index.resolve_meeting_id(config, meeting)
        filters["meeting"] = mid
        try:
            bundle_dir = bundle.resolve(meeting, config.recordings)
        except bundle.BundleError:
            bundle_dir = None
    hits = index.search(config, question, mode="hybrid", limit=limit, filters=filters)
    lang = str(config.get("language.default") or "pt-BR")
    if bundle_dir is not None:
        try:
            lang = bundle.read_meta(bundle_dir).language
        except bundle.BundleError:
            pass
    out = _synthesize(config, build_ask_prompt(question, hits, lang)) if hits else None
    if out:
        answer: str | None = out["answer"]
        citations = [dict(c) for c in out["citations"]]
    else:
        answer = None
        citations = [{"meeting": h.title, "t": h.start, "speaker": h.speaker} for h in hits]
    for c in citations:
        c["ref"] = _cite(c)
    result = {"question": question, "answer": answer, "citations": citations,
              "evidence": [h.to_json() for h in hits]}
    if bundle_dir is not None:
        append_ask_md(bundle_dir, result, lang)
    return result


def append_ask_md(bundle_dir: Path, result: dict[str, Any], language: str) -> Path:
    W = _WORDS[_lang(language)]
    p = Path(bundle_dir) / "ask.md"
    when = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    block = [f"## {when} — {' '.join(result['question'].split())}", ""]
    block.append(result["answer"] if result["answer"] else W["no_answer"])
    block.append("")
    if result["evidence"]:
        block += [f"- {e['line']}" for e in result["evidence"]]
    else:
        block.append(W["none"])
    block.append("")
    prev = p.read_text(encoding="utf-8") if p.is_file() else ""
    if prev and not prev.endswith("\n"):
        prev += "\n"
    bundle.write_text(p, prev + ("\n" if prev else "") + "\n".join(block))
    return p
