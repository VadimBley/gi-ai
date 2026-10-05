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

"""SPEC-0001 v1.3.0 B19 / AT-23: licence text and per-file AGPL notices."""

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

NOTICE = (
    "Copyright (C) 2026 Vadim Bley "
    "This file is part of Ĝi (gi-ai). "
    "Ĝi is free software: you can redistribute it and/or modify it under the terms of the "
    "GNU Affero General Public License as published by the Free Software Foundation, version 3. "
    "Ĝi is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even "
    "the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU "
    "Affero General Public License for more details. "
    "You should have received a copy of the GNU Affero General Public License along with Ĝi. "
    "If not, see <https://www.gnu.org/licenses/>."
)

SOURCE_SUFFIXES = {".py", ".sh", ".toml", ".yml", ".yaml", ".1"}
SOURCE_NAMES = {"Makefile", "local.mk", "runtime/bin/gi-ai"}
# Not source: dev-only agent config (never exported), the read-only spec copy, test fixtures,
# and task templates, which are copied verbatim into the user's own task records.
EXEMPT_PREFIXES = (".claude/", "spec/", "tests/fixtures/", "runtime/gi_ai/assets/templates/")
COMMENT_PREFIX = re.compile(r'^(#|\.\\")\s?')


def _files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached"],
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        out = [p.relative_to(REPO).as_posix() for p in REPO.rglob("*") if p.is_file()]
    return sorted(p for p in out if (REPO / p).is_file())


def source_files() -> list[str]:
    return [
        p
        for p in _files()
        if not p.startswith(EXEMPT_PREFIXES)
        and (Path(p).suffix in SOURCE_SUFFIXES or p in SOURCE_NAMES)
    ]


def notice_of(text: str) -> str:
    """The leading comment block (after a shebang), comment markers removed, whitespace joined."""
    lines = text.splitlines()
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
    block = []
    for line in lines:
        if not COMMENT_PREFIX.match(line):
            break
        block.append(COMMENT_PREFIX.sub("", line, count=1))
    return " ".join(" ".join(block).split())


def test_source_file_list_is_not_empty():
    files = source_files()
    assert "runtime/gi_ai/cli.py" in files and "runtime/bin/gi-ai" in files
    assert "runtime/share/man/gi-ai.1" in files and "Makefile" in files


@pytest.mark.parametrize("rel", source_files())
def test_every_source_file_carries_the_agpl_notice(rel):
    text = (REPO / rel).read_text(encoding="utf-8")
    assert notice_of(text).startswith(NOTICE), f"{rel} lacks the AGPL-3.0-only notice"
    or_later = "any later" + " version"  # split so this file does not match itself
    assert or_later not in text.lower(), f"{rel}: AGPL-3.0-only, not 'or later'"


def test_notice_detection_rejects_a_bare_file():
    assert not notice_of("#!/bin/sh\necho hi\n").startswith(NOTICE)
    assert notice_of("#!/bin/sh\n# " + NOTICE + "\necho hi\n").startswith(NOTICE)


def test_copying_and_license_are_the_verbatim_agpl3_text():
    copying = (REPO / "COPYING").read_bytes()
    assert (REPO / "LICENSE").read_bytes() == copying
    text = copying.decode("utf-8")
    assert text.lstrip().startswith("GNU AFFERO GENERAL PUBLIC LICENSE\n")
    assert "Version 3, 19 November 2007" in text.splitlines()[1]
    assert text.rstrip().endswith("<https://www.gnu.org/licenses/>.")
    assert "Apache" not in text


def test_pyproject_declares_agpl_only():
    import tomllib

    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["license"] == "AGPL-3.0-only"
