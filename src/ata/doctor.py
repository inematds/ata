"""Comandos ``ata doctor [--json] [--skip-benchmark]`` e ``ata privacy [<alvo>]``.

O doctor só verifica (nunca instala nem baixa) e nunca mostra texto de reunião. Cada sonda externa passa por
``PROBES`` para os testes injetarem fakes. Saída: linhas ``[ok  ]``/``[warn]``/``[fail]``/``[skip]`` e uma
linha final dizendo se gravação, processamento e resumo funcionam. Código 0 quando não há ``fail``.
"""

from __future__ import annotations

import importlib.util
import json
import platform as _platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import audio, bundle
from .config import Config
from .engines import registry
from .engines.base import EngineError

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
TAG = {OK: "[ok  ]", WARN: "[warn]", FAIL: "[fail]", SKIP: "[skip]"}
MIN_DISK_FAIL = 500 * 1024 ** 2
MIN_DISK_WARN = 5 * 1024 ** 3


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return p.returncode, p.stdout


def _engine_status(config: Config) -> dict[str, Any]:
    from .engine_cmd import engine_status
    return engine_status(config)


def _ollama_models(config: Config) -> list[str]:
    from .engines.ollama import list_models
    return list_models(config)


def _disk_free(path: Path) -> int:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


@dataclass
class Probes:
    which: Callable[[str], str | None] = shutil.which
    run: Callable[..., tuple[int, str]] = _run
    platform: str = sys.platform
    machine: str = field(default_factory=_platform.machine)
    python: tuple[int, int, int] = tuple(sys.version_info[:3])  # type: ignore[assignment]
    disk_free: Callable[[Path], int] = _disk_free
    importable: Callable[[str], bool] = _importable
    engine_status: Callable[[Config], dict[str, Any]] = _engine_status
    ollama_models: Callable[[Config], list[str]] = _ollama_models


PROBES = Probes()


@dataclass
class Check:
    status: str
    name: str
    detail: str

    def line(self) -> str:
        return f"{TAG[self.status]} {self.name}: {self.detail}"


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".ata-doctor-"):
            pass
        return True
    except OSError:
        return False


def run_checks(config: Config, *, skip_benchmark: bool = False, probes: Probes | None = None) -> dict[str, Any]:
    pr = probes or PROBES
    checks: list[Check] = []
    add = lambda s, n, d: checks.append(Check(s, n, d))  # noqa: E731
    fake = registry.forced_fake()

    # python e plataforma
    py = ".".join(map(str, pr.python))
    add(OK if tuple(pr.python[:2]) >= (3, 12) else FAIL, "python", py + ("" if pr.python[:2] >= (3, 12)
                                                                       else " (precisa >= 3.12)"))
    plat = pr.platform
    add(OK, "plataforma", f"{plat} {pr.machine}")

    # captura
    recording_ok = False
    if plat.startswith("linux"):
        missing = [b for b in ("pw-record", "wpctl") if not pr.which(b)]
        if missing:
            add(FAIL, "captura", f"PipeWire sem {', '.join(missing)} (instale pipewire-bin / wireplumber)")
        else:
            recording_ok = True
            add(OK, "captura", "PipeWire (pw-record + wpctl)")
    elif plat.startswith("win"):
        recording_ok = pr.importable("pyaudiowpatch")
        add(OK if recording_ok else FAIL, "captura", "WASAPI loopback (PyAudioWPatch)" if recording_ok
            else "PyAudioWPatch ausente: uv tool install 'ata[windows]'")
    elif plat == "darwin":
        helper = pr.which("ata-capture-macos")
        recording_ok = bool(helper)
        add(OK if helper else WARN, "captura", "helper Swift (process tap)" if helper
            else "helper ata-capture-macos não encontrado")
    else:
        add(WARN, "captura", f"plataforma {plat} sem backend de captura")

    # config e pastas
    add(OK, "config", str(config.source) if config.source else "padrões (rode: ata setup)")
    for key in ("paths.recordings", "paths.notes", "paths.cache", "paths.models"):
        p = config.path(key)
        add(OK if _writable(p) else FAIL, key.split(".")[1], str(p) if _writable(p) else f"{p} sem escrita")
    try:
        free = pr.disk_free(config.recordings)
        gb = free / 1024 ** 3
        add(FAIL if free < MIN_DISK_FAIL else WARN if free < MIN_DISK_WARN else OK, "disco",
            f"{gb:.1f} GB livres em {config.recordings}")
    except OSError:
        add(WARN, "disco", "não consegui medir o espaço livre")

    # motores de transcrição
    st = pr.engine_status(config)
    sidecar_ok = bool(st.get("healthy"))
    if sidecar_ok:
        add(OK, "motor nemo", f"rodando em {st.get('host')}:{st.get('port')}")
    elif st.get("running"):
        add(WARN, "motor nemo", "processo vivo mas sem resposta de saúde (veja ata engine status)")
    else:
        add(WARN, "motor nemo", "parado (ata engine install && ata engine start)")
    onnx_pkgs = {n: pr.importable(n) for n in ("onnx_asr", "sherpa_onnx")}
    onnx_ok = all(onnx_pkgs.values())
    add(OK if onnx_ok else WARN, "motor onnx", "onnx-asr + sherpa-onnx" if onnx_ok else
        f"falta {', '.join(k for k, v in onnx_pkgs.items() if not v)}: uv tool install 'ata[onnx]'")
    if pr.which("nvidia-smi"):
        rc, out = pr.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"])
        add(OK if rc == 0 and out.strip() else WARN, "gpu", out.strip().splitlines()[0] if rc == 0 and out.strip()
            else "nvidia-smi falhou")
    else:
        add(SKIP, "gpu", "sem nvidia-smi (CPU)")
    processing_ok = fake or sidecar_ok or onnx_ok
    processing_by = "fake" if fake else "nemo" if sidecar_ok else "onnx" if onnx_ok else None
    if not processing_ok:
        add(FAIL, "processamento", "nenhum motor de ASR disponível (ligue o nemo ou instale o onnx)")

    # resumo
    provider = str(config.get("summary.provider"))
    summary_ok = provider == "none" or fake
    if provider == "ollama":
        model = str(config.get("summary.ollama_model"))
        try:
            models = pr.ollama_models(config)
        except EngineError:
            add(WARN, "ollama", "Ollama não está rodando (ollama serve)")
        else:
            present = model in models or f"{model}:latest" in models
            summary_ok = summary_ok or present
            add(OK if present else WARN, "ollama", f"modelo {model} presente" if present
                else f"modelo {model} ausente: ollama pull {model}")
    else:
        add(SKIP, "ollama", f"resumo = {provider}")
    for cli in ("claude", "codex"):
        found = pr.which(cli)
        if provider == cli:
            summary_ok = summary_ok or bool(found)
            add(OK if found else FAIL, cli, f"{found} (privacy.level = 1: texto sai da máquina)" if found
                else f"{cli} não encontrado no PATH, mas é o provedor do resumo")
        else:
            add(OK if found else SKIP, cli, found or "não instalado (opcional)")

    # utilitários
    add(OK if pr.which("ffmpeg") else WARN, "ffmpeg", "ok" if pr.which("ffmpeg")
        else "ausente: só WAV no ata import")
    add(OK if pr.which("piper") else SKIP, "piper", "ok" if pr.which("piper")
        else "ausente: ata demo usa --no-tts")

    # benchmark
    bench: dict[str, Any] | None = None
    if skip_benchmark:
        add(SKIP, "benchmark", "--skip-benchmark")
    elif fake:
        add(SKIP, "benchmark", "ATA_ENGINES=fake")
    elif not processing_ok:
        add(SKIP, "benchmark", "sem motor de ASR")
    else:
        bench = benchmark(config)
        if bench.get("error"):
            add(WARN, "benchmark", bench["error"])
        else:
            add(OK, "benchmark", f"{bench['engine']}: RTF {bench['rtf']:.3f} "
                                 f"({bench['seconds']:.1f} s para {bench['audio_s']:.0f} s de áudio)")

    failed = any(c.status == FAIL for c in checks)
    summary_line = (f"gravação: {'sim' if recording_ok else 'não'} · "
                    f"processamento: {'sim (' + processing_by + ')' if processing_ok else 'não'} · "
                    f"resumo: {'sim (' + provider + ')' if summary_ok and provider != 'none' else 'desligado' if provider == 'none' else 'não'}")
    return {"checks": [c.__dict__ for c in checks], "ok": not failed, "recording": recording_ok,
            "processing": processing_ok, "processing_engine": processing_by, "summary": summary_ok,
            "summary_provider": provider, "benchmark": bench, "final": summary_line}


def benchmark(config: Config, seconds: float = 20.0) -> dict[str, Any]:
    """RTF do ASR configurado num clipe sintético (tons modulados; o texto não importa)."""
    lang = str(config.get("language.default") or "pt-BR")
    ref = registry.asr_ref(config, lang if lang != "auto" else "pt-BR")
    with tempfile.TemporaryDirectory(prefix="ata-bench-") as tmp:
        wav = Path(tmp) / "clip.wav"
        t = np.arange(int(seconds * audio.SAMPLE_RATE)) / audio.SAMPLE_RATE
        x = 0.2 * np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t))
        audio.write_wav(wav, x.astype(np.float32))
        try:
            eng = registry.build("asr", ref, config)
            t0 = time.perf_counter()
            eng.transcribe(wav, lang)
            el = time.perf_counter() - t0
        except EngineError as exc:
            return {"engine": ref, "error": f"{ref}: {exc}"}
    return {"engine": ref, "seconds": el, "audio_s": seconds, "rtf": el / seconds}


def cmd_doctor(args: Any, config: Config) -> int:
    rep = run_checks(config, skip_benchmark=args.skip_benchmark)
    try:
        config.cache.mkdir(parents=True, exist_ok=True)
        (config.cache / "doctor.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
    except OSError:
        pass
    if args.json:
        print(json.dumps(rep, ensure_ascii=False))
    else:
        for c in rep["checks"]:
            print(Check(**c).line())
        print(rep["final"])
    return 0 if rep["ok"] else 1


# ------------------------------------------------------------------------------------------ privacy

LEVELS = {0: "0 — cofre: nada sai da máquina", 1: "1 — assinatura: texto da transcrição vai para um CLI"}
EXTERNAL = {"claude": "Anthropic via claude CLI (assinatura): texto da transcrição, nunca áudio",
            "codex": "OpenAI via codex CLI (assinatura): texto da transcrição, nunca áudio"}


def destinations_for(engines: dict[str, Any]) -> list[str]:
    out = []
    for role, ref in engines.items():
        name = str(ref).split(":")[0].strip().lower()
        if name in EXTERNAL:
            out.append(f"{role}: {EXTERNAL[name]}")
    return out


def config_privacy(config: Config) -> dict[str, Any]:
    provider = str(config.get("summary.provider"))
    lang = str(config.get("language.default") or "pt-BR")
    engines = {"asr": registry.asr_ref(config, lang if lang != "auto" else "pt-BR"),
               "diarization": str(config.get("diarization.engine")),
               "summary": provider if provider == "none" else f"{provider}:" + str(
                   config.get(f"summary.{provider}_model") or ""),
               "embeddings": f"{config.get('embeddings.provider')}:{config.get('embeddings.model')}",
               "live": str(config.get("live.engine"))}
    return {"level": int(config.get("privacy.level") or 0), "engines": engines,
            "destinations": destinations_for(engines)}


def cmd_privacy(args: Any, config: Config) -> int:
    if args.target:
        try:
            bdir = bundle.resolve(args.target, config.recordings)
            meta = bundle.read_meta(bdir)
        except bundle.BundleError as exc:
            print(f"erro: {exc}", file=sys.stderr)
            return 1
        dests = destinations_for(meta.engines)
        print(f"gravação: {meta.name}")
        if not meta.engines:
            print("motores: (ainda não processada)")
        for role, ref in sorted(meta.engines.items()):
            print(f"  {role}: {ref}")
        print("saiu da máquina: " + ("nada" if not dests else ""))
        for d in dests:
            print(f"  - {d}")
        return 0
    info = config_privacy(config)
    print(f"nível de privacidade: {LEVELS.get(info['level'], info['level'])}")
    for role, ref in info["engines"].items():
        print(f"  {role}: {ref}")
    if info["destinations"]:
        print("sai da máquina:")
        for d in info["destinations"]:
            print(f"  - {d}")
    else:
        print("sai da máquina: nada (ASR, diarização, resumo, embeddings e busca locais)")
    print("áudio nunca sai da máquina; logs e erros não contêm texto de reunião")
    return 0


def add_parser(sub: Any) -> None:
    p = sub.add_parser("doctor", help="verifica o que funciona nesta máquina")
    p.add_argument("--json", action="store_true")
    p.add_argument("--skip-benchmark", action="store_true", help="não mede o RTF do ASR")
    p.set_defaults(func=cmd_doctor)
    q = sub.add_parser("privacy", help="o que sai da máquina (config atual ou uma gravação)")
    q.add_argument("target", nargs="?", help="pasta/nome da gravação, 'latest' ou nota")
    q.set_defaults(func=cmd_privacy)

