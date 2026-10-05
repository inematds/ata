"""Textos e regras por idioma (pt-BR, en, es). Tudo que muda com o idioma passa por aqui."""

from __future__ import annotations

LANGUAGES = ("pt-BR", "en", "es")
DEFAULT_LANGUAGE = "pt-BR"

_ALIASES = {"pt": "pt-BR", "pt-br": "pt-BR", "ptbr": "pt-BR", "pt_br": "pt-BR", "en-us": "en", "en-gb": "en",
            "en_us": "en", "es-es": "es", "es-mx": "es", "es-419": "es", "es_es": "es"}


def normalize(lang: str | None) -> str:
    """'pt', 'PT-BR', 'pt_BR' -> 'pt-BR'; 'auto' passa; desconhecido -> ValueError."""
    if lang is None or lang == "":
        return DEFAULT_LANGUAGE
    if lang == "auto":
        return "auto"
    key = lang.strip().lower()
    if key in _ALIASES:
        return _ALIASES[key]
    for code in LANGUAGES:
        if key == code.lower():
            return code
    raise ValueError(f"idioma não suportado: {lang!r} (use pt-BR, en, es ou auto)")


TEXTS: dict[str, dict[str, str]] = {
    "pt-BR": {
        "me": "Eu", "other": "Pessoa", "meeting": "Reunião", "summary": "Resumo", "decisions": "Decisões",
        "actions": "Ações", "questions": "Perguntas em aberto", "topics": "Tópicos", "participants": "Participantes",
        "transcript": "Transcrição", "problems": "Problemas da gravação", "my_notes": "Minhas anotações",
        "talk_time": "tempo de fala", "owner": "responsável", "due": "prazo", "no_summary": "(sem resumo)",
        "damaged": "Esta gravação tem problemas; a transcrição pode estar incompleta.",
        "prep": "Preparação", "open_actions": "Ações em aberto", "decided_before": "Decidido antes",
        "script": "Roteiro", "notes_used": "Notas usadas",
    },
    "en": {
        "me": "Me", "other": "Speaker", "meeting": "Meeting", "summary": "Summary", "decisions": "Decisions",
        "actions": "Action items", "questions": "Open questions", "topics": "Topics", "participants": "Participants",
        "transcript": "Transcript", "problems": "Recording problems", "my_notes": "My notes",
        "talk_time": "talk time", "owner": "owner", "due": "due", "no_summary": "(no summary)",
        "damaged": "This recording has problems; the transcript may be incomplete.",
        "prep": "Prep", "open_actions": "Open action items", "decided_before": "Decided before",
        "script": "Script", "notes_used": "Notes used",
    },
    "es": {
        "me": "Yo", "other": "Persona", "meeting": "Reunión", "summary": "Resumen", "decisions": "Decisiones",
        "actions": "Acciones", "questions": "Preguntas abiertas", "topics": "Temas", "participants": "Participantes",
        "transcript": "Transcripción", "problems": "Problemas de la grabación", "my_notes": "Mis notas",
        "talk_time": "tiempo de habla", "owner": "responsable", "due": "plazo", "no_summary": "(sin resumen)",
        "damaged": "Esta grabación tiene problemas; la transcripción puede estar incompleta.",
        "prep": "Preparación", "open_actions": "Acciones abiertas", "decided_before": "Decidido antes",
        "script": "Guion", "notes_used": "Notas usadas",
    },
}

FILLERS: dict[str, tuple[str, ...]] = {
    "pt-BR": ("é", "né", "hum", "hmm", "ahn", "ã", "éé", "tipo assim", "aham", "uhum"),
    "en": ("um", "umm", "uh", "uhh", "uhm", "erm", "hmm", "you know"),
    "es": ("eh", "este", "o sea", "mmm", "ehh", "pues"),
}


def t(lang: str, key: str) -> str:
    lang = normalize(lang) if lang != "auto" else DEFAULT_LANGUAGE
    return TEXTS[lang][key]


def me_label(lang: str) -> str:
    return t(lang, "me")


def other_label(lang: str, n: int) -> str:
    """Rótulo da n-ésima pessoa do outro lado (n começa em 2; 'Eu' é a 1)."""
    if n < 2:
        raise ValueError("n começa em 2 (a pessoa 1 é você)")
    return f"{t(lang, 'other')} {n}"
