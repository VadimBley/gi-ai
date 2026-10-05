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

"""Build the .deb and check it behaves like a user install (no root needed)."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb missing")


@pytest.fixture(scope="module")
def deb(tmp_path_factory):
    out = (
        subprocess.run(
            ["bash", "tools/build_deb.sh"], cwd=REPO, check=True, capture_output=True, text=True
        )
        .stdout.strip()
        .splitlines()[-1]
    )
    return REPO / out


def test_package_metadata(deb):
    info = subprocess.run(
        ["dpkg-deb", "-f", str(deb), "Package", "Architecture", "Depends"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "Package: gi-ai" in info
    assert "Architecture: all" in info
    assert "python3 (>= 3.11)" in info


def test_package_names_the_real_maintainer_and_homepage(deb):
    """Defaults must be right without any environment (the release job sets none)."""
    info = subprocess.run(
        ["dpkg-deb", "-f", str(deb), "Maintainer", "Homepage"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "Maintainer: Vadim Bley <bugs@gi-ai.app>" in info
    assert "Homepage: https://github.com/VadimBley/gi-ai" in info


def test_installed_layout_runs_in_isolated_mode(deb, tmp_path, subprocess_env):
    root = tmp_path / "root"
    subprocess.run(["dpkg-deb", "-x", str(deb), str(root)], check=True)
    launcher = root / "usr" / "bin" / "gi"
    assert launcher.is_symlink()
    r = subprocess.run(
        [sys.executable, "-I", str(launcher), "--json", "health"],
        env=subprocess_env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["name"] == "Ĝi"


def test_package_passes_isolation_gate(deb):
    r = subprocess.run(
        [sys.executable, "tools/isolation_gate.py", "--deb", str(deb)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr


def test_feature_watch_tooling_is_not_packaged(deb):
    """Development tooling (LM Studio feature watch, inventory, schema) is not packaged."""
    listing = subprocess.run(
        ["dpkg-deb", "--contents", str(deb)], check=True, capture_output=True, text=True
    ).stdout
    paths = [line.split()[5] for line in listing.splitlines() if line.strip()]
    assert paths, "empty package listing"
    for path in paths:
        assert "lmstudio_feature" not in path, path
        assert "/tools/" not in path, path


def _extract(deb, tmp_path):
    root = tmp_path / "root"
    subprocess.run(["dpkg-deb", "-x", str(deb), str(root)], check=True)
    return root


def test_at23_copyright_is_dep5_agpl_with_full_text(deb, tmp_path):
    text = (_extract(deb, tmp_path) / "usr/share/doc/gi-ai/copyright").read_text(encoding="utf-8")
    assert text.startswith(
        "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
    )
    assert "\nUpstream-Contact: Vadim Bley <license@gi-ai.app>\n" in text
    assert "\nFiles: *\nCopyright: 2026 Vadim Bley\nLicense: AGPL-3.0-only\n" in text
    standalone = text.split("\n\nLicense: AGPL-3.0-only\n", 1)[1]
    lines = standalone.rstrip("\n").split("\n")
    body = "\n".join("" if line == " ." else line[1:] for line in lines)
    assert body == (REPO / "COPYING").read_text(encoding="utf-8").rstrip("\n")
    assert "Apache" not in text


def test_at23_installed_sources_carry_the_notice(deb, tmp_path):
    import gzip

    sys.path.insert(0, str(REPO / "tests" / "unit"))
    from test_licence_notices import NOTICE, notice_of

    root = _extract(deb, tmp_path)
    files = [*sorted((root / "usr/lib/gi-ai").rglob("*.py")), root / "usr/bin/gi-ai"]
    assert len(files) > 5
    for f in files:
        assert notice_of(f.read_text(encoding="utf-8")).startswith(NOTICE), f
    man = gzip.decompress((root / "usr/share/man/man1/gi-ai.1.gz").read_bytes()).decode()
    assert notice_of(man).startswith(NOTICE)
    man = man.replace("\\-", "-")  # roff hyphens
    assert "bugs@gi-ai.app" in man
    assert "https://github.com/VadimBley/gi-ai/security/advisories/new" in man


def test_at22_installed_launcher_prints_version(deb, tmp_path, subprocess_env):
    sys.path.insert(0, str(REPO / "tests" / "unit"))
    from test_version import EXPECTED

    root = _extract(deb, tmp_path)
    bad = tmp_path / "unreadable.toml"
    bad.write_text("this is [not toml", encoding="utf-8")
    bad.chmod(0)
    env = dict(subprocess_env, GI_AI_CONFIG=str(bad))
    env.update(GI_AI_LLM_TOKEN="x" * 16)  # present, never read
    for name in ("gi", "gi-ai"):
        r = subprocess.run(
            [sys.executable, "-I", str(root / "usr" / "bin" / name), "--version"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout == EXPECTED
