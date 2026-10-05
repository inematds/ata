"""Comando ``ata engine``: instala, liga, desliga e mostra o sidecar NeMo-Speech.cpp (``nemo-speech serve``).

* ``engine install [--backend auto|cuda13|cuda12|cpu|metal|vulkan] [--dry-run] [--onnx] [--allow-unverified]``
  baixa o archive do release e os modelos do manifesto ``ata/data/models.toml`` para ``paths.models``, com
  arquivo temporário ``.part`` (retomável via HTTP Range) e verificação sha256. Item com sha256 vazio é
  recusado sem ``--allow-unverified``.
* ``engine start [--foreground]``: no Linux, sob ``systemd-run --user --scope -p MemoryMax=<engine.memory_max>``
  quando houver; PID/porta em ``<cache>/engine.json``. ``engine stop`` mata só esse PID. ``engine status [--json]``.

Tudo que toca o sistema (which, subprocess, kill, plataforma, saúde, download) passa por ``RUNTIME`` para os
testes injetarem fakes.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform as _platform
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .data import load_manifest
from .engines import nemo
from .engines.base import EngineError, EngineMissing

BACKENDS = ("auto", "cuda13", "cuda12", "cpu", "metal", "vulkan")
SIDECAR_ROLES = ("asr", "streaming", "diarizer", "vad")
STATE_FILE = "engine.json"


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return p.returncode, p.stdout


def _launch(cmd: list[str], log: Path, foreground: bool) -> Any:
    log.parent.mkdir(parents=True, exist_ok=True)
    out = open(log, "ab")
    return subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            start_new_session=not foreground)


@dataclass
class Runtime:
    which: Callable[[str], str | None] = shutil.which
    run: Callable[..., tuple[int, str]] = _run
    launcher: Callable[[list[str], Path, bool], Any] = _launch
    kill: Callable[[int, int], None] = os.kill
    platform: str = sys.platform
    machine: str = field(default_factory=_platform.machine)
    health: Callable[[Config], dict[str, Any]] = nemo.health
    urlopen: Callable[..., Any] = urllib.request.urlopen
    sleep: Callable[[float], None] = time.sleep


RUNTIME = Runtime()


# --------------------------------------------------------------------------------- backend e manifesto

def canon_os(p: str) -> str:
    p = p.lower()
    if p.startswith("linux"):
        return "linux"
    if p.startswith("darwin") or p == "macos":
        return "darwin"
    if p.startswith("win"):
        return "win32"
    return p


def canon_arch(m: str) -> str:
    m = m.lower()
    return {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x86_64", "amd64": "x86_64", "x64": "x86_64"}.get(m, m)


def detect_backend(platform: str, machine: str, gpu_csv: str | None) -> str:
    """``gpu_csv`` = saída de ``nvidia-smi --query-gpu=name,driver_version --format=csv,noheader`` (ou None)."""
    osname, arch = canon_os(platform), canon_arch(machine)
    if osname == "darwin":
        return "metal" if arch == "arm64" else "cpu"
    if gpu_csv:
        line = gpu_csv.strip().splitlines()[0] if gpu_csv.strip() else ""
        name, _, driver = line.partition(",")
        try:
            major = int(driver.strip().split(".")[0])
        except ValueError:
            major = 0
        if "gb10" in name.lower() or "dgx spark" in name.lower() or major >= 580:
            return "cuda13"
        if name.strip():
            return "cuda12"
    return "cpu"


def gpu_info(rt: Runtime | None = None) -> str | None:
    rt = rt or RUNTIME
    if not rt.which("nvidia-smi"):
        return None
    rc, out = rt.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"])
    return out.strip() if rc == 0 and out.strip() else None


def select_archive(manifest: dict[str, Any], platform: str, machine: str, backend: str
                   ) -> tuple[dict[str, Any] | None, str]:
    """(archive, backend efetivo). Sem archive para o backend pedido, tenta ``cpu`` do mesmo SO/arquitetura."""
    osname, arch = canon_os(platform), canon_arch(machine)
    for want in (backend, "cpu"):
        for a in manifest.get("archive", []):
            if canon_os(str(a.get("os"))) == osname and canon_arch(str(a.get("arch"))) == arch \
                    and a.get("backend") == want:
                return a, want
    return None, backend


def archive_url(manifest: dict[str, Any], archive: dict[str, Any]) -> str:
    if archive.get("url"):
        return str(archive["url"])
    rel = manifest.get("release", {})
    version = str(rel.get("version") or "latest")
    release_path = "latest/download" if version == "latest" else f"download/{version}"
    ext = "zip" if archive.get("format") == "zip" else "tar.gz"
    return str(rel["url_template"]).format(release_path=release_path, archive=archive["name"], ext=ext,
                                           version=version)


def models_dir(config: Config) -> Path:
    return config.path("paths.models")


def install_plan(config: Config, manifest: dict[str, Any], backend: str, *, onnx: bool = False,
                 rt: Runtime | None = None) -> dict[str, Any]:
    rt = rt or RUNTIME
    items: list[dict[str, Any]] = []
    mdir = models_dir(config)
    effective = backend
    if not onnx:
        arch, effective = select_archive(manifest, rt.platform, rt.machine, backend)
        if arch is None:
            raise EngineError(f"não há build do NeMo-Speech.cpp para {canon_os(rt.platform)}/"
                              f"{canon_arch(rt.machine)} ({backend}); use o motor onnx (CPU)")
        ext = "zip" if arch.get("format") == "zip" else "tar.gz"
        items.append({"kind": "engine", "name": arch["name"], "url": archive_url(manifest, arch),
                      "sha256": str(arch.get("sha256") or ""), "size_mb": arch.get("size_mb"),
                      "license": arch.get("license", ""), "dest": str(mdir / "downloads" / f"{arch['name']}.{ext}"),
                      "extract_to": str(mdir / "nemo-speech" / arch["name"]), "format": ext})
    roles = ("onnx-segmentation", "onnx-embedding") if onnx else SIDECAR_ROLES
    for m in manifest.get("model", []):
        if m.get("role") not in roles:
            continue
        item = {"kind": "model", "name": m["name"], "url": m["url"], "sha256": str(m.get("sha256") or ""),
                "size_mb": m.get("size_mb"), "license": m.get("license", ""), "role": m["role"],
                "dest": str(mdir / m["file"])}
        if m.get("archive"):
            item["format"] = m["archive"]
            item["dest"] = str(mdir / "downloads" / f"{m['name']}.{m['archive']}")
            item["extract_to"] = str(mdir)
            item["check"] = str(mdir / m["file"])
        items.append(item)
    return {"backend": effective, "requested": backend, "items": items}


# ----------------------------------------------------------------------------------------- download

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, sha256: str, *, allow_unverified: bool = False,
             rt: Runtime | None = None) -> str:
    """Baixa para ``dest.part`` (retoma com Range se já houver parte), confere sha256 e move. Devolve o hash."""
    rt = rt or RUNTIME
    if not sha256 and not allow_unverified:
        raise EngineError(f"{dest.name}: sem sha256 no manifesto; use --allow-unverified para baixar mesmo assim")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and sha256 and sha256_file(dest) == sha256:
        return sha256
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.is_file() else 0
    req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
    try:
        with rt.urlopen(req, timeout=60) as resp:
            status = getattr(resp, "status", None) or getattr(resp, "code", 200)
            mode = "ab" if have and status == 206 else "wb"
            with open(part, mode) as f:
                shutil.copyfileobj(resp, f, 1 << 20)
    except OSError as exc:
        raise EngineError(f"falha ao baixar {dest.name} ({type(exc).__name__}); rode de novo para retomar") from None
    got = sha256_file(part)
    if sha256 and got != sha256:
        part.unlink(missing_ok=True)
        raise EngineError(f"{dest.name}: sha256 não confere (esperado {sha256[:12]}…, veio {got[:12]}…)")
    os.replace(part, dest)
    return got


def extract(archive: Path, target: Path, fmt: str) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if fmt == "zip":
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                if n.startswith("/") or ".." in Path(n).parts:
                    raise EngineError(f"{archive.name}: caminho inseguro no zip")
            z.extractall(target)
        return
    with tarfile.open(archive) as t:
        try:
            t.extractall(target, filter="data")
        except TypeError:  # pragma: no cover - Python sem filtros de tarfile
            t.extractall(target)


# --------------------------------------------------------------------------------------- estado

def state_path(config: Config) -> Path:
    return config.cache / STATE_FILE


def read_state(config: Config) -> dict[str, Any] | None:
    p = state_path(config)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def write_state(config: Config, state: dict[str, Any]) -> None:
    p = state_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def pid_alive(pid: int, rt: Runtime | None = None) -> bool:
    rt = rt or RUNTIME
    try:
        rt.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def find_binary(config: Config, manifest: dict[str, Any] | None = None, rt: Runtime | None = None) -> str | None:
    rt = rt or RUNTIME
    explicit = config.get("engine.binary")
    if explicit and Path(str(explicit)).expanduser().is_file():
        return str(Path(str(explicit)).expanduser())
    name = str((manifest or load_manifest()).get("release", {}).get("binary") or "nemo-speech")
    root = models_dir(config) / "nemo-speech"
    if root.is_dir():
        for cand in sorted(root.rglob(name)) + sorted(root.rglob(name + ".exe")):
            if cand.is_file() and (os.access(cand, os.X_OK) or cand.suffix == ".exe"):
                return str(cand)
    return rt.which(name)


def serve_command(config: Config, binary: str, manifest: dict[str, Any], rt: Runtime | None = None) -> tuple[list[str], str | None]:
    rt = rt or RUNTIME
    host = str(config.get("engine.host") or "127.0.0.1")
    port = int(config.get("engine.port") or 47520)
    tmpl = manifest.get("release", {}).get("serve_args") or ["serve", "--host", "{host}", "--port", "{port}"]
    args = [str(a).format(host=host, port=port, models=str(models_dir(config))) for a in tmpl]
    cmd = [binary, *args]
    unit = None
    if canon_os(rt.platform) == "linux" and rt.which("systemd-run"):
        unit = f"ata-engine-{port}"
        mem = str(config.get("engine.memory_max") or "24G")
        cmd = ["systemd-run", "--user", "--scope", "--quiet", f"--unit={unit}", "-p", f"MemoryMax={mem}",
               "-p", "MemorySwapMax=0", "--", *cmd]
    return cmd, unit


def engine_status(config: Config, rt: Runtime | None = None) -> dict[str, Any]:
    rt = rt or RUNTIME
    st = read_state(config) or {}
    pid = st.get("pid")
    running = bool(pid) and pid_alive(int(pid), rt)
    h = rt.health(config) if running or not st else {"ok": False}
    if not st and h.get("ok"):
        running = True          # sidecar iniciado fora do ata (ex.: à mão)
    return {"running": running, "healthy": bool(h.get("ok")), "pid": pid if running else None,
            "host": config.get("engine.host"), "port": config.get("engine.port"), "unit": st.get("unit"),
            "started_at": st.get("started_at"), "managed": bool(st), "stale_state": bool(st) and not running}


# ---------------------------------------------------------------------------------------- comandos

def cmd_install(args: Any, config: Config) -> int:
    rt = RUNTIME
    manifest = load_manifest()
    backend = args.backend
    if backend == "auto":
        backend = "cpu" if args.onnx else detect_backend(rt.platform, rt.machine, gpu_info(rt))
    plan = install_plan(config, manifest, backend, onnx=args.onnx, rt=rt)
    if not args.onnx:
        print(f"backend: {plan['backend']}" + (f" (pedido {plan['requested']}, sem build; usando cpu)"
                                                if plan["backend"] != plan["requested"] else ""))
    unverified = [it for it in plan["items"] if not it["sha256"]]
    for it in plan["items"]:
        flag = "" if it["sha256"] else "  [sha256 ausente]"
        print(f"  {it['kind']:6} {it['name']} ({it.get('size_mb') or '?'} MB, {it['license']}) "
              f"{it['url']} -> {it['dest']}{flag}")
    if args.dry_run:
        print(f"dry-run: {len(plan['items'])} itens, nada baixado")
        return 0
    if unverified and not args.allow_unverified:
        print(f"erro: {len(unverified)} item(ns) sem sha256 no manifesto ({', '.join(i['name'] for i in unverified)})."
              " Use --allow-unverified para baixar e registrar o hash obtido.", file=sys.stderr)
        return 1
    for it in plan["items"]:
        got = download(it["url"], Path(it["dest"]), it["sha256"], allow_unverified=args.allow_unverified, rt=rt)
        if not it["sha256"]:
            print(f"  aviso: {it['name']} sem verificação; sha256 obtido = {got} (cole no models.toml)")
        if it.get("extract_to"):
            extract(Path(it["dest"]), Path(it["extract_to"]), it.get("format", "tar.gz"))
        print(f"  ok {it['name']}")
    if not args.onnx:
        info = {"backend": plan["backend"], "items": [i["name"] for i in plan["items"]],
                "at": datetime.now().astimezone().isoformat(timespec="seconds")}
        (models_dir(config) / "nemo-speech").mkdir(parents=True, exist_ok=True)
        (models_dir(config) / "nemo-speech" / "installed.json").write_text(json.dumps(info, indent=2),
                                                                         encoding="utf-8")
    print("instalação concluída" + ("" if args.onnx else "; ligue com: ata engine start"))
    return 0


def cmd_start(args: Any, config: Config) -> int:
    rt = RUNTIME
    st = read_state(config)
    if st and st.get("pid") and pid_alive(int(st["pid"]), rt):
        print(f"motor já está rodando (pid {st['pid']}, porta {st.get('port')})")
        return 3
    manifest = load_manifest()
    binary = find_binary(config, manifest, rt)
    if not binary:
        raise EngineMissing("NeMo-Speech.cpp não instalado: ata engine install")
    cmd, unit = serve_command(config, binary, manifest, rt)
    log = config.cache / "engine.log"
    proc = rt.launcher(cmd, log, bool(args.foreground))
    state = {"pid": proc.pid, "host": config.get("engine.host"), "port": config.get("engine.port"),
             "unit": unit, "binary": binary, "log": str(log),
             "started_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    write_state(config, state)
    if args.foreground:
        print(f"motor nemo em primeiro plano (pid {proc.pid}); Ctrl+C para parar")
        try:
            rc = proc.wait()
        except KeyboardInterrupt:
            rt.kill(proc.pid, signal.SIGTERM)
            rc = 0
        finally:
            state_path(config).unlink(missing_ok=True)
        return 0 if rc in (0, -signal.SIGTERM) else 1
    wait_s = float(config.get("engine.start_timeout") or 30)
    deadline = time.monotonic() + wait_s
    while True:
        if rt.health(config).get("ok"):
            print(f"motor nemo rodando em http://{state['host']}:{state['port']} (pid {proc.pid})")
            return 0
        code = proc.poll() if hasattr(proc, "poll") else None
        if code is not None:
            state_path(config).unlink(missing_ok=True)
            raise EngineError(f"motor nemo saiu ao iniciar (código {code}); veja {log}")
        if time.monotonic() >= deadline:
            print(f"aviso: o motor (pid {proc.pid}) não respondeu em {wait_s:.0f} s; veja {log} "
                  "ou pare com: ata engine stop", file=sys.stderr)
            return 1
        rt.sleep(0.5)


def cmd_stop(args: Any, config: Config) -> int:
    rt = RUNTIME
    st = read_state(config)
    if not st or not st.get("pid"):
        print("motor não está rodando (iniciado pelo ata)")
        return 3
    pid = int(st["pid"])
    if not pid_alive(pid, rt):
        state_path(config).unlink(missing_ok=True)
        print(f"motor não está rodando (estado antigo do pid {pid} removido)")
        return 3
    rt.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not pid_alive(pid, rt):
            break
        rt.sleep(0.25)
    else:
        rt.kill(pid, signal.SIGKILL)
    state_path(config).unlink(missing_ok=True)
    print(f"motor parado (pid {pid})")
    return 0


def cmd_status(args: Any, config: Config) -> int:
    s = engine_status(config)
    if args.json:
        print(json.dumps(s, ensure_ascii=False))
        return 0
    if s["running"]:
        print(f"motor: rodando (pid {s['pid'] or '?'}) em {s['host']}:{s['port']} · "
              f"saúde: {'ok' if s['healthy'] else 'sem resposta'}")
    else:
        print("motor: parado" + (" (estado antigo; rode ata engine stop)" if s["stale_state"] else "")
              + " · ligue com: ata engine start")
    return 0


def add_parser(sub: Any) -> None:
    p = sub.add_parser("engine", help="instala/liga/desliga o motor local NeMo-Speech.cpp")
    esub = p.add_subparsers(dest="engine_command", metavar="ação")
    i = esub.add_parser("install", help="baixa o motor e os modelos (sha256)")
    i.add_argument("--backend", choices=BACKENDS, default="auto")
    i.add_argument("--dry-run", action="store_true", help="só mostra o que seria baixado")
    i.add_argument("--onnx", action="store_true", help="baixa os modelos do motor CPU (sherpa-onnx)")
    i.add_argument("--allow-unverified", action="store_true", help="aceita itens sem sha256 no manifesto")
    i.set_defaults(func=cmd_install)
    s = esub.add_parser("start", help="liga o sidecar")
    s.add_argument("--foreground", action="store_true")
    s.set_defaults(func=cmd_start)
    esub.add_parser("stop", help="desliga o sidecar iniciado pelo ata").set_defaults(func=cmd_stop)
    st = esub.add_parser("status", help="estado do sidecar")
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=cmd_status)
    p.set_defaults(func=lambda args, config: (p.print_help(), 2)[1])
