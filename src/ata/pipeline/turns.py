"""Turnos: palavras (relógio do bundle) + spans da diarização -> falas por pessoa.

- mic = você por construção (``i18n.me_label``); com diarização do mic (``mic_spans``) os rótulos vêm de
  ``labels["mic:<rótulo>"]``.
- far: cada palavra vai para o span com maior sobreposição; sem sobreposição, o span mais próximo (até
  ``NEAREST_MAX_S``); sem spans, a primeira pessoa do outro lado.
- palavras consecutivas da mesma pessoa e faixa viram um turno; pausa > ``MAX_GAP_S`` abre turno novo.
- trocas minúsculas (< ``FLIP_MAX_S``) ensanduichadas pela mesma pessoa voltam para ela (erro do diarizador).
"""

from __future__ import annotations

from typing import Any

from .. import i18n
from ..types import Span, Turn, Word

MAX_GAP_S = 1.5
FLIP_MAX_S = 0.6
NEAREST_MAX_S = 2.0
MIC_PREFIX = "mic:"


def assign_speaker(word: Word, spans: list[Span]) -> str | None:
    """Rótulo do diarizador para uma palavra (maior sobreposição, senão o span mais próximo)."""
    if not spans:
        return None
    best, best_ov = None, 0.0
    for s in spans:
        ov = min(word.end, s.end) - max(word.start, s.start)
        if ov > best_ov:
            best, best_ov = s.speaker, ov
    if best is not None:
        return best
    mid = (word.start + word.end) / 2.0
    near, dist = None, NEAREST_MAX_S
    for s in spans:
        d = s.start - mid if mid < s.start else (mid - s.end if mid > s.end else 0.0)
        if d <= dist:
            near, dist = s.speaker, d
    return near


def first_appearance_labels(spans: list[Span], language: str, first_n: int = 2,
                            prefix: str = "") -> dict[str, str]:
    """Rótulos do diarizador -> 'Pessoa N' por ordem da primeira aparição (N começa em ``first_n``)."""
    out: dict[str, str] = {}
    for s in sorted(spans, key=lambda s: (s.start, s.speaker)):
        key = prefix + s.speaker
        if key not in out:
            out[key] = i18n.other_label(language, first_n + len(out))
    return out


def _runs(tagged: list[tuple[Word, str]], track: str) -> list[Turn]:
    turns: list[Turn] = []
    for w, who in tagged:
        last = turns[-1] if turns else None
        if last is not None and last.speaker == who and w.start - last.end <= MAX_GAP_S:
            last.words.append(w)
            last.end = max(last.end, w.end)
        else:
            turns.append(Turn(w.start, w.end, who, "", track, [w]))  # type: ignore[arg-type]
    return turns


def smooth_turns(turns: list[Turn]) -> list[Turn]:
    """Dobra turnos curtos (< FLIP_MAX_S) entre dois turnos da mesma pessoa; repete até estabilizar.

    Só olha uma faixa por vez (chame por faixa). Devolve turnos novos; o texto fica vazio (montado depois).
    """
    cur = [Turn(t.start, t.end, t.speaker, t.text, t.track, list(t.words)) for t in turns]
    changed = True
    while changed and len(cur) >= 3:
        changed = False
        for i in range(1, len(cur) - 1):
            a, b, c = cur[i - 1], cur[i], cur[i + 1]
            if a.speaker == c.speaker != b.speaker and (b.end - b.start) < FLIP_MAX_S:
                merged = Turn(a.start, max(a.end, b.end, c.end), a.speaker, "", a.track, a.words + b.words + c.words)
                cur[i - 1:i + 2] = [merged]
                changed = True
                break
    return cur


def _finish(turns: list[Turn]) -> list[Turn]:
    for t in turns:
        t.words.sort(key=lambda w: (w.start, w.end))
        t.text = " ".join(w.text for w in t.words).strip()
        t.start = min(w.start for w in t.words)
        t.end = max(w.end for w in t.words)
    return turns


def build_turns(far_words: list[Word], far_spans: list[Span], mic_words: list[Word], labels: dict[str, str],
                language: str, *, mic_spans: list[Span] | None = None) -> list[Turn]:
    """Turnos ordenados por início. ``labels``: rótulo do diarizador do far (``"S0"``) -> rótulo final;
    para o mic diarizado, chaves ``"mic:S0"``. Rótulo ausente no mapa -> 'Pessoa 2' (far) / 'Eu' (mic)."""
    me = i18n.me_label(language)
    default_far = i18n.other_label(language, 2)
    spans = sorted(far_spans, key=lambda s: s.start)
    far_tagged: list[tuple[Word, str]] = []
    fallback = next((v for k, v in labels.items() if not k.startswith(MIC_PREFIX)), default_far)
    for w in sorted(far_words, key=lambda w: (w.start, w.end)):
        raw = assign_speaker(w, spans)
        if raw is not None:
            who = labels.get(raw, default_far)
        else:   # sem span por perto: continua com quem falava antes
            who = far_tagged[-1][1] if far_tagged else fallback
        far_tagged.append((w, who))
    mic_tagged: list[tuple[Word, str]] = []
    mspans = sorted(mic_spans or [], key=lambda s: s.start)
    for w in sorted(mic_words, key=lambda w: (w.start, w.end)):
        who = me
        if mspans:
            raw = assign_speaker(w, mspans)
            if raw is not None:
                who = labels.get(MIC_PREFIX + raw, me)
        mic_tagged.append((w, who))
    out: list[Turn] = []
    for tagged, track in ((far_tagged, "far"), (mic_tagged, "mic")):
        if not tagged:
            continue
        runs = _runs(tagged, track)
        smoothed = smooth_turns(runs)
        # depois de dobrar, dois turnos vizinhos da mesma pessoa podem ter ficado colados
        regrouped = _runs([(w, t.speaker) for t in smoothed for w in sorted(t.words, key=lambda w: w.start)],
                          track)
        out.extend(_finish(regrouped))
    out.sort(key=lambda t: (t.start, 0 if t.track == "far" else 1))
    return out


def speaker_stats(turns: list[Turn]) -> dict[str, dict[str, Any]]:
    """Tempo de fala, turnos e palavras por falante (para turns.json e a nota)."""
    stats: dict[str, dict[str, Any]] = {}
    for t in turns:
        s = stats.setdefault(t.speaker, {"track": t.track, "seconds": 0.0, "turns": 0, "words": 0,
                                         "first": t.start})
        s["seconds"] = round(s["seconds"] + max(0.0, t.end - t.start), 3)
        s["turns"] += 1
        s["words"] += len(t.words) if t.words else len(t.text.split())
        s["first"] = min(s["first"], t.start)
    return stats

