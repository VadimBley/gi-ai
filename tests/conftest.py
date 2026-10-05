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

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(REPO / "runtime"))


@pytest.fixture
def gi_env(tmp_path, monkeypatch):
    """Isolated user environment with the offline 'echo' model backend."""
    home = tmp_path / "home"
    (home / ".config" / "gi-ai").mkdir(parents=True)
    cfg = home / ".config" / "gi-ai" / "gi.toml"
    cfg.write_text('[llm]\nbackend = "echo"\n', encoding="utf-8")
    cfg.chmod(0o600)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.delenv("GI_AI_CONFIG", raising=False)
    return home


def run_cli(*argv):
    from gi_ai.cli import main

    return main(list(argv))


@pytest.fixture
def cli():
    return run_cli


@pytest.fixture
def subprocess_env(gi_env):
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL")}
    env.update(
        HOME=str(gi_env),
        XDG_CONFIG_HOME=str(gi_env / ".config"),
        XDG_DATA_HOME=str(gi_env / ".local" / "share"),
    )
    return env
