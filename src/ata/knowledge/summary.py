"""Resumo estruturado de uma reunião (docs/CONTRATO.md §4).

``summarize_turns`` monta o prompt (instrução no idioma da reunião + modelo por tipo de reunião + transcrição
``[mm:ss] Falante: texto``), chama o summarizer do registro com ``SUMMARY_SCHEMA`` e valida a saída com um
validador próprio (subconjunto de JSON Schema). Saída inválida ganha UMA nova tentativa de reparo; erro do
provedor nunca derruba o pipeline: loga só o tipo da exceção e devolve ``None``.

Modelos por tipo de reunião: ``<dir da config>/templates/<nome>.md`` (do usuário) vencem os padrões do pacote
(``ata/knowledge/templates/{geral,standup,vendas,entrevista}.md``). Cada arquivo tem seções ``## pt-BR``,
``## en`` e ``## es``; usa-se a seção do idioma da reunião (ou o arquivo inteiro se não houver seções).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterable

from .. import i18n
from ..config import Config, default_config_path
from ..types import Turn

log = logging.getLogger("ata.knowledge.summary")

MAX_TRANSCRIPT_CHARS = 120_000
PACKAGE_TEMPLATES = Path(__file__).parent / "templates"
DEFAULT_TEMPLATE = "geral"

_EVIDENCE = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"t": {"type": "number"}, "speaker": {"type": "string"}},
        "required": ["t", "speaker"],
        "additionalProperties": False,
    },
}

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "language": {"type": "string"},
        "tldr": {"type": "string"},
        "topics": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "start": {"type": "number"}, "summary": {"type": "string"}},
            "required": ["title", "start", "summary"], "additionalProperties": False}},
        "decisions": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "evidence": _EVIDENCE},
            "required": ["text", "evidence"], "additionalProperties": False}},
        "actions": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "owner": {"type": ["string", "null"]},
                           "due": {"type": ["string", "null"]}, "evidence": _EVIDENCE},
            "required": ["text", "owner", "due", "evidence"], "additionalProperties": False}},
        "questions": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "evidence": _EVIDENCE},
            "required": ["text", "evidence"], "additionalProperties": False}},
    },
    "required": ["language", "tldr", "topics", "decisions", "actions", "questions"],
    "additionalProperties": False,
}

# ---- validador mínimo (type, properties, required, additionalProperties, items, enum) -------------------

_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,), "number": (int, float), "integer": (int,), "boolean": (bool,),
    "array": (list,), "object": (dict,), "null": (type(None),),
}


def _type_ok(value: Any, name: str) -> bool:
    if name in ("number", "integer") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[name])


def _validate(obj: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    want = schema.get("type")
    if want is not None:
        names = want if isinstance(want, list) else [want]
        if not any(_type_ok(obj, n) for n in names):
            errors.append(f"{path}: esperado {'|'.join(names)}, veio {type(obj).__name__}")
            return
    if "enum" in schema and obj not in schema["enum"]:
        errors.append(f"{path}: valor fora de {schema['enum']}")
    if isinstance(obj, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in obj:
                errors.append(f"{path}.{key}: obrigatório")
        if schema.get("additionalProperties") is False:
            for key in obj:
                if key not in props:
                    errors.append(f"{path}.{key}: campo não permitido")
        for key, sub in props.items():
            if key in obj:
                _validate(obj[key], sub, f"{path}.{key}", errors)
    if isinstance(obj, list) and "items" in schema:
        for i, item in enumerate(obj):
            _validate(item, schema["items"], f"{path}[{i}]", errors)


def validate(obj: Any, schema: dict[str, Any] | None = None) -> list[str]:
    """Erros de validação (caminhos + tipos, sem o texto dos campos). Lista vazia = válido."""
    errors: list[str] = []
    _validate(obj, schema or SUMMARY_SCHEMA, "$", errors)
    return errors


# ---- prompt ----------------------------------------------------------------------------------------------

INSTRUCTIONS: dict[str, str] = {
    "pt-BR": (
        "Você é um assistente que resume reuniões. Leia a transcrição abaixo (cada linha é "
        "[minuto:segundo] Falante: fala) e devolva SOMENTE um objeto JSON que siga o schema fornecido.\n"
        "Regras:\n"
        "- Escreva tudo em português do Brasil.\n"
        "- tldr: uma ou duas frases com o essencial.\n"
        "- topics: os assuntos em ordem, com o segundo em que começam (start) e um resumo curto.\n"
        "- decisions: só o que foi de fato decidido; evidence aponta o segundo (t) e quem falou.\n"
        "- actions: tarefas combinadas; owner e due ficam null quando não foram ditos (não invente).\n"
        "- O falante \"Eu\" é o dono da gravação.\n"
        "- questions: perguntas que ficaram sem resposta.\n"
        "- Use apenas o que está na transcrição."
    ),
    "en": (
        "You are an assistant that summarizes meetings. Read the transcript below (each line is "
        "[minute:second] Speaker: utterance) and return ONLY a JSON object that follows the given schema.\n"
        "Rules:\n"
        "- Write everything in English.\n"
        "- tldr: one or two sentences with the gist.\n"
        "- topics: subjects in order, with the second they start (start) and a short summary.\n"
        "- decisions: only what was actually decided; evidence points to the second (t) and the speaker.\n"
        "- actions: agreed tasks; owner and due are null when not stated (do not invent).\n"
        "- The speaker \"Me\" is the person who recorded the meeting.\n"
        "- questions: questions left unanswered.\n"
        "- Use only what is in the transcript."
    ),
    "es": (
        "Eres un asistente que resume reuniones. Lee la transcripción de abajo (cada línea es "
        "[minuto:segundo] Hablante: frase) y devuelve SOLO un objeto JSON que siga el schema dado.\n"
        "Reglas:\n"
        "- Escribe todo en español.\n"
        "- tldr: una o dos frases con lo esencial.\n"
        "- topics: los temas en orden, con el segundo en que empiezan (start) y un resumen corto.\n"
        "- decisions: solo lo que de verdad se decidió; evidence indica el segundo (t) y quién habló.\n"
        "- actions: tareas acordadas; owner y due quedan null cuando no se dijeron (no inventes).\n"
        "- El hablante \"Yo\" es quien grabó la reunión.\n"
        "- questions: preguntas que quedaron sin respuesta.\n"
        "- Usa solo lo que está en la transcripción."
    ),
}

_HEAD = {
    "pt-BR": {"template": "Modelo desta reunião", "notes": "Anotações do dono da gravação (use como pistas)",
              "transcript": "Transcrição", "omitted": "(trecho omitido)",
              "repair": "A resposta anterior não passou na validação ({errors}). Responda de novo, só o JSON."},
    "en": {"template": "Meeting template", "notes": "Notes from the person who recorded (use as hints)",
           "transcript": "Transcript", "omitted": "(section omitted)",
           "repair": "The previous answer failed validation ({errors}). Answer again, JSON only."},
    "es": {"template": "Plantilla de esta reunión", "notes": "Notas de quien grabó (úsalas como pistas)",
           "transcript": "Transcripción", "omitted": "(fragmento omitido)",
           "repair": "La respuesta anterior no pasó la validación ({errors}). Responde de nuevo, solo el JSON."},
}

_TRANSCRIPT_LINE = re.compile(r"^\[(\d+):(\d\d)\]")


def _lang(language: str | None) -> str:
    try:
        lang = i18n.normalize(language)
    except ValueError:
        return i18n.DEFAULT_LANGUAGE
    return i18n.DEFAULT_LANGUAGE if lang == "auto" else lang


def fmt_ts(seconds: float) -> str:
    """Segundos -> ``mm:ss`` (minutos totais, então 75 min vira ``75:03``)."""
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def transcript_lines(turns: Iterable[Turn]) -> list[str]:
    out = []
    for t in turns:
        text = " ".join(str(t.text).split())
        if not text:
            continue
        speaker = str(t.speaker).replace(":", " ").strip() or "?"
        out.append(f"[{fmt_ts(t.start)}] {speaker}: {text}")
    return out


def fit_lines(lines: list[str], budget: int = MAX_TRANSCRIPT_CHARS, marker: str = "(...)") -> list[str]:
    """Mantém no máximo ``budget`` caracteres: início e fim inteiros + meio amostrado por igual, nunca
    cortando uma linha. Onde algo foi tirado entra uma linha ``marker``."""
    total = sum(len(x) + 1 for x in lines)
    if total <= budget:
        return list(lines)
    third = budget // 3
    head: list[int] = []
    used = 0
    for i, line in enumerate(lines):
        if used + len(line) + 1 > third:
            break
        head.append(i)
        used += len(line) + 1
    tail: list[int] = []
    used = 0
    for i in range(len(lines) - 1, head[-1] if head else -1, -1):
        if used + len(lines[i]) + 1 > third:
            break
        tail.append(i)
        used += len(lines[i]) + 1
    tail.reverse()
    lo = (head[-1] + 1) if head else 0
    hi = tail[0] if tail else len(lines)
    middle = list(range(lo, hi))
    mid_budget = budget - sum(len(lines[i]) + 1 for i in head + tail) - 3 * (len(marker) + 1)
    chosen: list[int] = []
    if middle and mid_budget > 0:
        avg = max(1.0, sum(len(lines[i]) + 1 for i in middle) / len(middle))
        k = max(1, min(len(middle), int(mid_budget // avg)))
        step = len(middle) / k
        used = 0
        for j in range(k):
            i = middle[int(j * step)]
            if used + len(lines[i]) + 1 > mid_budget:
                continue
            chosen.append(i)
            used += len(lines[i]) + 1
    keep = sorted(set(head + chosen + tail))
    out: list[str] = []
    prev = -1
    for i in keep:
        if i != prev + 1:
            out.append(marker)
        out.append(lines[i])
        prev = i
    if prev != len(lines) - 1:
        out.append(marker)
    return out


def _safe_free_text(text: str) -> str:
    """Anotações/modelos não podem parecer linha de transcrição (o parser do resumo as confundiria)."""
    return "\n".join(("- " + ln) if _TRANSCRIPT_LINE.match(ln.strip()) else ln for ln in text.splitlines())


def build_prompt(turns: list[Turn], language: str, my_notes: str | None = None,
                 template: str | None = None) -> str:
    """Prompt completo: instrução no idioma, ``LANGUAGE: <lang>``, modelo (texto já resolvido), anotações do
    usuário e a transcrição ``[mm:ss] Falante: texto`` (com a política de transcrição longa)."""
    lang = _lang(language)
    h = _HEAD[lang]
    parts = [INSTRUCTIONS[lang], "", f"LANGUAGE: {lang}"]
    if template and template.strip():
        parts += ["", f"## {h['template']}", _safe_free_text(template.strip())]
    if my_notes and my_notes.strip():
        parts += ["", f"## {h['notes']}", _safe_free_text(my_notes.strip())]
    parts += ["", f"## {h['transcript']}"]
    parts += fit_lines(transcript_lines(turns), MAX_TRANSCRIPT_CHARS, h["omitted"])
    return "\n".join(parts) + "\n"


# ---- modelos por tipo de reunião -------------------------------------------------------------------------

def user_templates_dir(config: Config | None) -> Path:
    base = config.source.parent if (config is not None and config.source) else default_config_path().parent
    return base / "templates"


def list_templates(config: Config | None = None) -> list[str]:
    names = {p.stem for p in PACKAGE_TEMPLATES.glob("*.md")}
    udir = user_templates_dir(config)
    if udir.is_dir():
        names |= {p.stem for p in udir.glob("*.md")}
    return sorted(names)


def _section(text: str, lang: str) -> str:
    sections = re.split(r"^##\s+(pt-BR|en|es)\s*$", text, flags=re.M)
    if len(sections) < 3:
        return text.strip()
    found = {sections[i]: sections[i + 1].strip() for i in range(1, len(sections) - 1, 2)}
    return found.get(lang) or found.get("pt-BR") or next(iter(found.values()))


def load_template(config: Config | None, name: str | None, language: str) -> str | None:
    """Texto do modelo ``name`` na seção do idioma. Usuário vence o pacote; inexistente -> None."""
    if not name:
        return None
    safe = re.sub(r"[^\w-]", "", name)
    for base in (user_templates_dir(config), PACKAGE_TEMPLATES):
        p = base / f"{safe}.md"
        if p.is_file():
            return _section(p.read_text(encoding="utf-8"), _lang(language))
    return None


# ---- resumo ----------------------------------------------------------------------------------------------

def summarize_turns(turns: list[Turn], language: str, config: Config, *, my_notes: str | None = None,
                    template: str | None = None) -> dict[str, Any] | None:
    """Resumo validado em SUMMARY_SCHEMA, ou None (provider none, erro do provedor, saída inválida 2×).

    ``template`` é o NOME do modelo (padrão: ``summary.template`` da config ou ``geral``)."""
    from ..engines import registry

    lang = _lang(language)
    try:
        summarizer = registry.summarizer_for(config)
    except Exception as exc:  # motor ausente/quebrado: o pipeline segue sem resumo
        log.warning("resumo falhou: %s", type(exc).__name__)
        return None
    if summarizer is None:
        return None
    tpl = load_template(config, template or config.get("summary.template") or DEFAULT_TEMPLATE, lang)
    prompt = build_prompt(turns, lang, my_notes=my_notes, template=tpl)
    for attempt in range(2):
        try:
            out = summarizer.summarize(prompt, SUMMARY_SCHEMA)
        except Exception as exc:
            log.warning("resumo falhou: %s", type(exc).__name__)
            return None
        errors = validate(out)
        if not errors:
            return out
        log.warning("resumo inválido (%d erros, tentativa %d)", len(errors), attempt + 1)
        prompt = prompt + "\n" + _HEAD[lang]["repair"].format(errors="; ".join(errors[:8])) + "\n"
    return None
