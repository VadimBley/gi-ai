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

"""OllamaBackend against a fake local server: real HTTP, no real model.

SPEC-0001 v1.2.0 B10 / AT-21: any non-2xx HTTP status is a model error.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from gi_ai import llm
from gi_ai.config import LLMConfig
from gi_ai.llm import LLMError, Message, OllamaBackend


class FakeOllama(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict]] = []

    def log_message(self, *args):
        pass

    def _reply(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply({"models": []})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.seen.append(payload)
        self._reply({"message": {"role": "assistant", "content": "\x1b[2Jhi"}})


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_chat_round_trip(server):
    backend = OllamaBackend(LLMConfig(endpoint=server, model="m", timeout_s=5))
    assert backend.reachable()
    out = backend.chat([Message("system", "s"), Message("user", "q")])
    assert isinstance(out, llm.ChatReply)
    assert out.text == "\x1b[2Jhi"  # raw; the CLI sanitises before printing
    assert out.stats is None
    sent = FakeOllama.seen[-1]
    assert sent["stream"] is False and sent["model"] == "m"
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]


def test_unreachable_endpoint_is_reported():
    backend = OllamaBackend(LLMConfig(endpoint="http://127.0.0.1:9", timeout_s=2))
    assert not backend.reachable()
    with pytest.raises(LLMError, match="cannot reach"):
        backend.chat([Message("user", "q")])


# --- SPEC-0001 v1.1 B10: reply validation by body, using the shared fake server -------------


def _backend(url):
    return OllamaBackend(LLMConfig(endpoint=url, model="m", timeout_s=5))


@pytest.mark.parametrize(
    "body",
    [
        {"error": "no models", "models": []},
        {"error": {"message": "no models"}, "models": []},
        {"error": "no models"},
    ],
    ids=["error-and-models", "error-object", "error-only"],
)
def test_b10_tags_with_error_member_is_not_reachable(fake_server, body):
    fake_server.reply("/api/tags", body, method="GET")
    assert _backend(fake_server.url).reachable() is False
    fields = _backend(fake_server.url).health_fields()
    assert fields["reachable"] is False


@pytest.mark.parametrize(
    "raw",
    [b"{}", b'{"models": "x"}', b'{"models": null}', b"[]", b'"models"', b"not json", b""],
    ids=["no-models", "models-str", "models-null", "top-list", "top-str", "not-json", "empty"],
)
def test_b10_tags_with_invalid_body_is_not_reachable(fake_server, raw):
    fake_server.reply("/api/tags", raw=raw, method="GET")
    assert _backend(fake_server.url).reachable() is False


def test_b10_tags_valid_body_is_reachable(fake_server):
    fake_server.reply("/api/tags", {"models": [{"name": "m"}]}, method="GET")
    backend = _backend(fake_server.url)
    assert backend.reachable() is True
    assert backend.health_fields()["reachable"] is True


@pytest.mark.parametrize("status", [200, 500])
def test_b10_chat_error_member_raises_sanitised_message(fake_server, status):
    dirty = "\x1b[31mbad\x1b[0m\x07 model " + "x" * 500
    fake_server.reply(
        "/api/chat",
        {"error": dirty, "message": {"role": "assistant", "content": "ignored"}},
        status=status,
        method="POST",
    )
    with pytest.raises(LLMError) as info:
        _backend(fake_server.url).chat([Message("user", "q")])
    text = str(info.value)
    prefix = "the model server reported an error: "
    assert text.startswith(prefix)
    reported = text[len(prefix) :]
    assert reported == ("bad model " + "x" * 500)[:200]
    assert "\x1b" not in text and "\x07" not in text


@pytest.mark.parametrize(
    "body",
    [{"message": {"content": None}}, {"message": "hi"}, {"response": "hi"}, []],
    ids=["content-null", "message-str", "generate-shape", "top-list"],
)
def test_b10_chat_invalid_shape_is_a_model_error(fake_server, body):
    fake_server.reply("/api/chat", body, method="POST")
    with pytest.raises(LLMError):
        _backend(fake_server.url).chat([Message("user", "q")])


def test_b10_ollama_sends_no_authorization_header(fake_server):
    fake_server.reply("/api/chat", {"message": {"role": "assistant", "content": "hi"}})
    cfg = LLMConfig(endpoint=fake_server.url, model="m", timeout_s=5, token="tok-" + "abc123")
    OllamaBackend(cfg).chat([Message("user", "q")])
    assert "authorization" not in fake_server.requests[0].headers


# --- AT-21: HTTP 404/500 with a non-JSON body ----------------------------------------------

HTML_ERROR = b"<!DOCTYPE html><html><body><h1>Not here</h1>\x1b[2J</body></html>"


@pytest.mark.parametrize("status", [404, 500])
@pytest.mark.parametrize(
    "raw", [HTML_ERROR, b"Internal Server Error", b""], ids=["html", "text", "empty"]
)
def test_at21_ollama_ask_non_2xx_non_json_is_exit_1(
    cli, capsys, gi_config, fake_server, status, raw
):
    gi_config(backend="ollama", endpoint=fake_server.url, model="m", timeout_s=5)
    fake_server.reply("/api/chat", raw=raw, status=status, method="POST")
    code = cli("ask", "q")
    out, err = capsys.readouterr()
    assert code == 1
    assert out == ""
    assert f"the model server answered HTTP {status}" in err
    assert "\x1b" not in err and "Not here" not in err


@pytest.mark.parametrize("status", [404, 500])
def test_at21_ollama_non_2xx_with_a_valid_body_is_still_an_error(fake_server, status):
    fake_server.reply(
        "/api/chat", {"message": {"role": "assistant", "content": "hi"}}, status=status
    )
    fake_server.reply("/api/tags", {"models": [{"name": "m"}]}, status=status)
    with pytest.raises(LLMError, match=f"the model server answered HTTP {status}"):
        _backend(fake_server.url).chat([Message("user", "q")])
    assert _backend(fake_server.url).reachable() is False
