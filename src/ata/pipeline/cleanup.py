"""Limpeza do texto de um turno: fillers por idioma, glossário ``errado => certo`` e repetições.

Fillers que também são palavras de verdade (pt-BR "é", es "este"/"pues") só saem quando têm cara de
hesitação: seguidos de vírgula/reticências, repetidos ("é é") ou sozinhos no turno. "isso é bom" fica intacto.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from .. import i18n

# palavras que são filler só quando hesitação (ver docstring)
AMBIGUOUS: dict[str, frozenset[str]] = {
    "pt-BR": frozenset({"é", "tipo"}),
    "en": frozenset({"like"}),
    "es": frozenset({"este", "pues", "o sea"}),
}

Glossary = list[tuple[str, str]]


def load_glossary(path: Path | str | None) -> Glossary:
    """Lê linhas ``errado => certo`` (``#`` comenta). Arquivo ausente -> []. Linha sem ``=>`` é ignorada."""
    if not path:
        return []
    p = Path(path).expanduser()
    if not p.is_file():
        return []
    out: Glossary = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=>" not in line:
            continue
        wrong, _, right = line.partition("=>")
        wrong, right = wrong.strip(), right.strip()
        if wrong:
            out.append((wrong, right))
    return out


def fillers_for(language: str, extra: Iterable[str] = ()) -> tuple[str, ...]:
    lang = i18n.normalize(language) if language != "auto" else i18n.DEFAULT_LANGUAGE
    items = {f.strip().lower() for f in (*i18n.FILLERS.get(lang, ()), *extra) if f and f.strip()}
    return tuple(sorted(items, key=lambda f: (-len(f), f)))   # multi-palavra primeiro


def _word_re(phrase: str) -> str:
    return r"\s+".join(re.escape(p) for p in phrase.split())


_B = r"(?<![\w])"   # fronteira de palavra que funciona com acentos (\b falha em "é" isolado às vezes)
_E = r"(?![\w])"


def _strip_fillers(text: str, fillers: tuple[str, ...], ambiguous: frozenset[str]) -> str:
    for f in fillers:
        body = _word_re(f)
        if f in ambiguous:
            # repetido ("é é"), seguido de vírgula/reticências, ou o turno inteiro
            text = re.sub(rf"{_B}{body}(?:\s+{body})+{_E}[,.…]*", " ", text, flags=re.IGNORECASE)
            text = re.sub(rf"{_B}{body}{_E}\s*(?:,|\.\.\.|…)", " ", text, flags=re.IGNORECASE)
            if re.fullmatch(rf"\s*{body}\s*[.?!,…]*\s*", text, flags=re.IGNORECASE):
                text = ""
        else:
            text = re.sub(rf"{_B}{body}{_E}(?:\s*[,…]|\.\.\.)?", " ", text, flags=re.IGNORECASE)
    return text


def _apply_glossary(text: str, glossary: Glossary) -> str:
    for wrong, right in sorted(glossary, key=lambda g: -len(g[0])):
        text = re.sub(rf"{_B}{_word_re(wrong)}{_E}", lambda _m, r=right: r, text, flags=re.IGNORECASE)
    return text


def _norm_tok(tok: str) -> str:
    return re.sub(r"[^\w]", "", tok.lower())


def collapse_repeats(text: str, max_n: int = 2) -> str:
    """'a a a reunião' -> 'a reunião'; 'eu acho eu acho que' -> 'eu acho que' (n-gramas de 1..max_n)."""
    toks = text.split()
    changed = True
    while changed:
        changed = False
        for n in range(max_n, 0, -1):
            i = 0
            while i + 2 * n <= len(toks):
                a = [_norm_tok(t) for t in toks[i:i + n]]
                b = [_norm_tok(t) for t in toks[i + n:i + 2 * n]]
                if all(a) and a == b:
                    del toks[i + n:i + 2 * n]
                    changed = True
                else:
                    i += 1
    return " ".join(toks)


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?…])", r"\1", text)
    text = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", text)
    text = re.sub(r"^[,;:.…\s]+", "", text)
    return re.sub(r"[,;:]\s*$", "", text).strip()


def clean_text(text: str, language: str, glossary: Glossary | None = None, *,
               extra_fillers: Iterable[str] = ()) -> str:
    """Texto limpo do turno (pode virar '' se era só hesitação)."""
    lang = i18n.normalize(language) if language != "auto" else i18n.DEFAULT_LANGUAGE
    out = _strip_fillers(text, fillers_for(lang, extra_fillers), AMBIGUOUS.get(lang, frozenset()))
    out = collapse_repeats(re.sub(r"\s+", " ", out).strip())
    if glossary:
        out = _apply_glossary(out, glossary)
    return _tidy(out)
