"""Servidor HTTP fake em 127.0.0.1 (porta livre) para testar os clientes nemo e ollama sem motor real."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

Route = Callable[[dict[str, Any]], tuple[int, Any]]


@dataclass
class FakeServer:
    url: str
    port: int
    requests: list[dict[str, Any]] = field(default_factory=list)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def fake_http():
    servers: list[ThreadingHTTPServer] = []

    def start(routes: dict[tuple[str, str], Route | tuple[int, Any]]) -> FakeServer:
        state = FakeServer(url="", port=0)

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silencioso
                pass

            def _handle(self, method: str) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                req = {"method": method, "path": self.path, "headers": dict(self.headers), "body": body}
                state.requests.append(req)
                route = routes.get((method, self.path.split("?")[0]))
                if route is None:
                    status, payload = 404, {"error": "not found"}
                elif callable(route):
                    status, payload = route(req)
                else:
                    status, payload = route
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        servers.append(srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        state.port = srv.server_address[1]
        state.url = f"http://127.0.0.1:{state.port}"
        return state

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


@pytest.fixture
def closed_port() -> int:
    """Porta onde ninguém escuta (conexão recusada)."""
    return free_port()
