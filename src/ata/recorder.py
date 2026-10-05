"""Gravador: comandos ``start``/``stop``/``toggle``/``status`` e o supervisor destacado que segura a captura.

Contrato (docs/INTERFACES.md §A):
- ``start(config, *, title=None, speakers=None, language=None, backend=None) -> Path`` cria o bundle, sobe
  ``python -m ata.recorder supervise <bundle> --request <json>`` em sessão própria e só volta quando o
  supervisor confirmou a captura (``meta.json`` escrito) — falha do backend vira CaptureError/CaptureMissing.
  ``backend`` é um nome opcional (``fake``, ``linux``, ``windows``, ``macos``) repassado como ``ATA_CAPTURE``.
- ``stop(config, *, process=True) -> {"bundle": str, "note": str|None}``: cria ``<bundle>/STOP``, espera o
  supervisor sair (SIGTERM e depois SIGKILL se travar) e, com ``process=True``, chama
  ``ata.pipeline.run.process_bundle(bundle_dir, config)`` (import preguiçoso).
- ``status(config) -> {"phase": "idle"|"recording", "bundle", "started_at", "elapsed_s", "pid"}``.
- Estado em ``<cache>/state.json`` (dono único: este módulo, nunca o supervisor), com PID do supervisor.
  PID morto, zumbi ou reutilizado por outro programa = estado velho: o bundle é recuperado (cabeçalhos WAV
  consertados, ``recorder_killed``) e o estado é limpo.
- Já gravando / nada gravando -> AlreadyRecording / NothingRecording (CLI sai com 3).
- O supervisor escreve ``meta.json`` (schema ata/1) no início e no fim, e ``recorder.log`` (sem texto de reunião).
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from . import __version__, bundle, i18n
from .capture.base import CaptureError, CaptureMissing, backend_for_platform, clean_reasons, finalize_track
from .config import Config

STATE_FILE = "state.json"
LOCK_FILE = "recorder.lock"
REQUEST_DIR = "recorder-requests"
LOG_FILE = "recorder.log"
START_TIMEOUT_S = 15.0
STOP_TIMEOUT_S = 60.0
POLL_S = 0.05

log = logging.getLogger("ata.recorder")

# Popen dos supervisores lançados por ESTE processo: permite reaproveitar (reap) e não confundir zumbi com vivo.
_CHILDREN: dict[int, subprocess.Popen] = {}


class RecorderError(RuntimeError):
    pass


class AlreadyRecording(RecorderError):
    def __init__(self, bundle_dir: str) -> None:
        super().__init__(f"já está gravando: {bundle_dir}")
        self.bundle = bundle_dir


class NothingRecording(RecorderError):
    pass


# ---- estado ----------------------------------------------------------------------------------------------

def state_path(config: Config) -> Path:
    return config.cache / STATE_FILE


def read_state(config: Config) -> dict[str, Any] | None:
    p = state_path(config)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        p.unlink(missing_ok=True)   # estado corrompido não pode travar o gravador
        return None
    return d if isinstance(d, dict) and isinstance(d.get("pid"), int) and d.get("bundle") else None


def _write_state(config: Config, data: dict[str, Any]) -> None:
    config.cache.mkdir(parents=True, exist_ok=True)
    bundle.write_json(state_path(config), data)


def _clear_state(config: Config, pid: int | None = None) -> None:
    st = read_state(config)
    if st is None or pid is None or st.get("pid") == pid:
        state_path(config).unlink(missing_ok=True)


@contextlib.contextmanager
def _lock(config: Config) -> Iterator[None]:
    """Lock exclusivo entre processos para start/toggle (dois `ata start` simultâneos)."""
    config.cache.mkdir(parents=True, exist_ok=True)
    with open(config.cache / LOCK_FILE, "a+b") as f:
        if os.name == "nt":  # pragma: no cover - Windows
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def pid_alive(pid: int) -> bool:
    """Processo vivo E ainda é um supervisor do Ata (zumbi ou PID reutilizado contam como morto)."""
    child = _CHILDREN.get(pid)
    if child is not None:
        return child.poll() is None
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - Windows
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    proc = Path(f"/proc/{pid}")
    if proc.is_dir():
        try:
            st = (proc / "stat").read_text().rsplit(")", 1)[1].split()
            if st and st[0] in ("Z", "X"):
                return False
            if b"ata.recorder" not in (proc / "cmdline").read_bytes():
                return False
        except (OSError, IndexError):
            return False
    return True


def _pid_exists(pid: int) -> bool:
    """Existe QUALQUER processo com esse PID (inclusive zumbi e de outro programa)."""
    if os.name == "nt":  # pragma: no cover
        return pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reap(pid: int) -> None:
    child = _CHILDREN.pop(pid, None)
    if child is not None:
        with contextlib.suppress(subprocess.TimeoutExpired):
            child.wait(timeout=5)


def _kill_group(pid: int, sig: int) -> None:
    """Sinal para o grupo do supervisor (inclui pw-record órfãos). Silencioso se já não existe."""
    if os.name == "nt":  # pragma: no cover
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)


def recover_bundle(bundle_dir: Path) -> bool:
    """Fecha um bundle cujo supervisor morreu: conserta WAVs, mede faixas, ``stopped_at`` e ``recorder_killed``.

    Devolve True se mexeu no meta. Bundle já fechado (``stopped_at`` presente) ou sem meta -> False."""
    try:
        meta = bundle.read_meta(bundle_dir)
    except bundle.BundleError:
        return False
    if meta.stopped_at is not None:
        return False
    tracks = {}
    for k, tr in meta.tracks.items():
        new = finalize_track(Path(bundle_dir), tr.file, device=tr.device, start_epoch=tr.start_epoch,
                             start_measured=tr.start_measured)
        if new is not None:
            tracks[k] = new
    reasons = clean_reasons([*meta.damage_reasons, "recorder_killed"])
    bundle.write_meta(bundle_dir, meta.with_(stopped_at=_now_iso(), tracks=tracks, damage_reasons=tuple(reasons)))
    (Path(bundle_dir) / bundle.STOP_FILE).unlink(missing_ok=True)
    return True


def _live_state(config: Config) -> dict[str, Any] | None:
    """Estado se há gravação viva; estado velho é recuperado e limpo (devolve None)."""
    st = read_state(config)
    if st is None:
        return None
    if pid_alive(st["pid"]):
        return st
    _settle_dead(config, st)
    return None


def _settle_dead(config: Config, st: dict[str, Any]) -> bool:
    """Supervisor já morreu: encerra órfãos, recupera o bundle se ficou aberto, limpa o estado.
    Devolve True se o bundle terminou limpo (o próprio supervisor escreveu o meta final)."""
    pid = st["pid"]
    _reap(pid)
    bdir = Path(st["bundle"])
    clean = False
    try:
        clean = bundle.read_meta(bdir).stopped_at is not None
    except bundle.BundleError:
        pass
    if not clean and not _pid_exists(pid):
        # só quando o PID não existe mais: um grupo com esse id então só pode ser de órfãos do supervisor
        # (o kernel não reaproveita um PID em uso como id de grupo). PID reutilizado por outro programa: nada.
        _kill_group(pid, signal.SIGINT)
        time.sleep(0.2)
        _kill_group(pid, signal.SIGKILL)
    if not clean:
        if recover_bundle(bdir):
            log.warning("supervisor %s morreu; bundle recuperado com recorder_killed", pid)
    _clear_state(config, pid)
    return clean


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---- API -------------------------------------------------------------------------------------------------

def start(config: Config, *, title: str | None = None, speakers: int | None = None,
          language: str | None = None, backend: str | None = None, process: bool = True,
          timeout: float = START_TIMEOUT_S) -> Path:
    """Começa a gravar. Devolve a pasta do bundle. Ver contrato no topo do módulo.

    ValueError: idioma ou speakers inválidos. AlreadyRecording: já há gravação viva.
    CaptureMissing/CaptureError: o supervisor não conseguiu iniciar a captura."""
    lang = i18n.normalize(language or config.get("language.default") or i18n.DEFAULT_LANGUAGE)
    if speakers is not None and (not isinstance(speakers, int) or isinstance(speakers, bool) or speakers < 1):
        raise ValueError("--speakers deve ser um inteiro >= 1")
    with _lock(config):
        live = _live_state(config)
        if live is not None:
            raise AlreadyRecording(live["bundle"])
        started = datetime.now().astimezone()
        bdir = bundle.new_bundle_dir(config.recordings, started, title)
        req_dir = config.cache / REQUEST_DIR
        req_dir.mkdir(parents=True, exist_ok=True)
        req = req_dir / f"{bdir.name}.json"
        bundle.write_json(req, {"config": config.data, "title": title, "speakers": speakers, "language": lang,
                                "started_at": started.isoformat(timespec="seconds")})
        env = dict(os.environ)
        if backend:
            env["ATA_CAPTURE"] = backend
        argv = [sys.executable, "-m", "ata.recorder", "supervise", str(bdir), "--request", str(req)]
        kwargs: dict[str, Any] = {}
        if os.name == "nt":  # pragma: no cover
            kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        with open(bdir / LOG_FILE, "ab") as errlog:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errlog,
                                     env=env, cwd=str(bdir), **kwargs)
        _CHILDREN[child.pid] = child
        _write_state(config, {"pid": child.pid, "bundle": str(bdir), "started_at": started.isoformat(
            timespec="seconds"), "started_epoch": started.timestamp(), "title": title, "language": lang,
            "speakers": speakers, "process": bool(process)})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (bdir / bundle.META_FILE).is_file():
                return bdir
            if child.poll() is not None:
                break
            time.sleep(POLL_S)
        code = child.poll()
        if code is None:
            child.kill()
            child.wait()
        _CHILDREN.pop(child.pid, None)
        if (bdir / bundle.META_FILE).is_file() and code == 0:
            # terminou rápido demais (ex.: STOP imediato), mas o bundle existe
            _clear_state(config, child.pid)
            return bdir
        msg = _last_error(bdir / LOG_FILE) or ("o supervisor não respondeu" if code is None
                                               else f"o supervisor saiu com código {code}")
        _clear_state(config, child.pid)
        shutil.rmtree(bdir, ignore_errors=True)
        req.unlink(missing_ok=True)
        if code == 4:
            raise CaptureMissing(msg)
        raise CaptureError(msg)


def _last_error(logfile: Path) -> str | None:
    try:
        lines = logfile.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if "erro: " in line:
            return line.split("erro: ", 1)[1].strip()
    return None


def stop(config: Config, *, process: bool = True, timeout: float = STOP_TIMEOUT_S) -> dict[str, Any]:
    """Para a gravação e (opcional) processa. ``{"bundle": str, "note": str|None}``. NothingRecording se não
    há gravação; supervisor morto sem fechar o bundle -> recupera e levanta NothingRecording explicando."""
    st = read_state(config)
    if st is None:
        raise NothingRecording("nada gravando")
    pid, bdir = st["pid"], Path(st["bundle"])
    if not pid_alive(pid):
        if not _settle_dead(config, st):
            raise NothingRecording(f"o gravador tinha parado sozinho; gravação recuperada: {bdir}")
    else:
        bdir.mkdir(parents=True, exist_ok=True)
        (bdir / bundle.STOP_FILE).touch()
        if not _wait_dead(pid, timeout):
            log.warning("supervisor %s não parou com STOP; SIGTERM", pid)
            _signal(pid, signal.SIGTERM)
            if not _wait_dead(pid, 10.0):
                _kill_group(pid, signal.SIGKILL)
                _wait_dead(pid, 5.0)
        _reap(pid)
        recover_bundle(bdir)   # no-op se o supervisor fechou o meta
        _clear_state(config, pid)
    (config.cache / REQUEST_DIR / f"{bdir.name}.json").unlink(missing_ok=True)
    note = _process(bdir, config) if process else None
    return {"bundle": str(bdir), "note": note}


def _signal(pid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.kill(pid, sig)


def _wait_dead(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(POLL_S)
    return not pid_alive(pid)


def _process(bdir: Path, config: Config) -> str | None:
    try:
        run = importlib.import_module("ata.pipeline.run")
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("ata.pipeline"):
            print("aviso: processamento indisponível nesta instalação (ata.pipeline.run); "
                  f"rode depois: ata process {bdir}", file=sys.stderr)
            return None
        raise
    note = run.process_bundle(bdir, config)
    return None if note is None else str(note)


def status(config: Config) -> dict[str, Any]:
    """``{"phase", "bundle", "started_at", "elapsed_s", "pid"}`` (idle -> bundle/started_at/pid = None)."""
    st = _live_state(config)
    if st is None:
        return {"phase": "idle", "bundle": None, "started_at": None, "elapsed_s": None, "pid": None}
    epoch = st.get("started_epoch")
    elapsed = round(max(0.0, time.time() - float(epoch)), 1) if isinstance(epoch, (int, float)) else None
    return {"phase": "recording", "bundle": st["bundle"], "started_at": st.get("started_at"),
            "elapsed_s": elapsed, "pid": st["pid"]}


def toggle(config: Config, *, process: bool = True, title: str | None = None, speakers: int | None = None,
           language: str | None = None) -> dict[str, Any]:
    """Gravando -> para (``{"action": "stopped", **stop()}``); parado -> começa (``{"action": "started",
    "bundle": str}``)."""
    if status(config)["phase"] == "recording":
        return {"action": "stopped", **stop(config, process=process)}
    return {"action": "started", "bundle": str(start(config, title=title, speakers=speakers, language=language))}


# ---- supervisor ------------------------------------------------------------------------------------------

def supervise(bundle_dir: Path, request_path: Path) -> int:
    """Processo destacado: abre a captura, escreve meta.json, espera STOP/SIGTERM, fecha e regrava o meta.

    Saída: 0 ok · 1 falha de captura · 4 dependência ausente. Erros vão para recorder.log como ``erro: ...``."""
    bundle_dir = Path(bundle_dir)
    handler = logging.FileHandler(bundle_dir / LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("ata")
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    req = json.loads(Path(request_path).read_text(encoding="utf-8"))
    config = Config(req["config"])
    halt = {"flag": False}

    def _on_signal(signum: int, _frame: Any) -> None:
        halt["flag"] = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    try:
        backend = backend_for_platform(config=config)
        log.info("supervisor %s: backend %s", os.getpid(), backend.name)
        handle = backend.start(bundle_dir)
    except CaptureMissing as exc:
        log.error("erro: %s", exc)
        return 4
    except CaptureError as exc:
        log.error("erro: %s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        log.error("erro: falha inesperada ao iniciar a captura (%s)", type(exc).__name__)
        return 1

    title = req.get("title")
    meta = bundle.BundleMeta(
        name=bundle_dir.name, created_at=req.get("started_at") or _now_iso(), tracks=handle.preview(),
        slug=bundle.slugify(title), title=title, host=socket.gethostname(), platform=sys.platform,
        recorder={"name": backend.recorder_name, "version": __version__},
        language_requested=req.get("language") or "pt-BR", speakers_hint=req.get("speakers"))
    bundle.write_meta(bundle_dir, meta)
    log.info("gravando")

    died: str | None = None
    try:
        while not halt["flag"] and not (bundle_dir / bundle.STOP_FILE).exists():
            died = handle.poll()
            if died:
                log.warning("captura caiu: %s", died)
                break
            time.sleep(0.1)
    finally:
        try:
            tracks = handle.stop()
        except Exception as exc:  # noqa: BLE001
            log.error("erro: falha ao fechar a captura (%s)", type(exc).__name__)
            tracks = {}
        reasons = list(handle.damage_reasons)
        if died:
            reasons.append("recorder_killed")
        for w in handle.warnings:
            log.warning("%s", w)
        if not tracks:
            # sem nada medido: mantém o preview para o recover/relatório acusarem as faixas ausentes
            tracks = {k: t for k, t in meta.tracks.items() if (bundle_dir / t.file).is_file()}
        final = meta.with_(stopped_at=_now_iso(), tracks=tracks, damage_reasons=tuple(clean_reasons(reasons)))
        bundle.write_meta(bundle_dir, final)
        (bundle_dir / bundle.STOP_FILE).unlink(missing_ok=True)
        log.info("parado (%s)", ", ".join(f"{k}={t.seconds or 0:.1f}s" for k, t in tracks.items()) or "sem faixas")
    return 0


# ---- CLI -------------------------------------------------------------------------------------------------

def _cmd_start(args: argparse.Namespace, config: Config) -> int:
    try:
        bdir = start(config, title=args.title, speakers=args.speakers, language=args.lang,
                     process=not getattr(args, "no_process", False))
    except ValueError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    except AlreadyRecording as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except CaptureMissing as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 4
    except CaptureError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    print(f"gravando: {bdir}")
    print("para parar: ata stop")
    return 0


def _cmd_stop(args: argparse.Namespace, config: Config) -> int:
    st = read_state(config) or {}
    process = not args.no_process and bool(st.get("process", True))
    try:
        res = stop(config, process=process)
    except NothingRecording as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(f"gravação encerrada: {res['bundle']}")
    if res["note"]:
        print(f"note: {res['note']}")
    elif not process:
        print(f"para processar depois: ata process {res['bundle']}")
    return 0


def _cmd_toggle(args: argparse.Namespace, config: Config) -> int:
    if status(config)["phase"] == "recording":
        return _cmd_stop(args, config)
    return _cmd_start(args, config)


def _cmd_status(args: argparse.Namespace, config: Config) -> int:
    st = status(config)
    if args.json:
        print(json.dumps(st, ensure_ascii=False))
    elif st["phase"] == "recording":
        m, s = divmod(int(st["elapsed_s"] or 0), 60)
        print(f"gravando há {m:02d}:{s:02d}: {st['bundle']} (pid {st['pid']})")
    else:
        print("parado")
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    def start_opts(p: argparse.ArgumentParser) -> None:
        p.add_argument("--title", help="título da reunião (vira parte do nome da pasta)")
        p.add_argument("--speakers", type=int, help="quantas pessoas falam (dica para a diarização)")
        p.add_argument("--lang", help="idioma: pt-BR, en, es ou auto (padrão: [language] default)")

    p = sub.add_parser("start", help="começa a gravar (far = o que a máquina toca, mic = você)")
    start_opts(p)
    p.add_argument("--no-process", action="store_true", help="no stop, não processar automaticamente")
    p.set_defaults(func=_cmd_start)

    p = sub.add_parser("stop", help="para a gravação e gera a nota")
    p.add_argument("--no-process", action="store_true", help="só para; processar depois com `ata process`")
    p.set_defaults(func=_cmd_stop)

    p = sub.add_parser("toggle", help="começa ou para a gravação (bom para atalho de teclado)")
    start_opts(p)
    p.add_argument("--no-process", action="store_true", help="ao parar, não processar")
    p.set_defaults(func=_cmd_toggle)

    p = sub.add_parser("status", help="mostra se está gravando")
    p.add_argument("--json", action="store_true", help="saída JSON")
    p.set_defaults(func=_cmd_status)


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m ata.recorder")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("supervise")
    p.add_argument("bundle")
    p.add_argument("--request", required=True)
    args = ap.parse_args(argv)
    return supervise(Path(args.bundle), Path(args.request))


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
