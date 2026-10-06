"""`ata demo`: grava uma reunião inventada (roteiros próprios em pt-BR/en/es) e processa de ponta a ponta.

- ``--no-tts``: monta o bundle com ``ata.testing.synth_meeting`` (tons + fixtures dos motores fake).
- padrão: Piper (`piper` no PATH, vozes ``<cache>/piper-voices/*.onnx``) sintetiza cada fala; "Eu" vai no mic,
  as outras pessoas no far, com pausas, uma fala sobreposta e vazamento de −18 dB do far no mic.
Grava ``demo-reference.json`` (gabarito: fatos, decisão, ações, pergunta) no bundle. Última linha impressa:
``note: <caminho>``.
"""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from . import __version__, audio, bundle, i18n
from .config import Config
from .demo_scripts import SCRIPTS, reference
from .engines.base import EngineMissing
from .engines.fake import write_fixture
from .testing import Line, synth_meeting
from .types import Span, Word

VOICE_PREFIXES = {"pt-BR": ("pt_BR",), "en": ("en_US", "en_GB"), "es": ("es_ES", "es_MX")}
PREFERRED = {"pt-BR": ("faber", "cadu", "edresson", "jeff"), "en": ("lessac", "ryan", "amy", "alan", "joe"),
             "es": ("davefx", "sharvard", "carlfm", "ald", "claude")}
BLEED_DB = -18.0
GAP_S = 0.45
OVERLAP_S = 0.6
TARGET_DB = -20.0


def voices_dir(config: Config) -> Path:
    return config.cache / "piper-voices"


def find_voices(config: Config, language: str, wanted: list[str] | None = None) -> list[Path]:
    """Três vozes para Eu, A, B (repete se houver menos). ``wanted`` casa por trecho do nome do arquivo."""
    d = voices_dir(config)
    pool = sorted(p for p in d.glob("*.onnx") if p.name.startswith(VOICE_PREFIXES[language])) if d.is_dir() else []
    if wanted:
        chosen = []
        for w in wanted:
            m = [p for p in (sorted(d.glob("*.onnx")) if d.is_dir() else []) if w.lower() in p.stem.lower()]
            if not m:
                raise EngineMissing(f"voz Piper não encontrada: {w!r} em {d}")
            chosen.append(m[0])
        pool = chosen
    else:
        pref = PREFERRED.get(language, ())
        pool.sort(key=lambda p: next((i for i, k in enumerate(pref) if k in p.stem.lower()), len(pref)))
    if not pool:
        raise EngineMissing(f"nenhuma voz Piper para {language} em {d} (baixe um .onnx {VOICE_PREFIXES[language][0]}-*)"
                            " ou use --no-tts")
    return [pool[i % len(pool)] for i in range(3)]


def _piper(binary: str, voice: Path, text: str, out: Path) -> np.ndarray:
    r = subprocess.run([binary, "--model", str(voice), "--output_file", str(out)], input=text, text=True,
                       capture_output=True, timeout=120)
    if r.returncode != 0 or not out.is_file():
        raise RuntimeError(f"piper falhou (código {r.returncode}) com a voz {voice.name}")
    x = audio.to_float(audio.read_any_wav(out))
    rms = float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0
    if rms > 0:
        x = x * (10 ** (TARGET_DB / 20.0) / rms)
    return np.clip(x, -0.99, 0.99).astype(np.float32)


def tts_meeting(config: Config, lines: list[Line], language: str, title: str, voices: list[Path],
                piper: str, started: datetime | None = None) -> Path:
    """Bundle ata/1 com fala sintetizada pelo Piper (far = A/B, mic = Eu + vazamento)."""
    started = started or datetime.now().astimezone()
    by_who = {"Eu": voices[0], "A": voices[1], "B": voices[2]}
    clips: list[tuple[str, float, np.ndarray, str]] = []
    t = 1.0
    prev_end = t
    with tempfile.TemporaryDirectory(prefix="ata-demo-") as tmp:
        for i, ln in enumerate(lines):
            seg = _piper(piper, by_who.get(ln.who, voices[1]), ln.text, Path(tmp) / f"{i:03d}.wav")
            start = max(1.0, prev_end - OVERLAP_S) if (ln.overlap and clips) else t
            clips.append((ln.who, start, seg, ln.text))
            prev_end = start + len(seg) / audio.SAMPLE_RATE
            t = max(t, prev_end) + GAP_S
    total = t + 1.0
    n = int(total * audio.SAMPLE_RATE)
    far, mic = np.zeros(n, np.float32), np.zeros(n, np.float32)
    far_words: list[Word] = []
    mic_words: list[Word] = []
    far_spans: list[Span] = []
    for who, start, seg, text in clips:
        a = int(start * audio.SAMPLE_RATE)
        target = mic if who == "Eu" else far
        target[a:a + len(seg)] += seg[: max(0, n - a)]
        dur = len(seg) / audio.SAMPLE_RATE
        toks = text.split()
        step = dur / max(1, len(toks))
        ws = [Word(tok, start + k * step, start + (k + 1) * step - 0.02, 0.9) for k, tok in enumerate(toks)]
        if who == "Eu":
            mic_words.extend(ws)
        else:
            far_words.extend(ws)
            far_spans.append(Span(start, start + dur, f"S{ord(who[0]) - ord('A')}"))
    mic += far * (10 ** (BLEED_DB / 20.0))
    bdir = bundle.new_bundle_dir(config.recordings, started, title)
    audio.write_wav(bdir / "far.wav", np.clip(far, -1, 1))
    audio.write_wav(bdir / "mic.wav", np.clip(mic, -1, 1))
    # fixtures só são lidas pelos motores fake (ATA_ENGINES=fake); motores reais ignoram
    write_fixture(bdir / "far.wav", far_words, far_spans)
    write_fixture(bdir / "mic.wav", sorted(mic_words + far_words, key=lambda w: w.start),
                  [Span(w.start, w.end, "S0") for w in mic_words])
    epoch = started.timestamp()
    meta = bundle.BundleMeta(
        name=bdir.name, created_at=started.isoformat(timespec="seconds"),
        stopped_at=(started + timedelta(seconds=total)).isoformat(timespec="seconds"),
        slug=bundle.slugify(title), title=title, host=socket.gethostname(), platform=sys.platform,
        recorder={"name": "ata-demo", "version": __version__, "tts": "piper",
                  "voices": ",".join(v.stem for v in voices)},
        language_requested=language, speakers_hint=3,
        tracks={k: bundle.Track(file=f"{k}.wav", device="piper-tts", start_epoch=epoch, start_measured=True,
                                samples=n, silent=False) for k in ("far", "mic")})
    bundle.write_meta(bdir, meta)
    return bdir


def run_demo(config: Config, language: str = "pt-BR", *, tts: bool = True, process: bool = True,
             voices: list[str] | None = None, out: Any = None) -> tuple[Path, Path | None]:
    out = out or sys.stdout
    lang = i18n.normalize(language)
    if lang not in SCRIPTS:
        raise ValueError(f"demo sem roteiro para {language!r} (use pt-BR, en ou es)")
    sc = SCRIPTS[lang]
    if tts:
        piper = shutil.which("piper")
        if not piper:
            raise EngineMissing("`piper` não está no PATH (instale piper-tts) ou use --no-tts")
        vs = find_voices(config, lang, voices)
        print(f"vozes: {', '.join(v.stem for v in vs)}", file=out)
        bdir = tts_meeting(config, sc["lines"], lang, sc["title"], vs, piper)
    else:
        bdir = synth_meeting(config.recordings, sc["lines"], title=sc["title"], language=lang)
    bundle.write_json(bdir / "demo-reference.json", reference(lang))
    print(f"bundle: {bdir}", file=out)
    if not process:
        return bdir, None
    from .pipeline.run import process_bundle  # parte B
    note = process_bundle(bdir, config, language=lang, speakers=3)
    return bdir, Path(note) if note else None


def _cmd(args: argparse.Namespace, config: Config) -> int:
    voices = [v.strip() for v in args.voices.split(",") if v.strip()] if args.voices else None
    try:
        bdir, note = run_demo(config, args.lang, tts=not args.no_tts, process=not args.no_process, voices=voices)
    except ValueError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        if isinstance(exc, EngineMissing):
            raise
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    if note is None:
        print(f"note: (não processado; rode `ata process {bdir.name}`)" if args.no_process else "note: (sem nota)")
        return 0
    print(f"note: {note}")
    return 0


def add_parser(sub: Any) -> None:
    p = sub.add_parser("demo", help="reunião de demonstração (roteiro inventado) de ponta a ponta",
                       description="Gera uma reunião inventada em duas faixas e processa até a nota.")
    p.add_argument("--lang", default="pt-BR", choices=list(SCRIPTS), help="idioma do roteiro")
    p.add_argument("--no-tts", action="store_true", help="sem Piper: tons sintéticos + motores fake")
    p.add_argument("--no-process", action="store_true", help="só grava o bundle")
    p.add_argument("--voices", help="vozes Piper para Eu,A,B (trecho do nome do .onnx)")
    p.set_defaults(func=_cmd)
