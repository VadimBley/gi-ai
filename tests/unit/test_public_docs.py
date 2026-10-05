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

"""Public README/SECURITY: right contacts, licence section, no dev-only paths."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ADVISORIES = "https://github.com/VadimBley/gi-ai/security/advisories/new"
FINGERPRINT = "D523 F5BF 268D FD9F CD08 2391 E1B6 1810 1C64 54A0"
LICENCE_PARAGRAPH = (
    "Ĝi is free software, licensed under the GNU Affero General Public License v3.0 only "
    "(AGPL-3.0-only). You may use, study, share and modify it, including commercially. "
    "If you distribute Ĝi or a modified version, or let others use a modified version over a "
    "network, you must make the source of your version available under the same licence. "
    "Commercial licences for using Ĝi without these obligations are available upon request: "
    "license@gi-ai.app."
)
DEV_ONLY = ("plans/", "CONTRIBUTING", ".claude", "CLAUDE.md", "REVIEW.md", "proposals/")


def read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def flat(text: str) -> str:
    return " ".join(text.split())


def test_readme_install_and_verification():
    text = read("README.md")
    assert "https://gi-ai.app/apt/" in text
    assert FINGERPRINT in text
    assert "gpg --show-keys" in text
    assert "SHA256SUMS" in text
    assert "gh attestation verify" in text and "--repo VadimBley/gi-ai" in text
    assert "@gateway" in text and "lmstudio" in text and "ollama" in text.lower()


def test_readme_contacts_and_licence_section_last():
    text = read("README.md")
    assert "bugs@gi-ai.app" in text and "license@gi-ai.app" in text and ADVISORIES in text
    assert LICENCE_PARAGRAPH in flat(text)
    licence = text.rsplit("\n## ", 1)[1]
    assert licence.startswith("Licence\n"), "the licence section is the last section"
    assert (
        "[![GNU AGPLv3](.github/assets/agplv3-155x51.png)](https://www.gnu.org/licenses/agpl-3.0.html)"
        in licence
    )
    logo = REPO / ".github" / "assets" / "agplv3-155x51.png"
    assert logo.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_readme_mentions_ai_assistance_once():
    text = read("README.md")
    assert len(re.findall(r"AI assistance", text)) == 1


def test_public_docs_have_no_dev_only_references():
    for rel in ("README.md", "SECURITY.md"):
        text = read(rel)
        for term in DEV_ONLY:
            assert term not in text, f"{rel} mentions dev-only '{term}'"


def test_security_policy_is_private_reporting_only():
    text = read("SECURITY.md")
    assert ADVISORIES in text
    emails = set(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text))
    assert emails <= {"bugs@gi-ai.app"}, "security problems are never reported by email"


def test_exported_build_files_name_no_dev_only_paths():
    for rel in ("Makefile", "pyproject.toml"):
        assert ".claude" not in read(rel), f"{rel} must not reference .claude (use local.mk)"


def _dry_run(tmp_path, with_local_mk: bool, target: str = "verify-fast") -> str:
    import shutil
    import subprocess

    shutil.copy(REPO / "Makefile", tmp_path / "Makefile")
    if with_local_mk:
        shutil.copy(REPO / "local.mk", tmp_path / "local.mk")
    return subprocess.run(
        ["make", "-n", "-C", str(tmp_path), *([target] if target else [])],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_makefile_without_local_mk_names_no_dev_only_path(tmp_path):
    out = _dry_run(tmp_path, with_local_mk=False)
    assert "VERIFY OK" in out
    assert ".claude" not in out and "touch" not in out and "spec_drift" not in out
    assert "tests/hooks" not in out


@pytest.mark.skipif(not (REPO / "local.mk").exists(), reason="local.mk is dev-only (public tree)")
def test_local_mk_keeps_the_developer_stamp_and_steps(tmp_path):
    out = _dry_run(tmp_path, with_local_mk=True)
    assert "touch .claude/state/verified-at" in out
    assert "ruff check runtime tests tools .claude/hooks" in out
    assert "tests/hooks" in out and "tools/spec_drift.py" in out


@pytest.mark.parametrize("with_local_mk", [False, True])
def test_bare_make_runs_verify_fast(tmp_path, with_local_mk):
    if with_local_mk and not (REPO / "local.mk").exists():
        pytest.skip("local.mk is dev-only (public tree)")
    assert "VERIFY OK (fast)" in _dry_run(tmp_path, with_local_mk, target="")
