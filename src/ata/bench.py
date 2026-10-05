"""Comando ``ata bench asr|diar|summary --set <pasta> --engines a,b [--lang] [--out]``.

* asr: cada ``X.wav`` com ``X.txt`` (referência). WER de corpus = soma das edições / soma das palavras de
  referência (Levenshtein em tokens após normalização por idioma); RTF = tempo / duração do áudio.
* diar: cada ``X.wav`` com ``X.rttm``. DER simples por quadros de 10 ms: (falta + sobra + confusão) / fala de
  referência, com o melhor mapeamento de rótulos (permutação exata até 7 falantes, guloso acima).
* summary: cada ``X.txt`` é um prompt (linhas ``[mm:ss] Falante: texto``); mede a fração de respostas que
  passam no schema e o tempo médio.

Imprime uma tabela Markdown e grava JSON em ``bench/results/<data>-<host>.json`` (ou ``--out``). Sem texto
de reunião na saída: só métricas.
"""

from __future__ import annotations

import itertools
import json
import re
import socket
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from . import audio
from .config import Config
from .engines import registry
from .engines.base import EngineError
from .types import Span

FRAME = 0.01


# -------------------------------------------------------------------------------------------- WER

def normalize_text(text: str, language: str = "pt-BR") -> list[str]:
    """Minúsculas, sem pontuação, espaços colapsados. pt/es mantêm acentos; en tira diacríticos."""
    s = unicodedata.normalize("NFC", text).casefold()
    s = s.replace("’", "'")
    if language.lower().startswith("en"):
        s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
        s = re.sub(r"(\w)'(\w)", r"\1\2", s)          # don't -> dont
    s = re.sub(r"[^\w\s]|_", " ", s)
    return s.split()


def edit_distance(ref: list[str], hyp: list[str]) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


def wer(ref: str, hyp: str, language: str = "pt-BR") -> tuple[int, int]:
    """(edições, palavras de referência)."""
    r, h = normalize_text(ref, language), normalize_text(hyp, language)
    return edit_distance(r, h), len(r)


# -------------------------------------------------------------------------------------------- DER

def read_rttm(path: Path) -> list[Span]:
    spans = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[0] == "SPEAKER":
            start, dur = float(parts[3]), float(parts[4])
            spans.append(Span(start, start + dur, parts[7]))
    return spans


def _frames(spans: list[Span], n: int) -> list[set[str]]:
    out: list[set[str]] = [set() for _ in range(n)]
    for s in spans:
        for k in range(max(0, int(round(s.start / FRAME))), min(n, int(round(s.end / FRAME)))):
            out[k].add(s.speaker)
    return out


def best_mapping(overlap: dict[tuple[str, str], int], refs: list[str], hyps: list[str]) -> dict[str, str]:
    """hyp -> ref maximizando a sobreposição total (exato até 7 rótulos, guloso acima)."""
    if not refs or not hyps:
        return {}
    if max(len(refs), len(hyps)) <= 7:
        best, best_score = {}, -1
        small, big = (hyps, refs) if len(hyps) <= len(refs) else (refs, hyps)
        for perm in itertools.permutations(big, len(small)):
            pairs = list(zip(small, perm))
            m = {h: r for h, r in pairs} if small is hyps else {h: r for r, h in pairs}
            score = sum(overlap.get((h, r), 0) for h, r in m.items())
            if score > best_score:
                best, best_score = m, score
        return best
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for (h, r), _v in sorted(overlap.items(), key=lambda kv: -kv[1]):
        if h not in mapping and r not in used:
            mapping[h] = r
            used.add(r)
    return mapping


def der(ref: list[Span], hyp: list[Span]) -> tuple[float, float]:
    """(erro em segundos, fala de referência em segundos)."""
    end = max([s.end for s in ref + hyp] or [0.0])
    n = int(round(end / FRAME)) + 1
    rf, hf = _frames(ref, n), _frames(hyp, n)
    overlap: dict[tuple[str, str], int] = {}
    for a, b in zip(rf, hf):
        for h in b:
            for r in a:
                overlap[(h, r)] = overlap.get((h, r), 0) + 1
    mapping = best_mapping(overlap, sorted({s.speaker for s in ref}), sorted({s.speaker for s in hyp}))
    err = total = 0
    for a, b in zip(rf, hf):
        mapped = {mapping.get(h, f"__{h}") for h in b}
        correct = len(a & mapped)
        err += max(len(a), len(b)) - correct
        total += len(a)
    return err * FRAME, total * FRAME


# ------------------------------------------------------------------------------------------- runners

def _pairs(dataset: Path, ext: str) -> list[tuple[Path, Path]]:
    out = []
    for wav in sorted(dataset.glob("*.wav")):
        ref = wav.with_suffix(ext)
        if ref.is_file():
            out.append((wav, ref))
    return out


def bench_asr(config: Config, dataset: Path, refs: list[str], language: str) -> list[dict[str, Any]]:
    pairs = _pairs(dataset, ".txt")
    if not pairs:
        raise EngineError(f"nenhum par .wav + .txt em {dataset}")
    rows = []
    for ref in refs:
        try:
            eng = registry.build("asr", ref, config)
        except EngineError as exc:
            rows.append({"engine": ref, "error": str(exc), "n": 0})
            continue
        edits = words = 0
        elapsed = dur = 0.0
        failed = 0
        for wav, txt in pairs:
            dur += audio.duration(wav)
            t0 = time.perf_counter()
            try:
                hyp = " ".join(w.text for w in eng.transcribe(wav, language))
            except EngineError:
                failed += 1
                hyp = ""
            elapsed += time.perf_counter() - t0
            e, n = wer(txt.read_text(encoding="utf-8"), hyp, language)
            edits, words = edits + e, words + n
        rows.append({"engine": ref, "wer": edits / words if words else 0.0, "edits": edits, "words": words,
                     "rtf": elapsed / dur if dur else 0.0, "audio_s": round(dur, 3), "n": len(pairs),
                     "failed": failed})
    return rows


def bench_diar(config: Config, dataset: Path, refs: list[str], language: str) -> list[dict[str, Any]]:
    pairs = _pairs(dataset, ".rttm")
    if not pairs:
        raise EngineError(f"nenhum par .wav + .rttm em {dataset}")
    rows = []
    for ref in refs:
        try:
            eng = registry.build("diarizer", ref, config)
        except EngineError as exc:
            rows.append({"engine": ref, "error": str(exc), "n": 0})
            continue
        err = total = elapsed = dur = 0.0
        failed = 0
        for wav, rttm in pairs:
            dur += audio.duration(wav)
            t0 = time.perf_counter()
            try:
                hyp = eng.diarize(wav)
            except EngineError:
                failed += 1
                hyp = []
            elapsed += time.perf_counter() - t0
            e, t = der(read_rttm(rttm), hyp)
            err, total = err + e, total + t
        rows.append({"engine": ref, "der": err / total if total else 0.0, "rtf": elapsed / dur if dur else 0.0,
                     "audio_s": round(dur, 3), "n": len(pairs), "failed": failed})
    return rows


def _summary_schema() -> dict[str, Any]:
    try:
        from .knowledge.summary import SUMMARY_SCHEMA  # parte D
        return SUMMARY_SCHEMA
    except ImportError:
        return {"type": "object", "required": ["language", "tldr", "topics", "decisions", "actions", "questions"]}


def bench_summary(config: Config, dataset: Path, refs: list[str], language: str) -> list[dict[str, Any]]:
    from .engines.ollama import check_object
    prompts = sorted(dataset.glob("*.txt"))
    if not prompts:
        raise EngineError(f"nenhum .txt em {dataset}")
    schema = _summary_schema()
    rows = []
    for ref in refs:
        try:
            eng = registry.build("summarizer", ref, config)
        except EngineError as exc:
            rows.append({"engine": ref, "error": str(exc), "n": 0})
            continue
        valid = 0
        elapsed = 0.0
        for p in prompts:
            text = p.read_text(encoding="utf-8")
            if "LANGUAGE:" not in text:
                text = f"LANGUAGE: {language}\n{text}"
            t0 = time.perf_counter()
            try:
                valid += not check_object(eng.summarize(text, schema), schema)
            except EngineError:
                pass
            elapsed += time.perf_counter() - t0
        rows.append({"engine": ref, "valid": valid / len(prompts), "s_per_item": elapsed / len(prompts),
                     "n": len(prompts)})
    return rows


RUNNERS = {"asr": bench_asr, "diar": bench_diar, "summary": bench_summary}


def table(kind: str, rows: list[dict[str, Any]]) -> str:
    head = {"asr": ("WER", "wer", "RTF", "rtf"), "diar": ("DER", "der", "RTF", "rtf"),
            "summary": ("válidos", "valid", "s/item", "s_per_item")}[kind]
    lines = [f"| engine | {head[0]} | {head[2]} | n |", "|---|---|---|---|"]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['engine']} | erro | — | 0 |")
        else:
            lines.append(f"| {r['engine']} | {r[head[1]] * 100:.2f}% | {r[head[3]]:.3f} | {r['n']} |"
                         if kind != "summary" else
                         f"| {r['engine']} | {r[head[1]] * 100:.0f}% | {r[head[3]]:.2f} | {r['n']} |")
    return "\n".join(lines)


def cmd_bench(args: Any, config: Config) -> int:
    dataset = Path(args.set).expanduser()
    if not dataset.is_dir():
        print(f"erro: --set {dataset} não é uma pasta", file=sys.stderr)
        return 2
    refs = [e.strip() for e in args.engines.split(",") if e.strip()]
    if not refs:
        print("erro: --engines vazio", file=sys.stderr)
        return 2
    language = args.lang or str(config.get("language.default") or "pt-BR")
    rows = RUNNERS[args.kind](config, dataset, refs, language)
    print(table(args.kind, rows))
    host = socket.gethostname()
    now = datetime.now().astimezone()
    out_dir = Path(args.out).expanduser() if args.out else Path("bench") / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{now:%Y-%m-%d}-{host}.json"
    try:
        doc = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {"runs": []}
    except ValueError:
        doc = {"runs": []}
    doc.setdefault("runs", []).append({"at": now.isoformat(timespec="seconds"), "host": host, "kind": args.kind,
                                       "set": str(dataset), "language": language,
                                       "forced_fake": registry.forced_fake(), "results": rows})
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"resultados: {out}")
    return 1 if all("error" in r for r in rows) else 0


def add_parser(sub: Any) -> None:
    p = sub.add_parser("bench", help="mede WER/DER/RTF de motores num conjunto local")
    p.add_argument("kind", choices=tuple(RUNNERS))
    p.add_argument("--set", required=True, help="pasta com .wav + .txt (asr/summary) ou .rttm (diar)")
    p.add_argument("--engines", required=True, help="refs motor:modelo separadas por vírgula")
    p.add_argument("--lang", help="pt-BR, en ou es (normalização do WER)")
    p.add_argument("--out", help="pasta dos resultados (padrão bench/results)")
    p.set_defaults(func=cmd_bench)
