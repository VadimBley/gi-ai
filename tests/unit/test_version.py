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

"""SPEC-0001 v1.3.0 B18 / AT-22: `gi --version` reads nothing and opens no socket."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from gi_ai import __version__

REPO = Path(__file__).resolve().parents[2]
LAUNCHER = REPO / "runtime" / "bin" / "gi-ai"

EXPECTED = (
    f"gi-ai {__version__}\n"
    "Copyright (C) 2026 Vadim Bley\n"
    "License AGPL-3.0-only: GNU Affero General Public License version 3 "
    "<https://www.gnu.org/licenses/agpl-3.0.html>\n"
    "This is free software: you are free to change and redistribute it.\n"
    "There is NO WARRANTY, to the extent permitted by law.\n"
    "Source: https://github.com/VadimBley/gi-ai\n"
)


@pytest.fixture
def hostile_env(gi_env, monkeypatch):
    """An unreadable config file and a token in the environment: --version must touch neither."""
    cfg = gi_env / ".config" / "gi-ai" / "gi.toml"
    cfg.write_text("this is [not toml", encoding="utf-8")
    cfg.chmod(0)
    monkeypatch.setenv("GI_AI_CONFIG", str(cfg))
    monkeypatch.setenv("GI_AI_LLM_TOKEN", "token-must-not-be-read")
    yield gi_env
    cfg.chmod(0o600)


def test_at22_version_prints_b18_lines_exactly(cli, hostile_env, monkeypatch, capsys):
    def no_socket(*args, **kwargs):
        raise AssertionError("gi --version opened a socket")

    def no_read(*args, **kwargs):
        raise AssertionError("gi --version read the config or the token")

    from gi_ai import cli as cli_module
    from gi_ai import config

    monkeypatch.setattr(socket, "socket", no_socket)
    monkeypatch.setattr(socket, "create_connection", no_socket)
    monkeypatch.setattr(cli_module, "load", no_read)
    monkeypatch.setattr(config, "load", no_read)
    monkeypatch.setattr(config, "_resolve_token", no_read)
    assert cli("--version") == 0
    out = capsys.readouterr()
    assert out.out == EXPECTED
    assert out.err == ""


def test_at22_version_equals_health_version(cli, gi_env, capsys):
    assert cli("--json", "health") == 0
    health_version = json.loads(capsys.readouterr().out)["version"]
    assert cli("--version") == 0
    assert capsys.readouterr().out.splitlines()[0] == f"gi-ai {health_version}"


@pytest.mark.parametrize(
    "argv",
    [
        ("--version", "health"),
        ("health", "--version"),
        ("--json", "--version"),
        ("--version", "ask", "hi"),
    ],
)
def test_at22_version_with_other_arguments_is_usage_error(cli, hostile_env, capsys, argv):
    assert cli(*argv) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "--version" in out.err


def test_version_after_terminator_is_a_question(cli, gi_env, capsys):
    assert cli("ask", "--", "--version") == 0
    assert "echo:" in capsys.readouterr().out


def test_version_is_listed_in_help(cli, gi_env, capsys):
    with pytest.raises(SystemExit) as exc:
        cli("--help")
    assert exc.value.code == 0
    assert "--version" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["gi", "gi-ai"])
def test_at22_launcher_version(hostile_env, subprocess_env, tmp_path, name):
    link = tmp_path / name
    link.symlink_to(LAUNCHER)
    env = dict(subprocess_env, GI_AI_CONFIG=str(hostile_env / ".config" / "gi-ai" / "gi.toml"))
    env.update(GI_AI_LLM_TOKEN=os.environ["GI_AI_LLM_TOKEN"])  # set by hostile_env
    # The launcher resolves its library relative to itself; point it at the source tree.
    r = subprocess.run(
        [sys.executable, "-I", "-c", _RUN_LAUNCHER, str(link)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == EXPECTED


_RUN_LAUNCHER = (
    "import runpy, sys; "
    f"sys.path.insert(0, {str(REPO / 'runtime')!r}); "
    "sys.argv = [sys.argv[1], '--version']; "
    "runpy.run_path(sys.argv[0], run_name='__main__')"
)
