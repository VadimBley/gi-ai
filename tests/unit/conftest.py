# Copyright (C) 2026 Vadim Bley
# This file is part of Ĝi (gi-ai).
#
# Ĝi is free software: you can redistribute it and/or modify it under the terms of the
# GNU Affero General Public License as published by the Free Software Foundation, version 3.
#
# Ĝi is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
# the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License along with Ĝi.
# If not, see <https://www.gnu.org/licenses/>.

"""Shared fixtures for unit tests: a fake LM Studio / Ollama HTTP server on 127.0.0.1.

The fake records every request (method, path, query, lower-cased headers, JSON body) and
answers with whatever the test configured per path. Nothing here leaves the loopback.
"""

from __future__ import annotations

import json
import socket
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

PROXY_VARS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
)

LM_MODEL = "fixture/model-ĝ"


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    """A developer's own token or proxy settings must never leak into a unit test."""
    monkeypatch.delenv("GI_AI_LLM_TOKEN", raising=False)
    for name in PROXY_VARS:
        monkeypatch.delenv(name, raising=False)


@dataclass
class Recorded:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    raw: bytes
    body: Any = None


@dataclass
class Route:
    status: int = 200
    body: Any = None
    raw: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)


class FakeServer:
    """A tiny HTTP server. `reply(path, ...)` sets the answer; `requests` holds what came in."""

    def __init__(self) -> None:
        self.requests: list[Recorded] = []
        self.routes: dict[tuple[str | None, str], Route] = {}
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _handle(self):
                parts = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = None
                rec = Recorded(
                    method=self.command,
                    path=parts.path,
                    query=parse_qs(parts.query),
                    headers={k.lower(): v for k, v in self.headers.items()},
                    raw=raw,
                    body=body,
                )
                with fake._lock:
                    fake.requests.append(rec)
                route = fake.routes.get((self.command, parts.path)) or fake.routes.get(
                    (None, parts.path)
                )
                if route is None:
                    route = Route(status=404, body={"error": "not found in fake"})
                data = route.raw if route.raw is not None else json.dumps(route.body).encode()
                self.send_response(route.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                for k, v in route.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)
                self.close_connection = True

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _handle

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.host, self.port = self.httpd.server_address[:2]
        self.url = f"http://{self.host}:{self.port}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, args=(0.05,), daemon=True)
        self._thread.start()

    def reply(
        self,
        path: str,
        body: Any = None,
        *,
        status: int = 200,
        method: str | None = None,
        raw: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        route = Route(status=status, body=body, raw=raw, headers=headers or {})
        self.routes[(method, path)] = route

    def requests_to(self, path: str) -> list[Recorded]:
        return [r for r in self.requests if r.path == path]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def make_fake_server():
    """Factory for any number of fake servers; all are shut down after the test."""
    servers: list[FakeServer] = []

    def make() -> FakeServer:
        srv = FakeServer()
        servers.append(srv)
        return srv

    yield make
    for srv in servers:
        srv.close()


@pytest.fixture
def fake_server(make_fake_server):
    return make_fake_server()


@pytest.fixture
def closed_port():
    """A loopback port nobody listens on (bound, then released)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)  # a JSON string is a valid TOML basic string
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    raise TypeError(f"cannot write {value!r} as TOML")


def write_toml(path, sections: dict[str, dict[str, Any]], mode: int = 0o600):
    lines = []
    for name, keys in sections.items():
        lines.append(f"[{name}]")
        lines += [f"{k} = {toml_value(v)}" for k, v in keys.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


@pytest.fixture
def gi_config(gi_env):
    """write(**llm_keys) replaces the user config with exactly these [llm] keys."""
    path = gi_env / ".config" / "gi-ai" / "gi.toml"

    def write(_extra_sections: dict[str, dict[str, Any]] | None = None, **llm: Any):
        sections = {"llm": llm, **(_extra_sections or {})}
        return write_toml(path, sections)

    write.path = path
    return write


@pytest.fixture
def lmstudio_config(gi_config, fake_server):
    """configure(**extra) points gi at the fake server with backend = "lmstudio"."""

    def configure(**extra: Any):
        keys: dict[str, Any] = {
            "backend": "lmstudio",
            "endpoint": fake_server.url,
            "model": LM_MODEL,
            "timeout_s": 5,
        }
        keys.update(extra)
        return gi_config(**{k: v for k, v in keys.items() if v is not None})

    configure.model = LM_MODEL
    configure.server = fake_server
    configure()
    return configure


def lm_chat_reply(*items: dict[str, Any], stats: dict[str, Any] | None = None, **top: Any):
    """A /api/v1/chat reply in LM Studio's documented native shape."""
    reply: dict[str, Any] = {
        "model_instance_id": LM_MODEL,
        "output": list(items),
        "response_id": "resp_fixture",
        **top,
    }
    if stats is not None:
        reply["stats"] = stats
    return reply


def lm_model_item(key: str = LM_MODEL, **overrides: Any) -> dict[str, Any]:
    """One /api/v1/models item using only the member names known to the spec (B13)."""
    item: dict[str, Any] = {
        "type": "llm",
        "publisher": "fixture",
        "key": key,
        "display_name": "Fixture Model",
        "architecture": "qwen3",
        "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
        "size_bytes": 5_000_000_000,
        "params_string": "9B",
        "loaded_instances": [{"id": key, "config": {"context_length": 4096}}],
        "max_context_length": 262144,
        "format": "gguf",
        "capabilities": {
            "vision": False,
            "trained_for_tool_use": True,
            "reasoning": {"allowed_options": ["off", "on"], "default": "on"},
        },
        "description": None,
        "variants": [key],
        "selected_variant": key,
    }
    item.update(overrides)
    return item


@pytest.fixture
def lm_replies():
    """Builders for LM Studio reply bodies, shared across test modules."""

    class Builders:
        chat = staticmethod(lm_chat_reply)
        model_item = staticmethod(lm_model_item)
        model = LM_MODEL

        @staticmethod
        def message(text: str) -> dict[str, Any]:
            return {"type": "message", "content": text}

        @staticmethod
        def reasoning(text: str) -> dict[str, Any]:
            return {"type": "reasoning", "content": text}

    return Builders
