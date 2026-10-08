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
def lookups(monkeypatch):
    """Record every resolver lookup (host), then delegate to the real resolver."""
    seen: list[str] = []
    original = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        seen.append(host)
        return original(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
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
    lookups,
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
    assert lookups == [], "an IP literal endpoint is never looked up (SPEC-0001 1.5.0 B7)"


def test_at17_ollama_ignores_proxies_too(
    cli, capsys, gi_config, fake_server, decoy_proxy, connects, lookups
):
    gi_config(backend="ollama", endpoint=fake_server.url, model="m", timeout_s=5)
    fake_server.reply("/api/chat", {"message": {"role": "assistant", "content": "hi"}})
    fake_server.reply("/api/tags", {"models": []})
    assert cli("ask", "q") == 0
    assert cli("health") == 0
    capsys.readouterr()
    assert decoy_proxy.requests == []
    assert set(connects) == {(fake_server.host, fake_server.port)}
    assert lookups == []


def test_at17_echo_backend_makes_no_connection(cli, capsys, gi_env, decoy_proxy, connects, lookups):
    assert cli("ask", "q") == 0
    assert cli("health") == 0
    assert cli("selfcheck") in (0, 1)
    capsys.readouterr()
    assert connects == []
    assert lookups == []
    assert decoy_proxy.requests == []


def test_at17_selfcheck_makes_no_connection(cli, capsys, lmstudio_config, connects, lookups):
    lmstudio_config(endpoint="http://lmstudio.example:1234", allow_remote=True)
    cli("selfcheck")
    capsys.readouterr()
    assert connects == []
    assert lookups == [], "selfcheck does no lookup (B6, B7)"


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
    assert [kind for kind, _ in no_sockets if kind == "getaddrinfo"] == [], "B15 before any lookup"
    assert no_sockets == [], "no name lookup and no connect may happen"


@pytest.mark.parametrize("endpoint,host", REFUSED)
@pytest.mark.parametrize("backend", ["lmstudio", "ollama"])
def test_at18_without_a_token_the_request_is_still_attempted(
    cli, capsys, gi_config, no_sockets, backend, endpoint, host
):
    gi_config(backend=backend, endpoint=endpoint, model="m", timeout_s=5, allow_remote=True)
    is_name = host == "example.org"
    expected = f"cannot resolve {host}" if is_name else "cannot reach the model server"
    assert cli("ask", "x") == 1
    assert expected in capsys.readouterr().err
    assert cli("health") == 1
    capsys.readouterr()
    targets = {str(target[0] if isinstance(target, tuple) else target) for _, target in no_sockets}
    assert host in targets, no_sockets
    # SPEC-0001 1.5.0 B7: a name is looked up once per request, an IP literal never
    resolved = [target for kind, target in no_sockets if kind == "getaddrinfo"]
    assert resolved == ([host, host] if is_name else [])
    assert all(kind in ("getaddrinfo", "connect") for kind, _ in no_sockets), no_sockets


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


# --- AT-29: metadata addresses inside every IPv6 embedding, zone ids (SPEC-0001 1.5.0 B7) -----

METADATA_AT29 = [
    ("http://[64:ff9b:1:a9fe:a9:fe00::]", "169.254.169.254"),  # local-use NAT64, /48 layout
    ("http://[64:ff9b:1::a9fe:a9fe]", "169.254.169.254"),  # /96 layout
    ("http://[64:ff9b:1:a9:fe:a9fe::]", "169.254.169.254"),  # /56 layout
    ("http://[64:ff9b:1:0:a9:fea9:fe00::]", "169.254.169.254"),  # /64 layout
    ("http://[64:ff9b:1:a9fe:ffa9:fe00::]", "169.254.169.254"),  # /48, non-zero u-octet
    ("http://[::ffff:0:a9fe:a9fe]", "169.254.169.254"),  # IPv4-translated (SIIT)
    ("http://[fd00:ec2::254%25eth0]", "fd00:ec2::254"),  # zone id
    ("http://[::ffff:169.254.169.254%25lo]", "169.254.169.254"),
    ("http://[2002:a9fe:a9fe::1]", "169.254.169.254"),  # 6to4
    ("http://[2002:a9fe:aa02::]", "169.254.170.2"),
]
# More layouts and spellings of the same rule (bypass attempts beyond the spec's list).
METADATA_AT29_MORE = [
    ("http://[64:ff9b:1:ffff:a9:fe00::]:80", None),  # /48 with another first half: not metadata
    ("http://[64:ff9b:1:a9fe:a9:fe00:dead:beef]", "169.254.169.254"),  # /48, suffix bits set
    ("http://[64:ff9b:1:ffa9:fe:a9fe::]", "169.254.169.254"),  # /56, prefix bits 48-55 set
    ("http://[64:ff9b:1:1234:ffa9:fea9:fe00::]", "169.254.169.254"),  # /64, prefix and u-octet set
    ("http://[64:ff9b:1:ffff:ffff:ffff:a9fe:a9fe]", "169.254.169.254"),  # /96, any prefix bits
    ("http://[64:ff9b:1:a9fe:aa:200::]", "169.254.170.2"),  # /48 for 169.254.170.2
    ("http://[::ffff:0:169.254.170.2]", "169.254.170.2"),  # SIIT, dotted
    ("http://[2002:a9fe:a9fe:ffff:ffff:ffff:ffff:ffff]", "169.254.169.254"),
    ("http://[64:ff9b::a9fe:a9fe%25eth0]", "169.254.169.254"),  # well-known NAT64 + zone
    ("http://[::a9fe:a9fe%251]", "169.254.169.254"),  # IPv4-compatible + numeric zone
    ("http://[FD00:EC2::254%25ETH0]:1234", "fd00:ec2::254"),
]
NOT_METADATA_AT29 = ["http://[2002:c0a8:1::1]", "http://[64:ff9b:1::c0a8:1]"]


@pytest.mark.parametrize("endpoint,ip", METADATA_AT29)
@pytest.mark.parametrize("backend", ["lmstudio", "ollama"])
def test_at29_embedded_metadata_refused_before_any_socket(
    cli, capsys, gi_config, no_sockets, backend, endpoint, ip
):
    gi_config(backend=backend, endpoint=endpoint, allow_remote=True)
    for argv in (("ask", "hello"), ("health",), ("--json", "health"), ("task", "list")):
        _metadata_refused(cli, capsys, argv, ip)
    assert no_sockets == [], "no name lookup and no connect may happen"


@pytest.mark.parametrize("endpoint,ip", METADATA_AT29 + METADATA_AT29_MORE)
def test_at29_privacy_load_and_token_rule_agree(gi_config, endpoint, ip):
    if ip is None:
        assert config.endpoint_privacy(endpoint, True)[1].startswith("PUBLIC ")
        return
    for allow_remote in (False, True):
        assert config.endpoint_privacy(endpoint, allow_remote) == (False, f"METADATA {ip}")
    assert config.token_destination_ok(endpoint)[0] is False
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=True)
    with pytest.raises(config.ConfigError, match=f"refusing the endpoint {ip}: a cloud metadata"):
        config.load()


@pytest.mark.parametrize("endpoint,ip", METADATA_AT29[6:8])
def test_at29_selfcheck_reports_a_zone_id_metadata_endpoint(
    cli, capsys, gi_config, no_sockets, endpoint, ip
):
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=True)
    assert cli("selfcheck") == 1
    assert f"[FAIL] model-endpoint-private: METADATA {ip}" in capsys.readouterr().out
    assert no_sockets == []


@pytest.mark.parametrize("endpoint", NOT_METADATA_AT29)
def test_at29_other_embedded_addresses_are_not_metadata(gi_config, endpoint):
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=True)
    assert config.load().llm.endpoint == endpoint
    assert not config.endpoint_privacy(endpoint, True)[1].startswith("METADATA")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://[fe80::1%25eth0]:1234",
        "http://[::1%25lo]",
        "http://[fd00:ec2::253%25eth0]",
        "http://[2002:c0a8:1::1%25eth0]",
        "http://[fd00:ec2::254%eth0]",  # not URL-encoded: never a valid endpoint
        "http://[fd00:ec2::254%25]",
        "http://[fd00:ec2::254%25eth/0]",
    ],
)
def test_at29_zone_ids_are_only_parsed_for_the_metadata_refusal(
    cli, capsys, gi_config, no_sockets, endpoint
):
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=True)
    assert cli("ask", "hello") == 2
    assert "llm.endpoint must look like" in capsys.readouterr().err
    assert no_sockets == []


@pytest.mark.parametrize(
    "host,ip",
    [
        ("fd00:ec2::254%eth0", "fd00:ec2::254"),
        ("fd00:ec2::254%1", "fd00:ec2::254"),
        ("::ffff:169.254.169.254%lo", "169.254.169.254"),
        ("64:ff9b:1:a9fe:a9:fe00::", "169.254.169.254"),
        ("64:ff9b:1:a9fe:ffa9:fe00::", "169.254.169.254"),
        ("64:ff9b:1:a9:fe:a9fe::", "169.254.169.254"),
        ("64:ff9b:1:0:a9:fea9:fe00::", "169.254.169.254"),
        ("64:ff9b:1::a9fe:a9fe", "169.254.169.254"),
        ("::ffff:0:a9fe:a9fe", "169.254.169.254"),
        ("2002:a9fe:a9fe::1", "169.254.169.254"),
        ("2002:a9fe:aa02::", "169.254.170.2"),
        ("64:ff9b::a9fe:aa02", "169.254.170.2"),
        ("2002:c0a8:1::1", None),
        ("64:ff9b:1::c0a8:1", None),
        ("64:ff9b:2::a9fe:a9fe", None),  # outside 64:ff9b:1::/48
        ("2003:a9fe:a9fe::", None),
        ("fe80::1%eth0", None),
        ("::ffff:1:a9fe:a9fe", None),
    ],
)
def test_at29_metadata_address_table(host, ip):
    got = config.metadata_address(host)
    assert (None if got is None else str(got)) == ip


# --- AT-28: names are looked up once per request and checked at connect time (B7) -------------

NAME = "models.test"
LOCALHOST_TEXT = "refusing the endpoint {ip}: localhost does not resolve to loopback"


class FakeResolver:
    """The system resolver as a test double: answers[host] = [ip, ...]; records every lookup."""

    def __init__(self):
        self.answers: dict[str, list[str]] = {}
        self.fail: set[str] = set()
        self.calls: list[str] = []

    def getaddrinfo(self, host, port, family=0, type=0, proto=0, flags=0):
        self.calls.append(host)
        if host in self.fail:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        out = []
        for ip in self.answers.get(host, []):
            if ":" in ip:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, int(port), 0, 0)))
            else:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, int(port))))
        return out


@pytest.fixture
def resolver(monkeypatch):
    fake = FakeResolver()
    monkeypatch.setattr(socket, "getaddrinfo", fake.getaddrinfo)
    return fake


@pytest.fixture
def wire(monkeypatch):
    """Record every connect; carry chosen addresses to a real loopback port; fake peer names."""
    state = {"connects": [], "route": {}, "peer": None}
    original_connect = socket.socket.connect
    original_peer = socket.socket.getpeername

    def connect(self, address):
        state["connects"].append(tuple(address)[:2])
        target = state["route"].get(address[0])
        if target is not None:
            if self.family != socket.AF_INET:  # the test's loopback servers listen on IPv4
                raise OSError("test: no IPv6 route")
            return original_connect(self, target)
        if not str(address[0]).startswith("127."):
            raise OSError(f"test blocked connect to {address!r}")
        return original_connect(self, address)

    def getpeername(self):
        if state["peer"] is not None:
            return state["peer"]
        return original_peer(self)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "getpeername", getpeername)
    monkeypatch.setattr(socket, "create_connection", _never("create_connection"))
    return state


def _never(name):
    def fail(*args, **kwargs):
        raise AssertionError(f"{name} must not be called: it would look the name up again")

    return fail


@pytest.fixture
def name_server(lmstudio_config, lm_replies):
    """The fake LM Studio, configured as http://models.test:<port> with allow_remote."""
    server = lmstudio_config.server
    lmstudio_config(endpoint=f"http://{NAME}:{server.port}", allow_remote=True)
    server.reply(CHAT, lm_replies.chat(lm_replies.message("from the name")), method="POST")
    server.reply(MODELS, {"models": [lm_replies.model_item()]}, method="GET")
    return server


@pytest.mark.parametrize(
    "answer,ip",
    [
        (["169.254.169.254"], "169.254.169.254"),
        (["169.254.170.2"], "169.254.170.2"),
        (["fd00:ec2::254"], "fd00:ec2::254"),
        (["64:ff9b::a9fe:a9fe"], "169.254.169.254"),
        (["192.168.0.10", "169.254.169.254"], "169.254.169.254"),
        (["fd00:ec2::254%eth0"], "fd00:ec2::254"),
        (["::ffff:169.254.170.2"], "169.254.170.2"),
        (["2002:a9fe:a9fe::"], "169.254.169.254"),
        (["192.168.0.10", "fe80::1", "64:ff9b:1::a9fe:aa02"], "169.254.170.2"),
    ],
)
@pytest.mark.parametrize("backend", ["lmstudio", "ollama"])
def test_at28_name_resolving_to_metadata_is_refused_without_connect(
    cli, capsys, gi_config, resolver, wire, backend, answer, ip
):
    resolver.answers[NAME] = answer
    gi_config(backend=backend, endpoint=f"http://{NAME}:1234", model="m", allow_remote=True)
    for argv in (("ask", "hello"), ("health",), ("--json", "health")):
        resolver.calls.clear()
        code = cli(*argv)
        out, err = capsys.readouterr()
        assert code == 2, (argv, out, err)
        assert METADATA_TEXT.format(ip=ip) in err
        assert "Traceback" not in err
        assert resolver.calls == [NAME], "exactly one lookup"
    assert wire["connects"] == [], "no connection is opened"


def test_at28_name_connects_to_the_looked_up_address_with_the_name_as_host(
    cli, capsys, name_server, resolver, wire
):
    resolver.answers[NAME] = ["192.168.0.10"]
    wire["route"]["192.168.0.10"] = ("127.0.0.1", name_server.port)
    assert cli("ask", "q") == 0
    assert capsys.readouterr().out == "from the name\n"
    assert resolver.calls == [NAME]
    assert cli("health") == 0
    capsys.readouterr()
    assert resolver.calls == [NAME, NAME], "one lookup per request, no second lookup"
    assert wire["connects"] == [("192.168.0.10", name_server.port)] * 2
    assert [r.headers["host"] for r in name_server.requests] == [f"{NAME}:{name_server.port}"] * 2


def test_at28_addresses_are_tried_in_lookup_order(cli, capsys, name_server, resolver, wire):
    resolver.answers[NAME] = ["192.168.0.10", "10.0.0.7", "192.168.0.11"]
    wire["route"]["192.168.0.11"] = ("127.0.0.1", name_server.port)
    assert cli("ask", "q") == 0
    capsys.readouterr()
    port = name_server.port
    assert wire["connects"] == [("192.168.0.10", port), ("10.0.0.7", port), ("192.168.0.11", port)]
    assert resolver.calls == [NAME]


@pytest.mark.parametrize(
    "peer",
    [("169.254.169.254", 80), ("fd00:ec2::254", 80, 0, 0), ("::ffff:169.254.170.2", 80, 0, 0)],
)
def test_at28_peer_is_checked_again_before_anything_is_sent(
    cli, capsys, name_server, resolver, wire, peer
):
    resolver.answers[NAME] = ["192.168.0.10"]
    wire["route"]["192.168.0.10"] = ("127.0.0.1", name_server.port)
    wire["peer"] = peer
    ip = str(config.metadata_address(peer[0]))
    for argv in (("ask", "q"), ("health",)):
        code = cli(*argv)
        err = capsys.readouterr().err
        assert code == 2, err
        assert METADATA_TEXT.format(ip=ip) in err
    assert name_server.requests == [], "nothing is sent to a metadata peer"


def test_at28_peer_check_applies_to_ip_literals_too(cli, capsys, lmstudio_config, wire, resolver):
    wire["peer"] = ("169.254.169.254", 80)
    assert cli("ask", "q") == 2
    assert METADATA_TEXT.format(ip="169.254.169.254") in capsys.readouterr().err
    assert lmstudio_config.server.requests == []
    assert resolver.calls == []


@pytest.fixture
def tls_spy(monkeypatch):
    """Record every TLS wrap (server name) and stop there: there is no TLS server in the tests."""
    import ssl

    class Seen(list):
        """Server names in order; .contexts holds (check_hostname, verify_mode) per wrap."""

        def __init__(self):
            super().__init__()
            self.contexts: list[tuple] = []

    seen = Seen()

    def wrap_socket(self, sock, *args, server_hostname=None, **kwargs):
        seen.append(server_hostname)
        seen.contexts.append((self.check_hostname, self.verify_mode))
        raise ssl.SSLError("test: TLS stopped after the ClientHello would be sent")

    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap_socket)
    return seen


def test_at28_https_metadata_peer_gets_no_client_hello(
    cli, capsys, lmstudio_config, resolver, wire, tls_spy
):
    server = lmstudio_config.server
    lmstudio_config(endpoint=f"https://{NAME}:{server.port}", allow_remote=True)
    resolver.answers[NAME] = ["192.168.0.10"]
    wire["route"]["192.168.0.10"] = ("127.0.0.1", server.port)
    wire["peer"] = ("169.254.169.254", 443)
    assert cli("ask", "q") == 2
    assert METADATA_TEXT.format(ip="169.254.169.254") in capsys.readouterr().err
    assert tls_spy == [], "no TLS handshake with a metadata peer"
    assert server.requests == []


def test_at28_https_uses_the_configured_name_for_sni_and_certificate(
    cli, capsys, lmstudio_config, resolver, wire, tls_spy
):
    server = lmstudio_config.server
    lmstudio_config(endpoint=f"https://{NAME}:{server.port}", allow_remote=True)
    resolver.answers[NAME] = ["192.168.0.10"]
    wire["route"]["192.168.0.10"] = ("127.0.0.1", server.port)
    assert cli("ask", "q") == 1
    capsys.readouterr()
    import ssl

    assert tls_spy == [NAME]
    assert tls_spy.contexts == [(True, ssl.CERT_REQUIRED)], "certificate and name are checked"
    assert resolver.calls == [NAME]
    assert wire["connects"] == [("192.168.0.10", server.port)]


@pytest.mark.parametrize(
    "answer,message",
    [
        (["169.254.169.254"], METADATA_TEXT.format(ip="169.254.169.254")),
        (["127.0.0.1", "169.254.170.2"], METADATA_TEXT.format(ip="169.254.170.2")),
        (["192.168.0.10"], LOCALHOST_TEXT.format(ip="192.168.0.10")),
        (["127.0.0.1", "10.0.0.1"], LOCALHOST_TEXT.format(ip="10.0.0.1")),
        (["::1", "fe80::1"], LOCALHOST_TEXT.format(ip="fe80::1")),
    ],
)
def test_at28_localhost_must_resolve_to_loopback(
    cli, capsys, lmstudio_config, resolver, wire, answer, message
):
    server = lmstudio_config.server
    lmstudio_config(endpoint=f"http://localhost:{server.port}")
    resolver.answers["localhost"] = answer
    for argv in (("ask", "q"), ("health",)):
        code = cli(*argv)
        err = capsys.readouterr().err
        assert code == 2, err
        assert message in err
    assert wire["connects"] == []
    assert resolver.calls == ["localhost", "localhost"]


def test_at28_localhost_resolving_to_loopback_works(
    cli, capsys, lmstudio_config, lm_replies, resolver, wire
):
    server = lmstudio_config.server
    lmstudio_config(endpoint=f"http://localhost:{server.port}")
    server.reply(CHAT, lm_replies.chat(lm_replies.message("local")), method="POST")
    resolver.answers["localhost"] = ["127.0.0.1", "::1"]
    assert cli("ask", "q") == 0
    assert capsys.readouterr().out == "local\n"
    assert resolver.calls == ["localhost"]
    assert server.requests[0].headers["host"] == f"localhost:{server.port}"


def test_at28_localhost_peer_must_be_loopback(cli, capsys, lmstudio_config, resolver, wire):
    server = lmstudio_config.server
    lmstudio_config(endpoint=f"http://localhost:{server.port}")
    resolver.answers["localhost"] = ["127.0.0.1"]
    wire["peer"] = ("192.168.0.10", server.port)
    assert cli("ask", "q") == 2
    assert LOCALHOST_TEXT.format(ip="192.168.0.10") in capsys.readouterr().err
    assert server.requests == []


@pytest.mark.parametrize("failure", ["error", "empty"])
@pytest.mark.parametrize("backend", ["lmstudio", "ollama"])
def test_at28_failed_lookup_is_a_model_error(
    cli, capsys, gi_config, resolver, wire, backend, failure
):
    if failure == "error":
        resolver.fail.add(NAME)
    gi_config(backend=backend, endpoint=f"http://{NAME}:1234", model="m", allow_remote=True)
    assert cli("ask", "q") == 1
    assert f"gi ask: cannot resolve {NAME}" in capsys.readouterr().err
    assert cli("--json", "health") == 1
    assert '"status": "degraded"' in capsys.readouterr().out
    assert resolver.calls == [NAME, NAME]
    assert wire["connects"] == []


def test_at28_all_attempts_share_one_timeout(cli, capsys, gi_config, resolver, monkeypatch):
    """Three unreachable addresses, timeout_s = 2: about 2 s in total, not 6."""
    import time

    attempts: list[tuple[str, float]] = []

    def slow_connect(self, address):
        timeout = self.gettimeout()
        attempts.append((address[0], timeout))
        time.sleep(min(timeout or 0, 2.5))
        raise TimeoutError("timed out")

    monkeypatch.setattr(socket.socket, "connect", slow_connect)
    resolver.answers[NAME] = ["192.0.2.1", "192.0.2.2", "192.0.2.3"]
    gi_config(
        backend="lmstudio",
        endpoint=f"http://{NAME}:1234",
        model="m",
        timeout_s=2,
        allow_remote=True,
    )
    started = time.monotonic()
    assert cli("ask", "q") == 1
    assert time.monotonic() - started < 3
    capsys.readouterr()
    time.sleep(0.6)  # let the worker finish whatever attempt it is in
    assert attempts, "the looked-up addresses must have been tried"
    assert attempts[0][0] == "192.0.2.1"
    assert sum(t for _, t in attempts) <= 2.05, attempts
    assert resolver.calls == [NAME]


def test_at28_redirect_to_metadata_is_not_followed(
    cli, capsys, monkeypatch, lmstudio_config, resolver, wire
):
    monkeypatch.setenv("GI_AI_LLM_TOKEN", LEAK_MARK)
    server = lmstudio_config.server
    lmstudio_config(endpoint=f"http://localhost:{server.port}")
    resolver.answers["localhost"] = ["127.0.0.1"]
    for path in (CHAT, MODELS):
        server.reply(
            path, {"moved": True}, status=302, headers={"Location": "http://169.254.169.254/"}
        )
    assert cli("ask", "q") == 1
    out, err = capsys.readouterr()
    assert LEAK_MARK not in out + err
    assert cli("--json", "health") == 1
    assert '"status": "degraded"' in capsys.readouterr().out
    assert [r.path for r in server.requests] == [CHAT, MODELS]
    assert wire["connects"] == [("127.0.0.1", server.port)] * 2, "no second connect"
    assert resolver.calls == ["localhost", "localhost"]
