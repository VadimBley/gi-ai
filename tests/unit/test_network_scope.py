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


# --- AT-24: cloud metadata addresses are always refused (SPEC-0001 1.4.0 B7) ----------------

INSIDE_PATH = "~/.config/gi-ai/token"
METADATA_TEXT = "refusing the endpoint {ip}: a cloud metadata address"

# endpoint, the address the message names
METADATA_ENDPOINTS = [
    ("http://169.254.169.254", "169.254.169.254"),
    ("http://169.254.169.254:80", "169.254.169.254"),
    ("http://169.254.170.2:80", "169.254.170.2"),
    ("https://169.254.170.2", "169.254.170.2"),
    ("http://[fd00:ec2::254]", "fd00:ec2::254"),
    ("http://[fd00:ec2::254]:1234", "fd00:ec2::254"),
]

# Spellings of the same addresses that libc or the kernel would also reach (bypass attempts).
METADATA_BYPASSES = [
    ("http://[::ffff:169.254.169.254]", "169.254.169.254"),
    ("http://[::ffff:a9fe:a9fe]:80", "169.254.169.254"),
    ("http://[::FFFF:169.254.170.2]", "169.254.170.2"),
    ("http://[64:ff9b::a9fe:a9fe]", "169.254.169.254"),
    ("http://[::a9fe:a9fe]", "169.254.169.254"),
    ("http://[FD00:EC2:0::254]", "fd00:ec2::254"),
    ("http://[fd00:ec2:0:0:0:0:0:254]", "fd00:ec2::254"),
    ("http://[fd00:ec2::0254]", "fd00:ec2::254"),
    ("http://2852039166", "169.254.169.254"),
    ("http://0xa9fea9fe", "169.254.169.254"),
    ("http://0XA9FEA9FE:80", "169.254.169.254"),
    ("http://0251.0376.0251.0376", "169.254.169.254"),
    ("http://0xa9.0xfe.0xa9.0xfe", "169.254.169.254"),
    ("http://169.254.43518", "169.254.169.254"),
    ("http://169.16689662", "169.254.169.254"),
    ("http://169.254.169.254.", "169.254.169.254"),
    ("http://169.254.169.254.:80", "169.254.169.254"),
    ("http://0251.254.169.254", "169.254.169.254"),
    ("http://169.254.170.002", "169.254.170.2"),
]


def _metadata_refused(cli, capsys, argv, ip):
    code = cli(*argv)
    out, err = capsys.readouterr()
    assert code == 2, (argv, out, err)
    assert METADATA_TEXT.format(ip=ip) in err
    assert LEAK_MARK not in out + err
    return err


@pytest.mark.parametrize("endpoint,ip", METADATA_ENDPOINTS + METADATA_BYPASSES)
@pytest.mark.parametrize("backend", ["lmstudio", "ollama"])
def test_at24_metadata_endpoint_refused_before_any_socket(
    cli, capsys, gi_config, no_sockets, backend, endpoint, ip
):
    gi_config(backend=backend, endpoint=endpoint, allow_remote=True)
    for argv in (("ask", "hello"), ("health",), ("task", "list")):
        _metadata_refused(cli, capsys, argv, ip)
    assert no_sockets == [], "no name lookup and no connect may happen"
    with pytest.raises(config.ConfigError, match="a cloud metadata address"):
        config.load()


@pytest.mark.parametrize("endpoint,ip", METADATA_ENDPOINTS + METADATA_BYPASSES[:3])
def test_at24_metadata_endpoint_refused_with_a_token(
    cli, capsys, gi_config, no_sockets, token_source, endpoint, ip
):
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=True, **token_source)
    for argv in (("ask", "hello"), ("health",)):
        err = _metadata_refused(cli, capsys, argv, ip)
        assert "API token" not in err, "the metadata refusal comes first"
    assert no_sockets == []


@pytest.mark.parametrize("endpoint,ip", METADATA_ENDPOINTS[:1] + METADATA_ENDPOINTS[4:5])
def test_at24_metadata_refused_even_without_allow_remote(
    cli, capsys, gi_config, no_sockets, endpoint, ip
):
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=False)
    err = _metadata_refused(cli, capsys, ("ask", "hello"), ip)
    assert "allow_remote" not in err
    assert no_sockets == []


def test_at24_metadata_check_runs_before_the_token_file_is_read(
    cli, capsys, gi_config, gi_env, no_sockets
):
    # A token file that would fail its own check (mode 0644): the metadata refusal must win,
    # proving the endpoint is checked before the token is resolved.
    path = gi_env / ".config" / "gi-ai" / "token"
    path.write_text(f"{LEAK_MARK}\n", encoding="utf-8")
    path.chmod(0o644)
    gi_config(
        backend="lmstudio",
        endpoint="http://169.254.169.254",
        allow_remote=True,
        token_file=INSIDE_PATH,
    )
    err = _metadata_refused(cli, capsys, ("health",), "169.254.169.254")
    assert "token_file" not in err
    assert no_sockets == []


def _route_to(tmp_path, monkeypatch, gw_hex):
    route = tmp_path / "route-metadata"
    route.write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        f"eth0\t00000000\t{gw_hex}\t0003\t0\t0\t0\t00000000\t0\t0\t0\n",
        encoding="ascii",
    )
    monkeypatch.setattr(config, "ROUTE_FILE", route, raising=False)


@pytest.mark.parametrize(
    "gw_hex,ip", [("FEA9FEA9", "169.254.169.254"), ("02AAFEA9", "169.254.170.2")]
)
def test_at24_gateway_resolving_to_metadata_is_refused(
    cli, capsys, gi_config, no_sockets, tmp_path, monkeypatch, gw_hex, ip
):
    _route_to(tmp_path, monkeypatch, gw_hex)
    gi_config(backend="lmstudio", endpoint="http://@gateway:1234", allow_remote=True)
    for argv in (("ask", "hello"), ("health",)):
        _metadata_refused(cli, capsys, argv, ip)
    assert no_sockets == []


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://169.254.1.1:1234",
        "http://169.254.169.253",
        "http://169.254.169.255",
        "http://169.254.170.3",
        "http://169.254.170.1",
        "http://[fe80::1]:1234",
        "http://[fd00:ec2::253]",
        "http://[fd00:ec2::1:254]",
    ],
)
def test_at24_other_link_local_addresses_still_load(gi_config, endpoint):
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=True)
    assert config.load().llm.endpoint == endpoint


def test_at24_other_link_local_with_a_token_is_still_allowed(gi_config, token_source):
    gi_config(
        backend="lmstudio", endpoint="http://169.254.1.1:1234", allow_remote=True, **token_source
    )
    assert config.load().llm.token == LEAK_MARK


@pytest.mark.parametrize("endpoint,ip", METADATA_ENDPOINTS + METADATA_BYPASSES)
def test_at24_privacy_and_token_rule_agree_on_metadata(endpoint, ip):
    for allow_remote in (False, True):
        assert config.endpoint_privacy(endpoint, allow_remote) == (False, f"METADATA {ip}")
    ok, _ = config.token_destination_ok(endpoint)
    assert ok is False


@pytest.mark.parametrize(
    "endpoint,detail",
    [
        ("http://127.0.0.1:1234", "loopback"),
        ("http://127.1:1234", "loopback"),
        ("http://0x7f000001", "loopback"),
        ("http://[::1]", "loopback"),
        ("http://localhost:1234", "loopback"),
        ("http://10.1:1234", "private network 10.0.0.1"),
        ("http://192.168.1.10.", "unresolved name 192.168.1.10."),
        ("http://127.0.0.1.:1234", "unresolved name 127.0.0.1."),
        ("http://169.254.1.1", "private network 169.254.1.1"),
        ("http://8.8.8.8", "PUBLIC 8.8.8.8"),
        ("http://134744072", "PUBLIC 8.8.8.8"),
        ("http://0x08.0x08.0x08.0x08", "PUBLIC 8.8.8.8"),
        ("http://[::ffff:8.8.8.8]", "PUBLIC 8.8.8.8"),
        ("http://example.com", "unresolved name example.com"),
        ("http://cafe", "unresolved name cafe"),
    ],
)
def test_at24_privacy_and_load_agree_on_every_host(gi_config, monkeypatch, endpoint, detail):
    """One parser for all checks: the selfcheck verdict and the token rule at load agree."""
    monkeypatch.setenv("GI_AI_LLM_TOKEN", LEAK_MARK)
    ok, got = config.endpoint_privacy(endpoint, allow_remote=True)
    assert got == detail
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=True)
    if ok:
        assert config.load().llm.token == LEAK_MARK
    else:
        with pytest.raises(config.ConfigError, match="refusing to send the API token"):
            config.load()


# The C library looks a host ending in "." up as a name (DNS, /etc/hosts), even when the rest
# is an address: only the metadata refusal reads it as the address, never a permissive check.
@pytest.mark.parametrize("endpoint", ["http://127.0.0.1.:1234", "http://127.1."])
def test_at24_trailing_dot_is_not_loopback(cli, capsys, gi_config, no_sockets, endpoint):
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=False)
    code = cli("ask", "hi")
    _, err = capsys.readouterr()
    assert code == 2
    assert "set llm.allow_remote = true" in err
    assert no_sockets == []


@pytest.mark.parametrize("endpoint", ["http://10.0.0.1.:1234", "http://127.0.0.1.:1234"])
def test_at24_trailing_dot_gets_no_token(
    cli, capsys, gi_config, no_sockets, token_source, endpoint
):
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=True, **token_source)
    code = cli("ask", "hi")
    _, err = capsys.readouterr()
    assert code == 2
    assert "refusing to send the API token" in err
    assert LEAK_MARK not in err
    assert no_sockets == []
