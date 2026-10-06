"""Servidor do dashboard: ``ThreadingHTTPServer`` só em 127.0.0.1.

Segurança: Host precisa ser loopback (127.0.0.1/localhost/[::1] com a porta certa); o token secreto fica em
``<cache>/dashboard-token`` (0600) e a 1ª visita ``/?t=<token>`` grava um cookie HttpOnly SameSite=Strict.
Toda rota exige cookie ou cabeçalho ``X-Ata-Token``; POST exige também Origin loopback (quando presente).
Ids de reunião passam por ``api.resolve_meeting`` (sem path traversal) e estáticos são uma lista fixa.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from ..config import Config
from . import api

STATIC_DIR = Path(__file__).with_name("static")
STATIC_FILES = {"/": ("index.html", "text/html; charset=utf-8"),
                "/index.html": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8")}
COOKIE = "ata_t"
TOKEN_HEADER = "X-Ata-Token"
MAX_BODY = 64 * 1024
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def load_token(config: Config) -> str:
    """Segredo persistente do dashboard (criado na 1ª vez, permissão 0600)."""
    p = config.cache / "dashboard-token"
    try:
        tok = p.read_text(encoding="utf-8").strip()
        if len(tok) >= 32:
            return tok
    except OSError:
        pass
    p.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(tok)
    return tok


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: Config, port: int = 0, token: str | None = None, sse_interval: float = 1.0):
        self.config = config
        self.token = token or load_token(config)
        self.stopping = threading.Event()
        self.sse_interval = sse_interval
        self.jobs = api.Jobs()
        super().__init__(("127.0.0.1", port), Handler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def shutdown(self) -> None:  # para os loops SSE antes
        self.stopping.set()
        super().shutdown()


_ROUTE_MEETING = re.compile(r"^/api/meetings/([^/]+)$")
_ROUTE_AUDIO = re.compile(r"^/api/meetings/([^/]+)/audio/([^/]+)$")
_ROUTE_SPEAKERS = re.compile(r"^/api/meetings/([^/]+)/speakers$")


class Handler(BaseHTTPRequestHandler):
    server: DashboardServer
    server_version = "ata-dashboard"
    sys_version = ""

    # sem log de requisição (a URL pode ter termos de busca)
    def log_message(self, format: str, *args: Any) -> None:
        return

    # ---- respostas ----------------------------------------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; media-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj: Any, status: int = 200) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _err(self, status: int, msg: str) -> None:
        self._json({"ok": False, "error": msg}, status)

    # ---- segurança ----------------------------------------------------------------------------------------
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        port = self.server.port
        return host in {f"{h}:{port}" for h in LOOPBACK_HOSTS}

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        port = self.server.port
        return origin.lower() in {f"http://{h}:{port}" for h in LOOPBACK_HOSTS}

    def _cookie_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            c = SimpleCookie(raw)
        except Exception:  # noqa: BLE001
            return None
        return c[COOKIE].value if COOKIE in c else None

    def _authed(self) -> bool:
        tok = self.server.token
        for cand in (self._cookie_token(), self.headers.get(TOKEN_HEADER)):
            if cand and hmac.compare_digest(cand, tok):
                return True
        return False

    # ---- roteamento ---------------------------------------------------------------------------------------
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._err(403, "Host não permitido (use 127.0.0.1)")
        url = urlsplit(self.path)
        q = parse_qs(url.query)
        path = url.path
        if path == "/" and "t" in q:
            given = q["t"][0]
            if hmac.compare_digest(given, self.server.token):
                return self._send(303, b"", "text/plain", {
                    "Location": "/",
                    "Set-Cookie": f"{COOKIE}={self.server.token}; HttpOnly; SameSite=Strict; Path=/"})
            return self._err(403, "token inválido")
        if not self._authed():
            if path in ("/", "/index.html"):
                body = ("<!doctype html><meta charset=utf-8><title>Ata</title><p>Abra o endereço com o token "
                        "impresso por <code>ata dashboard</code>.</p>").encode()
                return self._send(403, body, "text/html; charset=utf-8")
            return self._err(403, "sem token: abra o link impresso por `ata dashboard`")
        try:
            self._route_get(path, q)
        except api.ApiError as exc:
            self._err(exc.status, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001
            self._err(500, f"erro interno: {type(exc).__name__}")

    def _route_get(self, path: str, q: dict[str, list[str]]) -> None:
        cfg = self.server.config
        one = lambda k, d=None: (q.get(k) or [d])[0]
        if path in STATIC_FILES:
            name, ctype = STATIC_FILES[path]
            return self._send(200, (STATIC_DIR / name).read_bytes(), ctype)
        if path == "/api/status":
            st = api.recorder_status(cfg)
            live = api.current_live_bundle(cfg)
            return self._json({"recorder": st, "live_bundle": live.name if live else None})
        if path == "/api/meetings":
            return self._json(api.list_meetings(cfg, query=one("q"), participant=one("participant"),
                                                language=one("language"), limit=one("limit"),
                                                cursor=one("cursor")))
        if path == "/api/search":
            return self._json(api.search(cfg, one("q", ""), mode=one("mode", "hybrid"), limit=one("limit"),
                                         cursor=one("cursor")))
        if path == "/api/config":
            return self._json(api.config_view(cfg))
        if path == "/api/doctor":
            return self._json(api.doctor(cfg))
        if path == "/api/actions":
            return self._json(api.collect_items(cfg, "actions", owner=one("owner"), limit=one("limit")))
        if path.startswith("/api/jobs/"):
            return self._json(self.server.jobs.get(unquote(path[len("/api/jobs/"):])))
        if path == "/api/events":
            return self._sse(one("meeting"))
        m = _ROUTE_AUDIO.match(path)
        if m:
            return self._audio(unquote(m.group(1)), unquote(m.group(2)))
        m = _ROUTE_MEETING.match(path)
        if m:
            return self._json(api.meeting_detail(cfg, unquote(m.group(1)),
                                                 parts=["meta", "summary", "transcript", "my_notes"]))
        self._err(404, "rota desconhecida")

    def do_POST(self) -> None:
        if not self._host_ok() or not self._origin_ok():
            return self._err(403, "origem não permitida")
        if not self._authed():
            return self._err(403, "sem token")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            return self._err(413, "corpo grande demais")
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._err(400, "JSON inválido")
        cfg = self.server.config
        path = urlsplit(self.path).path
        try:
            if path == "/api/record/start":
                return self._json(api.record_start(cfg, title=body.get("title") or None,
                                                   language=body.get("language") or None))
            if path == "/api/record/stop":
                return self._json(api.record_stop(cfg, process=bool(body.get("process", True)),
                                                  jobs=self.server.jobs))
            m = _ROUTE_SPEAKERS.match(path)
            if m or path == "/api/speakers":
                mid = unquote(m.group(1)) if m else str(body.get("meeting", ""))
                return self._json(api.rename_speakers(cfg, mid, body.get("names") or {},
                                                      do_rerender=bool(body.get("rerender", True))))
            self._err(404, "rota desconhecida")
        except api.ApiError as exc:
            self._err(exc.status, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._err(500, f"erro interno: {type(exc).__name__}")

    # ---- áudio com Range ----------------------------------------------------------------------------------
    def _audio(self, mid: str, track: str) -> None:
        if track not in ("far", "mic"):
            raise api.ApiError("faixa inválida (far ou mic)", 400)
        bdir = api.resolve_meeting(self.server.config, mid)
        f = bdir / f"{track}.wav"
        if not f.is_file():
            raise api.ApiError("faixa ausente", 404)
        size = f.stat().st_size
        rng = self.headers.get("Range")
        start, end, status = 0, size - 1, 200
        if rng:
            m = re.match(r"^bytes=(\d*)-(\d*)$", rng.strip())
            if not m or (not m.group(1) and not m.group(2)):
                return self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                return self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
            status = 206
        with open(f, "rb") as fh:
            fh.seek(start)
            data = fh.read(end - start + 1)
        extra = {"Accept-Ranges": "bytes"}
        if status == 206:
            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
        self._send(status, data, "audio/wav", extra)

    # ---- SSE ----------------------------------------------------------------------------------------------
    def _sse(self, meeting: str | None) -> None:
        cfg = self.server.config
        bdir = api.resolve_meeting(cfg, meeting) if meeting else None
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        since = 0
        last_dir: Path | None = None
        try:
            while not self.server.stopping.is_set():
                st = api.recorder_status(cfg)
                target = bdir or api.current_live_bundle(cfg)
                if target != last_dir:
                    since, last_dir = 0, target
                self._event("status", {"recorder": st, "live_bundle": target.name if target else None})
                if target is not None:
                    turns, since = api.live_turns(target, since)
                    for t in turns:
                        self._event("turn", t)
                self.wfile.flush()
                self.server.stopping.wait(self.server.sse_interval)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _event(self, name: str, data: Any) -> None:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        self.wfile.write(f"event: {name}\ndata: {payload}\n\n".encode())


# ---- comando -----------------------------------------------------------------------------------------------

def serve(config: Config, port: int | None = None, open_browser: bool = True) -> int:
    p = int(port if port is not None else config.get("dashboard.port", 47530))
    try:
        srv = DashboardServer(config, p)
    except OSError as exc:
        print(f"erro: não consegui abrir 127.0.0.1:{p} ({exc.strerror})", file=sys.stderr)
        return 1
    print(f"Dashboard do Ata em {srv.url}")
    print("(só neste computador; Ctrl+C para sair)")
    if open_browser:
        threading.Thread(target=lambda: (time.sleep(0.3), webbrowser.open(srv.url)), daemon=True).start()
    try:
        srv.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stopping.set()
        srv.server_close()
    return 0


def _cmd(args: argparse.Namespace, config: Config) -> int:
    return serve(config, args.port, open_browser=not args.no_browser)


def add_parser(sub: Any) -> None:
    p = sub.add_parser("dashboard", help="painel local no navegador (127.0.0.1)",
                       description="Painel local: gravar, ver reuniões, ouvir com a transcrição, buscar, ao vivo.")
    p.add_argument("--port", type=int, default=None, help="porta (padrão: dashboard.port = 47530)")
    p.add_argument("--no-browser", action="store_true", help="não abre o navegador")
    p.set_defaults(func=_cmd)
