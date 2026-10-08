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

"""SPEC-0001 v1.1 B6: selfcheck `model-endpoint-private` and `token-file-private` (AT-15)."""

from __future__ import annotations

import json

import pytest

from gi_ai import config

LEAK_MARK = "tok-" + "ĜLEAK-selfcheck-51e0"

PRIVACY_CASES = [
    # endpoint, allow_remote, ok, detail
    ("http://127.0.0.1:1234", False, True, "loopback"),
    ("http://127.0.0.2:1234", False, True, "loopback"),
    ("http://localhost:1234", False, True, "loopback"),
    ("http://[::1]:1234", False, True, "loopback"),
    ("http://127.0.0.1:1234", True, True, "loopback"),
    ("http://172.29.224.1:1234", True, True, "private network 172.29.224.1"),
    ("http://100.64.0.1:1234", True, True, "private network 100.64.0.1"),
    ("http://[fe80::1]:1234", True, True, "private network fe80::1"),
    ("http://10.1.2.3", True, True, "private network 10.1.2.3"),
    ("http://192.168.0.1:1234", True, True, "private network 192.168.0.1"),
    ("http://169.254.10.20:1234", True, True, "private network 169.254.10.20"),
    ("http://100.127.255.254:1234", True, True, "private network 100.127.255.254"),
    ("https://[fd12:3456::1]:443", True, True, "private network fd12:3456::1"),
    ("http://8.8.8.8:1234", True, False, "PUBLIC 8.8.8.8"),
    ("http://172.32.0.1:1234", True, False, "PUBLIC 172.32.0.1"),
    ("http://100.128.0.1:1234", True, False, "PUBLIC 100.128.0.1"),
    ("http://192.0.2.1:1234", True, False, "PUBLIC 192.0.2.1"),  # is_private, but not private
    ("http://[2001:db8::1]:1234", True, False, "PUBLIC 2001:db8::1"),
    ("http://[2606:4700::1111]:1234", True, False, "PUBLIC 2606:4700::1111"),
    ("http://lmstudio.example:1234", True, False, "unresolved name lmstudio.example"),
    ("http://windows-host:1234", True, False, "unresolved name windows-host"),
]


@pytest.mark.parametrize("endpoint,allow_remote,ok,detail", PRIVACY_CASES)
def test_at15_endpoint_privacy(endpoint, allow_remote, ok, detail):
    assert config.endpoint_privacy(endpoint, allow_remote) == (ok, detail)


@pytest.mark.parametrize(
    "endpoint,ip",
    [("http://172.29.224.1:1234", "172.29.224.1"), ("http://[fe80::1]:1234", "fe80::1")],
)
def test_at15_private_network_without_allow_remote(endpoint, ip):
    assert config.endpoint_privacy(endpoint, False) == (
        False,
        f"private network {ip} (llm.allow_remote is false)",
    )


def _selfcheck(cli, capsys):
    code = cli("--json", "selfcheck")
    out, err = capsys.readouterr()
    assert out, f"no JSON report (exit {code}): {err}"
    report = json.loads(out)
    checks = {c["id"]: c for c in report["checks"]}
    return code, report, checks


@pytest.mark.parametrize(
    "endpoint,allow_remote,ok,detail",
    [
        ("http://127.0.0.1:1234", False, True, "loopback"),
        ("http://localhost:1234", False, True, "loopback"),
        ("http://172.29.224.1:1234", True, True, "private network 172.29.224.1"),
        ("http://100.64.0.1:1234", True, True, "private network 100.64.0.1"),
        ("http://[fe80::1]:1234", True, True, "private network fe80::1"),
        ("http://8.8.8.8:1234", True, False, "PUBLIC 8.8.8.8"),
        ("http://lmstudio.example:1234", True, False, "unresolved name lmstudio.example"),
    ],
)
def test_at15_selfcheck_model_endpoint_private(
    cli, capsys, gi_config, endpoint, allow_remote, ok, detail
):
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=allow_remote)
    code, report, checks = _selfcheck(cli, capsys)
    assert "model-is-local" not in checks
    check = checks["model-endpoint-private"]
    assert check["ok"] is ok
    assert check["detail"] == detail
    assert report["ok"] is ok
    assert code == (0 if ok else 1)


def test_at15_selfcheck_text_output(cli, capsys, gi_config):
    gi_config(backend="lmstudio", endpoint="http://8.8.8.8:1234", allow_remote=True)
    assert cli("selfcheck") == 1
    out = capsys.readouterr().out
    assert "[FAIL] model-endpoint-private: PUBLIC 8.8.8.8" in out
    assert "model-is-local" not in out


def test_at15_selfcheck_uses_the_resolved_gateway(cli, capsys, gi_config, tmp_path, monkeypatch):
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t01E01DAC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n",
        encoding="ascii",
    )
    monkeypatch.setattr(config, "ROUTE_FILE", route, raising=False)
    gi_config(backend="lmstudio", endpoint="http://@gateway:1234", allow_remote=True)
    code, _, checks = _selfcheck(cli, capsys)
    assert checks["model-endpoint-private"] == {
        "id": "model-endpoint-private",
        "ok": True,
        "detail": "private network 172.29.224.1",
    }
    assert code == 0


def test_at15_echo_default_still_passes(cli, capsys, gi_env):
    cli("init-workspace")
    capsys.readouterr()
    code, _, checks = _selfcheck(cli, capsys)
    assert code == 0
    assert checks["model-endpoint-private"]["ok"] is True
    assert "token-file-private" not in checks
    assert "model-is-local" not in checks


def _token_file(home, mode):
    path = home / ".config" / "gi-ai" / "lm.token"  # SPEC-0001 1.2.0 B15: inside ~/.config/gi-ai/
    path.write_text(LEAK_MARK + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


def test_at15_token_file_private_fails_for_0640(cli, capsys, monkeypatch, gi_env, gi_config):
    # with the env token set the file is not read at load time, so selfcheck can report it
    monkeypatch.setenv("GI_AI_LLM_TOKEN", "from-the-environment")
    path = _token_file(gi_env, 0o640)
    gi_config(backend="lmstudio", endpoint="http://127.0.0.1:1234", token_file=str(path))
    code = cli("--json", "selfcheck")
    out, err = capsys.readouterr()
    assert out, f"no JSON report (exit {code}): {err}"
    checks = {c["id"]: c for c in json.loads(out)["checks"]}
    assert code == 1
    assert checks["token-file-private"]["ok"] is False
    assert "0o640" in checks["token-file-private"]["detail"]
    assert LEAK_MARK not in out
    assert cli("selfcheck") == 1
    text = capsys.readouterr().out
    assert "[FAIL] token-file-private" in text
    assert LEAK_MARK not in text


@pytest.mark.parametrize("with_env", [False, True])
def test_at15_token_file_private_passes_for_0600(
    cli, capsys, monkeypatch, gi_env, gi_config, with_env
):
    if with_env:
        monkeypatch.setenv("GI_AI_LLM_TOKEN", "from-the-environment")
    path = _token_file(gi_env, 0o600)
    gi_config(backend="lmstudio", endpoint="http://127.0.0.1:1234", token_file=str(path))
    code = cli("--json", "selfcheck")
    out, err = capsys.readouterr()
    assert out, f"no JSON report (exit {code}): {err}"
    checks = {c["id"]: c for c in json.loads(out)["checks"]}
    assert checks["token-file-private"]["ok"] is True
    assert "0o600" in checks["token-file-private"]["detail"]
    assert LEAK_MARK not in out
    assert code == 0


def test_at15_no_token_file_no_check(cli, capsys, gi_config):
    gi_config(backend="lmstudio", endpoint="http://127.0.0.1:1234")
    _, _, checks = _selfcheck(cli, capsys)
    assert "token-file-private" not in checks


# --- AT-24: cloud metadata addresses (SPEC-0001 1.4.0 B6, B7) --------------------------------


@pytest.mark.parametrize(
    "endpoint,ip",
    [
        ("http://169.254.169.254", "169.254.169.254"),
        ("http://169.254.170.2:80", "169.254.170.2"),
        ("http://[fd00:ec2::254]", "fd00:ec2::254"),
        ("http://[::ffff:169.254.169.254]", "169.254.169.254"),
        ("http://2852039166", "169.254.169.254"),
        ("http://169.254.169.254.", "169.254.169.254"),
    ],
)
@pytest.mark.parametrize("allow_remote", [True, False])
def test_at24_selfcheck_reports_metadata(cli, capsys, gi_config, endpoint, ip, allow_remote):
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=allow_remote)
    code, report, checks = _selfcheck(cli, capsys)
    assert checks["model-endpoint-private"] == {
        "id": "model-endpoint-private",
        "ok": False,
        "detail": f"METADATA {ip}",
    }
    assert report["ok"] is False
    assert code == 1
    assert cli("selfcheck") == 1
    assert f"[FAIL] model-endpoint-private: METADATA {ip}" in capsys.readouterr().out


def test_at24_selfcheck_reports_metadata_via_gateway(cli, capsys, gi_config, tmp_path, monkeypatch):
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\tFEA9FEA9\t0003\t0\t0\t0\t00000000\t0\t0\t0\n",
        encoding="ascii",
    )
    monkeypatch.setattr(config, "ROUTE_FILE", route, raising=False)
    gi_config(backend="lmstudio", endpoint="http://@gateway:1234", allow_remote=True)
    code, _, checks = _selfcheck(cli, capsys)
    assert checks["model-endpoint-private"]["detail"] == "METADATA 169.254.169.254"
    assert code == 1


def test_at24_selfcheck_with_metadata_does_not_read_the_token_file(cli, capsys, gi_env, gi_config):
    # 0640 would stop a load that reads the file; selfcheck must still report both checks.
    path = _token_file(gi_env, 0o640)
    gi_config(
        backend="lmstudio",
        endpoint="http://169.254.169.254",
        allow_remote=True,
        token_file=str(path),
    )
    code = cli("--json", "selfcheck")
    out, err = capsys.readouterr()
    assert out, f"no JSON report (exit {code}): {err}"
    checks = {c["id"]: c for c in json.loads(out)["checks"]}
    assert code == 1
    assert checks["model-endpoint-private"]["detail"] == "METADATA 169.254.169.254"
    assert checks["token-file-private"]["ok"] is False
    assert LEAK_MARK not in out + err


@pytest.mark.parametrize(
    "endpoint,detail",
    [
        ("http://169.254.1.1:1234", "private network 169.254.1.1"),
        ("http://169.254.169.253", "private network 169.254.169.253"),
        ("http://[fe80::1]:1234", "private network fe80::1"),
    ],
)
def test_at24_selfcheck_other_link_local_still_passes(cli, capsys, gi_config, endpoint, detail):
    gi_config(backend="lmstudio", endpoint=endpoint, allow_remote=True)
    code, _, checks = _selfcheck(cli, capsys)
    assert checks["model-endpoint-private"] == {
        "id": "model-endpoint-private",
        "ok": True,
        "detail": detail,
    }
    assert code == 0
