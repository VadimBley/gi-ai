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

"""SPEC-0001 v1.2.0 AT-17: no network call goes anywhere but the resolved endpoint;
AT-18: with a token, a public or DNS endpoint is refused before any socket is opened.

Every socket connect is recorded (and still performed). Proxy variables point at a decoy
listener, and a redirect points at a second server: neither may ever see a connection.
"""

from __future__ import annotations

import socket
import urllib.request

import pytest

from gi_ai import config

CHAT = "/api/v1/chat"
MODELS = "/api/v1/models"
LEAK_MARK = "tok-" + "GiLEAK-scope-0b7d"
PROXY_SET = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


@pytest.fixture
def connects(monkeypatch):
    """Record the peer address of every outgoing socket connect, then delegate."""
    seen: list[tuple] = []
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def connect(self, address):
        seen.append(tuple(address)[:2] if isinstance(address, tuple) else (address,))
        return original_connect(self, address)

    def connect_ex(self, address):
        seen.append(tuple(address)[:2] if isinstance(address, tuple) else (address,))
        return original_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    return seen


@pytest.fixture
def decoy_proxy(make_fake_server, monkeypatch):
    decoy = make_fake_server()
    decoy.reply(CHAT, {"output": [{"type": "message", "content": "FROM-THE-PROXY"}]})
    decoy.reply(MODELS, {"models": []})
    for name in PROXY_SET:
        monkeypatch.setenv(name, decoy.url)
    # urlopen() caches a global opener built from the environment of its first call; reset it
    # so this test sees the proxy variables exactly like a fresh `gi` process would.
    monkeypatch.setattr(urllib.request, "_opener", None, raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    return decoy


@pytest.fixture
def gateway_to_loopback(tmp_path, monkeypatch):
    """A route table whose default gateway is 127.0.0.1, so @gateway reaches the fake."""
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t0100007F\t0003\t0\t0\t0\t00000000\t0\t0\t0\n",
        encoding="ascii",
    )
    monkeypatch.setattr(config, "ROUTE_FILE", route, raising=False)
    return route


@pytest.mark.parametrize("form", ["literal", "gateway"])
def test_at17_only_the_resolved_endpoint_is_contacted(
    cli,
    capsys,
    monkeypatch,
    lmstudio_config,
    lm_replies,
    decoy_proxy,
    gateway_to_loopback,
    connects,
    form,
):
    server = lmstudio_config.server
    if form == "gateway":
        lmstudio_config(endpoint=f"http://@gateway:{server.port}", allow_remote=True)
    monkeypatch.setenv("GI_AI_LLM_TOKEN", LEAK_MARK)
    server.reply(CHAT, lm_replies.chat(lm_replies.message("from the endpoint")), method="POST")
    server.reply(MODELS, {"models": [lm_replies.model_item()]}, method="GET")

    assert cli("ask", "q") == 0
    out = capsys.readouterr().out
    assert out == "from the endpoint\n"
    assert cli("--json", "health") == 0
    assert f'"endpoint": "http://127.0.0.1:{server.port}"' in capsys.readouterr().out

    assert [r.path for r in server.requests] == [CHAT, MODELS]
    assert decoy_proxy.requests == []
    assert connects, "the patch must have seen the connections"
    assert set(connects) == {(server.host, server.port)}


def test_at17_ollama_ignores_proxies_too(
    cli, capsys, gi_config, fake_server, decoy_proxy, connects
):
    gi_config(backend="ollama", endpoint=fake_server.url, model="m", timeout_s=5)
    fake_server.reply("/api/chat", {"message": {"role": "assistant", "content": "hi"}})
    fake_server.reply("/api/tags", {"models": []})
    assert cli("ask", "q") == 0
    assert cli("health") == 0
    capsys.readouterr()
    assert decoy_proxy.requests == []
    assert set(connects) == {(fake_server.host, fake_server.port)}


def test_at17_echo_backend_makes_no_connection(cli, capsys, gi_env, decoy_proxy, connects):
    assert cli("ask", "q") == 0
    assert cli("health") == 0
    assert cli("selfcheck") in (0, 1)
    capsys.readouterr()
    assert connects == []
    assert decoy_proxy.requests == []


def test_at17_selfcheck_makes_no_connection(cli, capsys, lmstudio_config, connects):
    lmstudio_config(endpoint="http://lmstudio.example:1234", allow_remote=True)
    cli("selfcheck")
    capsys.readouterr()
    assert connects == []


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_at17_redirects_are_not_followed(
    cli, capsys, monkeypatch, lmstudio_config, lm_replies, make_fake_server, connects, status
):
    monkeypatch.setenv("GI_AI_LLM_TOKEN", LEAK_MARK)
    server = lmstudio_config.server
    elsewhere = make_fake_server()
    elsewhere.reply(CHAT, lm_replies.chat(lm_replies.message("REDIRECTED-ANSWER")))
    elsewhere.reply(MODELS, {"models": [lm_replies.model_item()]})
    for path in (CHAT, MODELS):
        server.reply(
            path, {"moved": True}, status=status, headers={"Location": elsewhere.url + path}
        )

    assert cli("ask", "q") == 1
    out, err = capsys.readouterr()
    assert "REDIRECTED-ANSWER" not in out
    assert LEAK_MARK not in err
    assert cli("--json", "health") == 1
    capsys.readouterr()

    assert [r.path for r in server.requests] == [CHAT, MODELS]
    assert elsewhere.requests == [], "the second server must see nothing (no token either)"
    assert set(connects) == {(server.host, server.port)}


# --- AT-18: token destination rule, socket level -------------------------------------------

DEST_TEXT = "refusing to send the API token to {host}: not a local or private address"
REFUSED = [("http://8.8.8.8:1234", "8.8.8.8"), ("http://example.org:1234", "example.org")]


@pytest.fixture
def no_sockets(monkeypatch):
    """Record and refuse every name lookup and outgoing connect: nothing leaves the process."""
    seen: list[tuple] = []

    def refuse(kind):
        def blocked(*args, **kwargs):
            # args[0] is the socket for the methods, the host for the module functions
            target = args[1] if kind in ("connect", "connect_ex") else args[0]
            seen.append((kind, target))
            raise OSError(f"test blocked {kind} to {target!r}")

        return blocked

    monkeypatch.setattr(socket.socket, "connect", refuse("connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", refuse("connect_ex"))
    monkeypatch.setattr(socket, "getaddrinfo", refuse("getaddrinfo"))
    monkeypatch.setattr(socket, "gethostbyname", refuse("gethostbyname"))
    monkeypatch.setattr(socket, "create_connection", refuse("create_connection"))
    return seen


@pytest.fixture(params=["env", "file"])
def token_source(request, monkeypatch, gi_env):
    """A configured token: GI_AI_LLM_TOKEN, or a valid ~/.config/gi-ai/token file."""
    if request.param == "env":
        monkeypatch.setenv("GI_AI_LLM_TOKEN", LEAK_MARK)
        return {}
    path = gi_env / ".config" / "gi-ai" / "token"
    path.write_text(f"{LEAK_MARK}\n", encoding="utf-8")
    path.chmod(0o600)
    return {"token_file": "~/.config/gi-ai/token"}


@pytest.mark.parametrize("endpoint,host", REFUSED)
@pytest.mark.parametrize("backend", ["lmstudio", "ollama"])
def test_at18_token_to_public_or_dns_endpoint_opens_no_socket(
    cli, capsys, gi_config, no_sockets, token_source, backend, endpoint, host
):
    gi_config(backend=backend, endpoint=endpoint, model="m", allow_remote=True, **token_source)
    message = DEST_TEXT.format(host=host)
    for argv in (("ask", "x"), ("health",), ("--json", "health"), ("ask", "--verbose", "x")):
        code = cli(*argv)
        out, err = capsys.readouterr()
        assert code == 2, (argv, err)
        assert message in err
        assert "Traceback" not in err
        assert LEAK_MARK not in out and LEAK_MARK not in err
    assert no_sockets == [], "no name lookup and no connect may happen"


@pytest.mark.parametrize("endpoint,host", REFUSED)
@pytest.mark.parametrize("backend", ["lmstudio", "ollama"])
def test_at18_without_a_token_the_request_is_still_attempted(
    cli, capsys, gi_config, no_sockets, backend, endpoint, host
):
    gi_config(backend=backend, endpoint=endpoint, model="m", timeout_s=5, allow_remote=True)
    assert cli("ask", "x") == 1
    assert "cannot reach the model server" in capsys.readouterr().err
    assert cli("health") == 1
    capsys.readouterr()
    targets = {str(target[0] if isinstance(target, tuple) else target) for _, target in no_sockets}
    assert host in targets, no_sockets


def test_at18_token_to_loopback_reaches_the_endpoint_only(
    cli, capsys, monkeypatch, lmstudio_config, lm_replies, connects
):
    monkeypatch.setenv("GI_AI_LLM_TOKEN", LEAK_MARK)
    server = lmstudio_config.server
    lmstudio_config(endpoint=f"http://localhost:{server.port}")
    server.reply(CHAT, lm_replies.chat(lm_replies.message("ok")), method="POST")
    assert cli("ask", "q") == 0
    assert capsys.readouterr().out == "ok\n"
    assert server.requests[0].headers["authorization"] == f"Bearer {LEAK_MARK}"
    assert {peer[0] for peer in connects} <= {"127.0.0.1", "::1"}
