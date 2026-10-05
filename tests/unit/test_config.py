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

"""SPEC-0001 v1.2.0 B7, B14, B15: configuration keys, @gateway and the API token (AT-13, AT-14,
AT-18 token destination, AT-19 token_file location)."""

from __future__ import annotations

import builtins
import io
import os
import pathlib

import pytest

from gi_ai import config

GW_TEXT = "cannot find the default gateway for llm.endpoint"
# A distinctive fake credential; it must never appear in an error message.
LEAK_MARK = "tok-" + "ĜLEAK-file-9d1c"
ENV_VALUES = ("env-value-1", "env-value-2", "env-value-3")
# SPEC-0001 1.2.0 B15: a token_file must live inside ~/.config/gi-ai/.
TILDE_PATH = "~/.config/gi-ai/tilde.token"
NOT_A_KEY = "abc"

ROUTE_HEADER = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT"


def route_line(dest, gw, flags, iface="eth0", mask="00000000"):
    return f"{iface}\t{dest}\t{gw}\t{flags}\t0\t0\t0\t{mask}\t0\t0\t0"


def write_routes(path, *lines, header=True):
    rows = ([ROUTE_HEADER] if header else []) + list(lines)
    path.write_text("\n".join(rows) + "\n", encoding="ascii")
    return path


@pytest.fixture
def route_file(tmp_path, monkeypatch):
    """A WSL-like route table: default route via 192.168.0.1 (0100A8C0)."""
    path = write_routes(
        tmp_path / "route",
        route_line("0000A8C0", "00000000", "0001", mask="00FFFFFF"),
        route_line("00000000", "0100A8C0", "0003"),
    )
    monkeypatch.setattr(config, "ROUTE_FILE", path, raising=False)
    return path


def _load_error(match):
    with pytest.raises(config.ConfigError, match=match) as info:
        config.load()
    return str(info.value)


# --- AT-13: @gateway ---------------------------------------------------------------------


def test_at13_default_gateway_from_fixture_route_table(route_file):
    assert config.ROUTE_FILE == route_file
    assert config.default_gateway(route_file) == "192.168.0.1"


def test_at13_skips_header_and_lines_without_gateway_flag(tmp_path):
    path = write_routes(
        tmp_path / "route",
        route_line("00000000", "0200A8C0", "0001"),  # default destination, but no 0x2 flag
        route_line("0000A8C0", "0300A8C0", "0003", mask="00FFFFFF"),  # gateway, not default
        route_line("00000000", "0100A8C0", "0003"),
        route_line("00000000", "0400A8C0", "0003"),  # later default routes are ignored
    )
    assert config.default_gateway(path) == "192.168.0.1"


@pytest.mark.parametrize(
    "gw,flags,expected",
    [
        ("0100007F", "0003", "127.0.0.1"),
        ("01E01DAC", "0003", "172.29.224.1"),
        ("FEFFFFFF", "0002", "255.255.255.254"),
        ("0100a8c0", "0007", "192.168.0.1"),
    ],
)
def test_at13_little_endian_conversion(tmp_path, gw, flags, expected):
    path = write_routes(tmp_path / "route", route_line("00000000", gw, flags))
    assert config.default_gateway(path) == expected


@pytest.mark.parametrize(
    "lines",
    [
        [],
        [route_line("0000A8C0", "00000000", "0001", mask="00FFFFFF")],
        [route_line("00000000", "0100A8C0", "0001")],
        [route_line("00000000", "0100A8C0", "0005")],
    ],
    ids=["header-only", "no-default", "default-without-gateway-flag", "flags-0x5"],
)
def test_at13_no_default_route(tmp_path, lines):
    path = write_routes(tmp_path / "route", *lines)
    with pytest.raises(config.ConfigError, match=GW_TEXT):
        config.default_gateway(path)


@pytest.mark.parametrize(
    "content",
    ["", "\x00\x01garbage", "eth0\t00000000\tZZZZ\t0003\n", "eth0 00000000\n", "Ĝ" * 100],
    ids=["empty", "binary", "bad-hex", "short-line", "unicode"],
)
def test_at13_malformed_route_table(tmp_path, content):
    path = tmp_path / "route"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(config.ConfigError, match=GW_TEXT):
        config.default_gateway(path)


def test_at13_unreadable_route_table(tmp_path):
    with pytest.raises(config.ConfigError, match=GW_TEXT):
        config.default_gateway(tmp_path / "missing")
    with pytest.raises(config.ConfigError, match=GW_TEXT):
        config.default_gateway(tmp_path)  # a directory


def test_at13_load_replaces_gateway(gi_config, route_file):
    gi_config(backend="lmstudio", endpoint="http://@gateway:1234", allow_remote=True)
    assert config.load().llm.endpoint == "http://192.168.0.1:1234"


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("http://@gateway", "http://192.168.0.1"),
        ("https://@gateway:8443", "https://192.168.0.1:8443"),
        ("http://@gateway:1234/", "http://192.168.0.1:1234"),
    ],
)
def test_at13_gateway_forms(gi_config, route_file, endpoint, expected):
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=True)
    assert config.load().llm.endpoint.rstrip("/") == expected


def test_at13_gateway_is_resolved_every_run(gi_config, route_file):
    gi_config(backend="lmstudio", endpoint="http://@gateway:1234", allow_remote=True)
    assert config.load().llm.endpoint == "http://192.168.0.1:1234"
    write_routes(route_file, route_line("00000000", "01E01DAC", "0003"))
    assert config.load().llm.endpoint == "http://172.29.224.1:1234"


def test_at13_no_default_route_is_exit_2(cli, capsys, gi_config, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROUTE_FILE", write_routes(tmp_path / "route"), raising=False)
    gi_config(backend="lmstudio", endpoint="http://@gateway:1234", allow_remote=True)
    _load_error(GW_TEXT)
    for argv in (("health",), ("ask", "q"), ("selfcheck",)):
        assert cli(*argv) == 2
        assert GW_TEXT in capsys.readouterr().err


def test_at13_unreadable_route_file_is_exit_2(cli, capsys, gi_config, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROUTE_FILE", tmp_path / "does-not-exist", raising=False)
    gi_config(backend="lmstudio", endpoint="http://@gateway:1234", allow_remote=True)
    assert cli("health") == 2
    assert GW_TEXT in capsys.readouterr().err


def test_at13_gateway_without_allow_remote_is_exit_2(cli, capsys, gi_config, route_file):
    gi_config(backend="lmstudio", endpoint="http://@gateway:1234")
    _load_error("not on this computer")
    assert cli("health") == 2
    assert "not on this computer" in capsys.readouterr().err


def test_at13_allow_remote_is_checked_before_resolution(gi_config, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROUTE_FILE", tmp_path / "does-not-exist", raising=False)
    gi_config(backend="lmstudio", endpoint="http://@gateway:1234", allow_remote=False)
    _load_error("not on this computer")


def test_at13_gateway_that_is_loopback_still_needs_allow_remote(gi_config, tmp_path, monkeypatch):
    path = write_routes(tmp_path / "route", route_line("00000000", "0100007F", "0003"))
    monkeypatch.setattr(config, "ROUTE_FILE", path, raising=False)
    gi_config(backend="lmstudio", endpoint="http://@gateway:1234")
    _load_error("not on this computer")


def test_at13_route_file_not_read_without_gateway(gi_config, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROUTE_FILE", tmp_path / "does-not-exist", raising=False)
    gi_config(backend="lmstudio", endpoint="http://127.0.0.1:1234")
    assert config.load().llm.endpoint == "http://127.0.0.1:1234"


# --- AT-14: token from env or file -------------------------------------------------------


@pytest.fixture
def token_file(gi_env):
    def make(content=f"  {LEAK_MARK}\n", mode=0o600, name="lm.token"):
        path = gi_env / ".config" / "gi-ai" / name
        path.write_text(content, encoding="utf-8")
        path.chmod(mode)
        return path

    return make


def test_at14_env_token(monkeypatch, gi_config):
    monkeypatch.setenv("GI_AI_LLM_TOKEN", ENV_VALUES[0])
    gi_config(backend="lmstudio", endpoint="http://127.0.0.1:1234")
    assert config.load().llm.token == ENV_VALUES[0]


def test_at14_no_token_by_default(gi_config):
    gi_config(backend="lmstudio", endpoint="http://127.0.0.1:1234")
    cfg = config.load()
    assert cfg.llm.token is None
    assert cfg.llm.token_file is None


def test_at14_token_from_file_is_stripped(gi_config, token_file):
    gi_config(backend="lmstudio", endpoint="http://127.0.0.1:1234", token_file=str(token_file()))
    assert config.load().llm.token == LEAK_MARK


@pytest.mark.parametrize("mode", [0o600, 0o400])
def test_at14_private_token_file_modes_accepted(gi_config, token_file, mode):
    gi_config(backend="lmstudio", token_file=str(token_file(mode=mode)))
    assert config.load().llm.token == LEAK_MARK


def test_at14_token_file_with_tilde(gi_config, token_file):
    token_file(name="tilde.token")
    gi_config(backend="lmstudio", token_file=TILDE_PATH)
    assert config.load().llm.token == LEAK_MARK


def test_at14_env_wins_over_file(monkeypatch, gi_config, token_file):
    monkeypatch.setenv("GI_AI_LLM_TOKEN", ENV_VALUES[1])
    gi_config(backend="lmstudio", token_file=str(token_file()))
    assert config.load().llm.token == ENV_VALUES[1]


def test_at14_env_wins_and_file_is_not_read(monkeypatch, gi_config, token_file, gi_env):
    # with the env var set, even a bad (0644) or missing file does not matter
    monkeypatch.setenv("GI_AI_LLM_TOKEN", ENV_VALUES[2])
    gi_config(backend="lmstudio", token_file=str(token_file(mode=0o644)))
    assert config.load().llm.token == ENV_VALUES[2]
    gi_config(backend="lmstudio", token_file=str(gi_env / ".config" / "gi-ai" / "missing.token"))
    assert config.load().llm.token == ENV_VALUES[2]


def test_at14_empty_env_falls_back_to_file(monkeypatch, gi_config, token_file):
    monkeypatch.setenv("GI_AI_LLM_TOKEN", "")
    gi_config(backend="lmstudio", token_file=str(token_file()))
    assert config.load().llm.token == LEAK_MARK


def _bad_token_file(cli, capsys, gi_config, path):
    gi_config(backend="lmstudio", token_file=str(path))
    with pytest.raises(config.ConfigError) as info:
        config.load()
    text = str(info.value)
    assert "llm.token_file" in text
    assert LEAK_MARK not in text and LEAK_MARK not in repr(info.value)
    assert cli("health") == 2
    out, err = capsys.readouterr()
    assert LEAK_MARK not in out and LEAK_MARK not in err
    return text


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o620, 0o602, 0o660, 0o666], ids=oct)
def test_at14_token_file_with_group_or_other_bits(cli, capsys, gi_config, token_file, mode):
    _bad_token_file(cli, capsys, gi_config, token_file(mode=mode))


def test_at14_token_file_mode_0700_is_fine(gi_config, token_file):
    # 0700 has no group/other bits; executable is odd but private
    gi_config(backend="lmstudio", token_file=str(token_file(mode=0o700)))
    assert config.load().llm.token == LEAK_MARK


def test_at14_token_file_missing(cli, capsys, gi_config, gi_env):
    _bad_token_file(cli, capsys, gi_config, gi_env / ".config" / "gi-ai" / "missing.token")


@pytest.mark.parametrize("content", ["", "   \n\t\n"], ids=["empty", "whitespace"])
def test_at14_token_file_empty(cli, capsys, gi_config, token_file, content):
    _bad_token_file(cli, capsys, gi_config, token_file(content=content))


def test_at14_token_file_symlink_refused(cli, capsys, gi_config, token_file, gi_env):
    target = token_file()
    link = gi_env / ".config" / "gi-ai" / "link.token"
    link.symlink_to(target)
    _bad_token_file(cli, capsys, gi_config, link)


def test_at14_token_file_is_a_directory(cli, capsys, gi_config, gi_env):
    d = gi_env / ".config" / "gi-ai" / "token-dir"
    d.mkdir(mode=0o700)
    _bad_token_file(cli, capsys, gi_config, d)


def test_at14_token_file_not_owned_by_user(cli, capsys, monkeypatch, gi_config, token_file):
    path = token_file()
    real = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: real + 1)
    _bad_token_file(cli, capsys, gi_config, path)


def test_at14_token_is_not_a_config_key(gi_config):
    gi_config(backend="lmstudio", token=NOT_A_KEY)
    _load_error("unknown keys")


def test_at14_token_key_in_any_section_is_refused(gi_config):
    gi_config(_extra_sections={"limits": {"token": NOT_A_KEY}}, backend="lmstudio")
    _load_error("unknown keys")


# --- B7: keys, types and ranges ----------------------------------------------------------


def test_b7_defaults(gi_env):
    llm = config.load().llm
    assert llm.backend == "echo"  # gi_env writes the echo backend
    assert llm.reasoning == "off"
    assert llm.context_length is None
    assert llm.max_output_tokens is None
    assert llm.temperature is None
    assert llm.token_file is None
    assert llm.token is None
    assert config.LLMConfig().backend == "ollama"


@pytest.mark.parametrize("backend", ["ollama", "lmstudio", "echo"])
def test_b7_backends_accepted(gi_config, backend):
    gi_config(backend=backend)
    assert config.load().llm.backend == backend


@pytest.mark.parametrize("backend", ["openai", "LMStudio", "", "lm-studio"])
def test_b7_unknown_backend_refused(gi_config, backend):
    gi_config(backend=backend)
    _load_error("llm.backend")


@pytest.mark.parametrize("value", ["off", "low", "medium", "high", "on", "default"])
def test_b7_reasoning_values(gi_config, value):
    gi_config(backend="lmstudio", reasoning=value)
    assert config.load().llm.reasoning == value


@pytest.mark.parametrize("value", ["max", "", "OFF", "none", 1, True, ["off"]], ids=repr)
def test_b7_reasoning_refused(gi_config, value):
    gi_config(backend="lmstudio", reasoning=value)
    msg = _load_error("llm.reasoning")
    assert "unknown keys" not in msg


RANGES = {
    "timeout_s": (1, 3600),
    "context_length": (256, 1048576),
    "max_output_tokens": (1, 131072),
}


@pytest.mark.parametrize("key", sorted(RANGES))
def test_b7_int_range_bounds_accepted(gi_config, key):
    low, high = RANGES[key]
    for value in (low, high):
        gi_config(backend="lmstudio", **{key: value})
        assert getattr(config.load().llm, key) == value


@pytest.mark.parametrize("key", sorted(RANGES))
def test_b7_int_range_bounds_refused(gi_config, key):
    low, high = RANGES[key]
    for value in (low - 1, high + 1, -1):
        gi_config(backend="lmstudio", **{key: value})
        msg = _load_error(f"llm.{key}")
        assert "unknown keys" not in msg


@pytest.mark.parametrize("key", sorted(RANGES))
@pytest.mark.parametrize("value", [True, False, "512", 512.0, 512.5], ids=repr)
def test_b7_int_keys_need_real_ints(gi_config, key, value):
    gi_config(backend="lmstudio", **{key: value})
    msg = _load_error(f"llm.{key}")
    assert "unknown keys" not in msg


@pytest.mark.parametrize("value", [0, 1, 0.0, 0.7, 1.0])
def test_b7_temperature_accepted(gi_config, value):
    gi_config(backend="lmstudio", temperature=value)
    assert config.load().llm.temperature == value


@pytest.mark.parametrize(
    "value", [-0.1, 1.1, 2, True, False, "0.5", float("nan"), float("inf")], ids=repr
)
def test_b7_temperature_refused(gi_config, value):
    gi_config(backend="lmstudio", temperature=value)
    msg = _load_error("llm.temperature")
    assert "unknown keys" not in msg


@pytest.mark.parametrize("value", ["yes", 1, 0, "true"], ids=repr)
def test_b7_allow_remote_must_be_bool(gi_config, value):
    gi_config(backend="lmstudio", allow_remote=value)
    _load_error("llm.allow_remote")


@pytest.mark.parametrize("key", ["model", "token_file", "endpoint", "backend"])
def test_b7_string_keys_must_be_strings(gi_config, key):
    gi_config(**{key: 123})
    _load_error(f"llm.{key}")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:1234",
        "http://127.0.0.1:1234/",
        "https://localhost",
        "http://localhost:65535",
        "http://127.0.0.1:1",
        "http://[::1]:1234",
    ],
)
def test_b7_endpoint_forms_accepted(gi_config, endpoint):
    gi_config(backend="lmstudio", endpoint=endpoint)
    assert config.load().llm.endpoint.rstrip("/") == endpoint.rstrip("/")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://user@127.0.0.1:1234",
        "http://user:pw@127.0.0.1:1234",
        "http://@127.0.0.1:1234",
        "http://127.0.0.1:1234/api/v1",
        "http://127.0.0.1:1234/v1/",
        "http://127.0.0.1:1234?x=1",
        "http://127.0.0.1:1234#frag",
        "ftp://127.0.0.1:1234",
        "127.0.0.1:1234",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:port",
        "http://",
        "http://127.0.0.1 :1234",
        "http://127.0.0.1:1234\n",
        "http://[zzzz]:1234",
        "http://gateway@127.0.0.1",
        "http://@gatewayx:1234",
        "http://Ĝi.local:1234",
        "",
    ],
)
def test_b7_endpoint_strict_form(gi_config, endpoint):
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=True)
    msg = _load_error("llm.endpoint")
    assert "unknown keys" not in msg


def test_b7_endpoint_length_limit(gi_config):
    host_300 = "a" * (300 - len("http://"))
    gi_config(backend="lmstudio", endpoint=f"http://{host_300}", allow_remote=True)
    assert len(config.load().llm.endpoint.rstrip("/")) == 300
    gi_config(backend="lmstudio", endpoint=f"http://{host_300}a", allow_remote=True)
    _load_error("llm.endpoint")


def test_b7_remote_ip_endpoint_needs_allow_remote(gi_config):
    gi_config(backend="lmstudio", endpoint="http://172.29.224.1:1234")
    _load_error("not on this computer")
    gi_config(backend="lmstudio", endpoint="http://172.29.224.1:1234", allow_remote=True)
    assert config.load().llm.endpoint == "http://172.29.224.1:1234"


def test_b7_old_config_keeps_working(gi_config):
    gi_config(backend="ollama", endpoint="http://127.0.0.1:11434", model="qwen3:4b", timeout_s=120)
    llm = config.load().llm
    assert (llm.backend, llm.endpoint, llm.model, llm.timeout_s) == (
        "ollama",
        "http://127.0.0.1:11434",
        "qwen3:4b",
        120,
    )


def test_b7_malformed_toml_is_exit_2(cli, capsys, gi_config):
    gi_config.path.write_text('[llm\nbackend = "lmstudio\n', encoding="utf-8")
    assert cli("health") == 2
    assert "configuration error" in capsys.readouterr().err


def test_b7_config_precedence_with_new_keys(gi_config, tmp_path, monkeypatch):
    gi_config(backend="lmstudio", reasoning="low", temperature=0.3)
    env_cfg = tmp_path / "env.toml"
    env_cfg.write_text('[llm]\nreasoning = "high"\n', encoding="utf-8")
    monkeypatch.setenv("GI_AI_CONFIG", str(env_cfg))
    llm = config.load().llm
    assert (llm.reasoning, llm.temperature) == ("high", 0.3)


# --- AT-18: token destination rule (B15) ------------------------------------------------

DEST_TEXT = "refusing to send the API token to {host}: not a local or private address"


def _dest_message(host):
    return DEST_TEXT.format(host=host)


@pytest.fixture
def home_token(gi_env):
    """A valid token file at ~/.config/gi-ai/token (mode 0600)."""
    path = gi_env / ".config" / "gi-ai" / "token"
    path.write_text(f"{LEAK_MARK}\n", encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.fixture(params=["env", "file"])
def with_token(request, monkeypatch, home_token):
    """A token configured through the environment or through ~/.config/gi-ai/token."""
    if request.param == "env":
        monkeypatch.setenv("GI_AI_LLM_TOKEN", LEAK_MARK)
        return {}
    return {"token_file": str(home_token)}


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("http://127.0.0.1:1234", "http://127.0.0.1:1234"),
        ("http://localhost:1234", "http://localhost:1234"),
        ("http://@gateway:1234", "http://192.168.0.1:1234"),
        ("http://172.29.224.1:1234", "http://172.29.224.1:1234"),
        ("http://[fe80::1]:1234", "http://[fe80::1]:1234"),
        ("https://100.64.0.1:8443", "https://100.64.0.1:8443"),
        ("http://[::ffff:10.0.0.1]:1234", "http://[::ffff:10.0.0.1]:1234"),
    ],
)
@pytest.mark.parametrize("backend", ["lmstudio", "ollama"])
def test_at18_token_accepted_for_local_and_private_endpoints(
    gi_config, route_file, with_token, backend, endpoint, expected
):
    gi_config(backend=backend, endpoint=endpoint, allow_remote=True, **with_token)
    llm = config.load().llm
    assert llm.endpoint == expected
    assert llm.token == LEAK_MARK


@pytest.mark.parametrize(
    "endpoint,host",
    [("http://8.8.8.8:1234", "8.8.8.8"), ("http://example.org:1234", "example.org")],
)
@pytest.mark.parametrize("backend", ["lmstudio", "ollama", "echo"])
def test_at18_token_refused_for_public_or_dns_endpoints(
    gi_config, with_token, backend, endpoint, host
):
    gi_config(backend=backend, endpoint=endpoint, allow_remote=True, **with_token)
    with pytest.raises(config.ConfigError) as info:
        config.load()
    assert str(info.value) == _dest_message(host)
    assert LEAK_MARK not in str(info.value) and LEAK_MARK not in repr(info.value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://[2001:db8::1]:1234",
        "http://[2606:4700::1111]:1234",
        "http://[::ffff:8.8.8.8]:1234",
        "http://100.128.0.1:1234",
        "http://172.32.0.1:1234",
        "https://lmstudio.example:443",
        "http://windows-host:1234",
    ],
)
def test_at18_token_refused_for_other_non_private_hosts(gi_config, with_token, endpoint):
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=True, **with_token)
    with pytest.raises(config.ConfigError) as info:
        config.load()
    text = str(info.value)
    assert text.startswith("refusing to send the API token to ")
    assert text.endswith(": not a local or private address")
    assert LEAK_MARK not in text


def test_at18_rule_uses_the_resolved_gateway(gi_config, with_token, tmp_path, monkeypatch):
    path = write_routes(tmp_path / "route", route_line("00000000", "08080808", "0003"))
    monkeypatch.setattr(config, "ROUTE_FILE", path, raising=False)
    gi_config(backend="lmstudio", endpoint="http://@gateway:1234", allow_remote=True, **with_token)
    with pytest.raises(config.ConfigError) as info:
        config.load()
    assert str(info.value) == _dest_message("8.8.8.8")


@pytest.mark.parametrize(
    "endpoint", ["http://8.8.8.8:1234", "http://example.org:1234", "http://[2001:db8::1]:1234"]
)
@pytest.mark.parametrize("backend", ["lmstudio", "ollama"])
def test_at18_without_a_token_public_endpoints_still_load(gi_config, backend, endpoint):
    gi_config(backend=backend, endpoint=endpoint, allow_remote=True)
    llm = config.load().llm
    assert llm.endpoint == endpoint
    assert llm.token is None


def test_at18_empty_env_token_is_no_token(monkeypatch, gi_config):
    monkeypatch.setenv("GI_AI_LLM_TOKEN", "")
    gi_config(backend="lmstudio", endpoint="http://8.8.8.8:1234", allow_remote=True)
    assert config.load().llm.token is None


@pytest.mark.parametrize(
    "endpoint,host",
    [("http://8.8.8.8:1234", "8.8.8.8"), ("http://example.org:1234", "example.org")],
)
def test_at18_cli_exit_2_with_the_message(cli, capsys, gi_config, with_token, endpoint, host):
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=True, **with_token)
    for argv in (("ask", "x"), ("health",), ("--json", "health")):
        assert cli(*argv) == 2, argv
        out, err = capsys.readouterr()
        assert _dest_message(host) in err
        assert LEAK_MARK not in out and LEAK_MARK not in err


TOKEN_DESTINATIONS = [
    "http://127.0.0.1:1234",
    "http://127.0.0.2:1234",
    "http://localhost:1234",
    "http://LOCALHOST:1234",
    "http://[::1]:1234",
    "http://@gateway:1234",
    "http://172.29.224.1:1234",
    "http://100.64.0.1:1234",
    "http://100.127.255.254:1234",
    "http://10.1.2.3",
    "http://192.168.0.1:1234",
    "http://169.254.10.20:1234",
    "http://[fe80::1]:1234",
    "https://[fd12:3456::1]:443",
    "http://[::ffff:10.0.0.1]:1234",
    "http://[::ffff:127.0.0.1]:1234",
    "http://[::ffff:8.8.8.8]:1234",
    "http://8.8.8.8:1234",
    "http://172.32.0.1:1234",
    "http://100.128.0.1:1234",
    "http://192.0.2.1:1234",
    "http://[2001:db8::1]:1234",
    "http://[2606:4700::1111]:1234",
    "http://example.org:1234",
    "http://lmstudio.example:1234",
    "http://windows-host:1234",
]


@pytest.mark.parametrize("endpoint", TOKEN_DESTINATIONS)
def test_at18_token_destination_ok_agrees_with_endpoint_privacy(route_file, endpoint):
    # B15: "the same address classes that pass model-endpoint-private" -> no drift, ever
    ok, host = config.token_destination_ok(endpoint)
    assert isinstance(ok, bool) and isinstance(host, str)
    assert ok is config.endpoint_privacy(endpoint, True)[0]


@pytest.mark.parametrize(
    "endpoint,ok",
    [
        ("http://[::ffff:8.8.8.8]:1234", False),
        ("http://[::ffff:10.0.0.1]:1234", True),
        ("http://8.8.8.8:1234", False),
        ("http://example.org:1234", False),
        ("http://127.0.0.1:1234", True),
        ("http://[fe80::1]:1234", True),
    ],
)
def test_at18_token_destination_ok_explicit(endpoint, ok):
    assert config.token_destination_ok(endpoint)[0] is ok


@pytest.mark.parametrize(
    "endpoint,host",
    [("http://8.8.8.8:1234", "8.8.8.8"), ("http://example.org:1234", "example.org")],
)
def test_at18_token_destination_ok_names_the_host(endpoint, host):
    assert config.token_destination_ok(endpoint) == (False, host)


# --- AT-19: token_file location (B15) ---------------------------------------------------

LOCATION_TEXT = "llm.token_file must be inside ~/.config/gi-ai/"
OUTSIDE_PATH = "~/secret.txt"
UNICODE_PATH = "~/.config/gi-ai/Ĝi.token"
SUBDIR_PATH = "~/.config/gi-ai/tokens/lm.token"


@pytest.fixture
def opened(monkeypatch):
    """Record every path opened through os.open, builtins.open, io.open and Path.open."""
    seen: list[str] = []
    real_os_open = os.open
    real_open = builtins.open
    real_io_open = io.open
    real_path_open = pathlib.Path.open

    def note(path):
        if isinstance(path, (str, bytes, os.PathLike)):
            raw = os.fsdecode(os.fspath(path))
            seen.append(raw)
            seen.append(os.path.realpath(raw))

    def os_open(path, *args, **kwargs):
        note(path)
        return real_os_open(path, *args, **kwargs)

    def any_open(path, *args, **kwargs):
        note(path)
        return real_open(path, *args, **kwargs)

    def io_open(path, *args, **kwargs):
        note(path)
        return real_io_open(path, *args, **kwargs)

    def path_open(self, *args, **kwargs):
        note(self)
        return real_path_open(self, *args, **kwargs)

    monkeypatch.setattr(os, "open", os_open)
    monkeypatch.setattr(builtins, "open", any_open)
    monkeypatch.setattr(io, "open", io_open)
    monkeypatch.setattr(pathlib.Path, "open", path_open)
    return seen


def _private_file(path, content=f"{LEAK_MARK}\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def _refused_location(cli, capsys, gi_config, opened, raw, *never):
    """token_file `raw` is outside ~/.config/gi-ai/: exit 2, and none of `never` is opened."""
    gi_config(backend="lmstudio", endpoint="http://127.0.0.1:1234", token_file=raw)
    opened.clear()  # forget the test's own setup writes; only the code under test counts
    with pytest.raises(config.ConfigError) as info:
        config.load()
    assert LOCATION_TEXT in str(info.value)
    assert LEAK_MARK not in str(info.value)
    for argv in (("ask", "x"), ("health",)):
        assert cli(*argv) == 2, argv
        out, err = capsys.readouterr()
        assert LOCATION_TEXT in err
        assert LEAK_MARK not in out and LEAK_MARK not in err
    forbidden = set()
    for path in never:
        forbidden |= {str(path), os.path.realpath(path)}
    touched = forbidden & set(opened)
    assert not touched, f"token file opened: {sorted(touched)}"


def test_at19_token_file_in_home_is_refused(cli, capsys, gi_config, gi_env, opened):
    target = _private_file(gi_env / "secret.txt")
    _refused_location(cli, capsys, gi_config, opened, OUTSIDE_PATH, target)


def test_at19_dotdot_out_of_the_directory_is_refused(cli, capsys, gi_config, gi_env, opened):
    target = _private_file(gi_env / ".config" / "x")
    _refused_location(cli, capsys, gi_config, opened, "~/.config/gi-ai/../x", target)


def test_at19_absolute_dotdot_out_of_the_directory_is_refused(
    cli, capsys, gi_config, gi_env, opened
):
    target = _private_file(gi_env / ".config" / "x")
    raw = str(gi_env / ".config" / "gi-ai" / ".." / "x")
    _refused_location(cli, capsys, gi_config, opened, raw, target)


def test_at19_relative_path_is_refused(cli, capsys, gi_config, tmp_path, monkeypatch, opened):
    workdir = tmp_path / "work"
    target = _private_file(workdir / "rel.token")
    monkeypatch.chdir(workdir)
    _refused_location(cli, capsys, gi_config, opened, "rel.token", target)


def test_at19_symlink_outside_pointing_into_the_directory_is_refused(
    cli, capsys, gi_config, gi_env, opened
):
    inside = _private_file(gi_env / ".config" / "gi-ai" / "token")
    link = gi_env / "link.token"
    link.symlink_to(inside)
    _refused_location(cli, capsys, gi_config, opened, str(link), link, inside)


def test_at19_symlinked_subdirectory_pointing_out_is_refused(
    cli, capsys, gi_config, gi_env, tmp_path, opened
):
    outside_dir = tmp_path / "outside"
    target = _private_file(outside_dir / "token")
    outside_dir.chmod(0o700)
    (gi_env / ".config" / "gi-ai" / "sub").symlink_to(outside_dir, target_is_directory=True)
    raw = "~/.config/gi-ai/sub/token"
    _refused_location(
        cli, capsys, gi_config, opened, raw, gi_env / ".config" / "gi-ai" / "sub" / "token", target
    )


@pytest.mark.parametrize(
    "raw",
    ["~/.config/gi-ai-evil/token", "~/.config/gi-ai", "~/.config/gi-ai/", "/etc/gi-ai/token"],
    ids=["prefix-sibling", "the-directory", "the-directory-slash", "system-dir"],
)
def test_at19_other_locations_are_refused(cli, capsys, gi_config, gi_env, opened, raw):
    if "evil" in raw:
        _private_file(gi_env / ".config" / "gi-ai-evil" / "token")
    expanded = os.path.expanduser(raw)
    _refused_location(cli, capsys, gi_config, opened, raw, expanded)


def test_at19_rule_holds_even_when_the_env_token_is_set(
    cli, capsys, monkeypatch, gi_config, gi_env, opened
):
    monkeypatch.setenv("GI_AI_LLM_TOKEN", ENV_VALUES[0])
    target = _private_file(gi_env / "secret.txt")
    gi_config(backend="lmstudio", endpoint="http://127.0.0.1:1234", token_file=OUTSIDE_PATH)
    opened.clear()  # forget the test's own setup writes; only the code under test counts
    with pytest.raises(config.ConfigError, match=LOCATION_TEXT):
        config.load()
    assert cli("health") == 2
    out, err = capsys.readouterr()
    assert LOCATION_TEXT in err
    assert ENV_VALUES[0] not in out and ENV_VALUES[0] not in err
    assert str(target) not in opened


@pytest.mark.parametrize(
    "raw",
    ["~/.config/gi-ai/token", "{home}/.config/gi-ai/token", "~/.config/gi-ai/./token"],
    ids=["tilde", "absolute", "dot"],
)
def test_at19_token_file_inside_the_directory_is_used(gi_config, gi_env, raw):
    _private_file(gi_env / ".config" / "gi-ai" / "token")
    gi_config(backend="lmstudio", token_file=raw.format(home=gi_env))
    assert config.load().llm.token == LEAK_MARK


def test_at19_unicode_name_inside_the_directory_is_used(gi_config, gi_env):
    _private_file(gi_env / ".config" / "gi-ai" / "Ĝi.token")
    gi_config(backend="lmstudio", token_file=UNICODE_PATH)
    assert config.load().llm.token == LEAK_MARK


def test_at19_real_subdirectory_inside_is_fine(gi_config, gi_env):
    sub = gi_env / ".config" / "gi-ai" / "tokens"
    sub.mkdir(mode=0o700)
    _private_file(sub / "lm.token")
    gi_config(backend="lmstudio", token_file=SUBDIR_PATH)
    assert config.load().llm.token == LEAK_MARK


def test_at19_the_directory_is_literal_not_xdg(gi_env, tmp_path, monkeypatch, opened):
    # Q2 (plan): the spec names ~/.config/gi-ai/ literally; XDG_CONFIG_HOME does not move it.
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    elsewhere = _private_file(xdg / "gi-ai" / "token")
    user_cfg = xdg / "gi-ai" / "gi.toml"
    _write_llm(user_cfg, {"backend": "lmstudio", "token_file": str(elsewhere)})
    assert config.user_config_path() == user_cfg
    opened.clear()  # forget the test's own setup writes; only the code under test counts
    with pytest.raises(config.ConfigError, match=LOCATION_TEXT):
        config.load()
    assert str(elsewhere) not in opened

    _private_file(gi_env / ".config" / "gi-ai" / "token")
    _write_llm(user_cfg, {"backend": "lmstudio", "token_file": "~/.config/gi-ai/token"})
    assert config.load().llm.token == LEAK_MARK


def _write_llm(path, keys):
    lines = ["[llm]"] + [f'{k} = "{v}"' for k, v in keys.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
