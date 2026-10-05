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

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "tools" / "isolation_gate.py"


def run_gate(*args, stdin=None):
    return subprocess.run(
        [sys.executable, str(GATE), *args], input=stdin, capture_output=True, text=True
    )


def test_runtime_tree_is_clean():
    r = run_gate()
    assert r.returncode == 0, r.stderr


def test_markdown_in_runtime_is_rejected():
    r = run_gate("--stdin-path", "runtime/gi_ai/notes.md", stdin="hello")
    assert r.returncode == 1 and "forbidden file" in r.stderr


@pytest.mark.skipif(
    not (REPO / "tools" / "isolation_terms.txt").exists(),
    reason="private term list absent (public tree)",
)
def test_mermaid_and_dev_terms_are_rejected():
    r = run_gate(
        "--stdin-path", "runtime/gi_ai/x.py", stdin="# see the control plane\n# ```mermaid\n"
    )
    assert r.returncode == 1
    assert "control plane" in r.stderr and "```mermaid" in r.stderr


def test_plain_runtime_code_is_accepted():
    r = run_gate("--stdin-path", "runtime/gi_ai/x.py", stdin="print('hello')\n")
    assert r.returncode == 0, r.stderr


# --- private term list (tools/isolation_terms.txt, dev-only) ------------------------------
TERMS_FILE = REPO / "tools" / "isolation_terms.txt"
# sha256 of the 23 terms in order, joined by "\n" (the list itself stays out of exported files).
TERMS_SHA256 = "b6da9b451dca03d3b81e083bb35073c1e4355c5831b0eab9e04c9342ba91a3fd"
needs_terms = pytest.mark.skipif(
    not TERMS_FILE.exists(), reason="private term list absent (public tree)"
)


DEV_MARKERS = (REPO / "local.mk", REPO / ".claude" / "hooks")


def test_dev_repo_keeps_the_private_term_list():
    """Fail closed: in the development repository (local.mk or .claude/hooks present) a missing
    term list is an error, not a skip. Only the public tree, which has neither, runs without it."""
    if not any(marker.exists() for marker in DEV_MARKERS):
        pytest.skip("public tree: the term list is not exported")
    assert TERMS_FILE.is_file(), "tools/isolation_terms.txt is missing: the gate would fail open"
    assert len(_load_gate().FORBIDDEN_TERMS or []) == 23


def _load_gate():
    import importlib.util

    spec = importlib.util.spec_from_file_location("isolation_gate_under_test", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@needs_terms
def test_term_list_is_loaded_unchanged_from_the_terms_file():
    import hashlib

    terms = _load_gate().FORBIDDEN_TERMS
    assert len(terms) == 23
    assert hashlib.sha256("\n".join(terms).encode()).hexdigest() == TERMS_SHA256
    assert any(t.endswith(" ") for t in terms), "quoted term lost its trailing space"


@needs_terms
def test_gate_with_terms_prints_the_plain_clean_message():
    r = run_gate()
    assert r.returncode == 0, r.stderr
    assert r.stdout == "isolation gate: clean\n"


@pytest.fixture
def gate_without_terms(tmp_path):
    """A copy of the gate as it is exported: no terms file next to it."""
    tools = tmp_path / "tools"
    tools.mkdir()
    shutil.copy(GATE, tools / "isolation_gate.py")
    root = tmp_path / "runtime"
    (root / "gi_ai").mkdir(parents=True)
    (root / "gi_ai" / "x.py").write_text("print('hello')\n", encoding="utf-8")

    def run(*args, stdin=None):
        return subprocess.run(
            [sys.executable, str(tools / "isolation_gate.py"), "--root", str(root), *args],
            input=stdin,
            capture_output=True,
            text=True,
        )

    run.root = root
    run.terms = tools / "isolation_terms.txt"
    return run


def test_gate_without_terms_reports_clean_without_list(gate_without_terms):
    r = gate_without_terms()
    assert r.returncode == 0, r.stderr
    assert r.stdout == "isolation gate: clean (no private term list)\n"


@pytest.mark.parametrize(
    "rel",
    ["gi_ai/notes.md", "gi_ai/flow.mmd", ".github/x.toml", "gi_ai/data.bin", "gi_ai/nb.ipynb"],
)
def test_gate_without_terms_still_runs_structural_checks(gate_without_terms, rel):
    path = gate_without_terms.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n", encoding="utf-8")
    r = gate_without_terms()
    assert r.returncode == 1
    assert "ISOLATION:" in r.stderr


def test_gate_without_terms_checks_stdin_names(gate_without_terms):
    r = gate_without_terms("--stdin-path", "runtime/gi_ai/notes.md", stdin="hello")
    assert r.returncode == 1 and "forbidden file" in r.stderr


@pytest.mark.parametrize(
    "content",
    ["", "# only a comment\n", '"  "\n', '""\n'],
    ids=["empty", "comments-only", "blank-quoted", "empty-quoted"],
)
def test_gate_refuses_a_weakened_terms_file(gate_without_terms, content):
    gate_without_terms.terms.write_text(content, encoding="utf-8")
    r = gate_without_terms()
    assert r.returncode != 0
    assert "isolation gate:" in r.stderr and "clean" not in r.stdout


def test_gate_refuses_a_directory_in_place_of_the_terms_file(gate_without_terms):
    gate_without_terms.terms.mkdir()
    r = gate_without_terms()
    assert r.returncode != 0 and "cannot read" in r.stderr


def test_gate_reads_a_terms_file_with_bom(gate_without_terms):
    gate_without_terms.terms.write_text("\ufeffzebra-term\n", encoding="utf-8")
    (gate_without_terms.root / "gi_ai" / "z.py").write_text("# Zebra-Term\n", encoding="utf-8")
    r = gate_without_terms()
    assert r.returncode == 1 and "zebra-term" in r.stderr.lower()
