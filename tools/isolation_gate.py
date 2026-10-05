#!/usr/bin/env python3
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

"""Isolation gate: keeps the Standalone Runtime (what ships to users) free of
development-plane material.

Checks a directory tree (default: runtime/) or a built .deb:
  * only allow-listed file types
  * no Markdown files, no Mermaid diagrams, no AI-tool config files/dirs
  * no development-plane vocabulary (terms from tools/isolation_terms.txt, when present)
  * (for .deb) every file path is inside the expected install locations

Usage:
  tools/isolation_gate.py                 # scan runtime/
  tools/isolation_gate.py --deb dist/gi-ai_0.1.0_all.deb
  tools/isolation_gate.py --stdin-path runtime/x.py < content   # used by hooks
Exit 0 = clean, 1 = violations (listed on stderr), 2 = usage error.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

ALLOWED_SUFFIXES = {".py", ".toml", ".json", ".txt", ".1", ".gz", ""}
ALLOWED_NAMES_NO_SUFFIX = {"gi-ai", "gi", "copyright", "control", "md5sums", "conffiles"}

FORBIDDEN_NAME = re.compile(
    r"(^|/)(\.claude|\.github|\.mcp\.json|[^/]*\.md|[^/]*\.mmd|[^/]*\.ipynb|__pycache__)(/|$)",
    re.IGNORECASE,
)

# Development-plane vocabulary that must never reach users or Ĝi's own model. The list is
# private development data: when the file is absent (public tree) only the structural checks run.
TERMS_FILE = Path(__file__).resolve().parent / "isolation_terms.txt"


def load_terms(path: Path = TERMS_FILE) -> list[str] | None:
    """One term per line, '#' comment lines; a "quoted" term keeps its edge spaces.

    None only when nothing exists at the path. A file that is unreadable, empty or has an empty
    term stops the gate instead of silently scanning for fewer terms (fail closed)."""
    if not path.exists() and not path.is_symlink():
        return None
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise SystemExit(f"isolation gate: cannot read {path}: {exc}") from None
    terms = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if len(line) >= 2 and line[0] == line[-1] == '"':
            line = line[1:-1]
        if not line.strip():
            raise SystemExit(f"isolation gate: empty term in {path}")
        terms.append(line.lower())
    if not terms:
        raise SystemExit(f"isolation gate: {path} lists no terms")
    return terms


FORBIDDEN_TERMS = load_terms()
TERM_RE = (
    re.compile("|".join(re.escape(t) for t in FORBIDDEN_TERMS), re.IGNORECASE)
    if FORBIDDEN_TERMS
    else None
)

DEB_ALLOWED_PREFIXES = (
    "./usr/lib/gi-ai/",
    "./usr/bin/",
    "./etc/gi-ai/",
    "./usr/share/man/man1/",
    "./usr/share/doc/gi-ai/",
)


def scan_text(rel: str, text: str) -> list[str]:
    problems: list[str] = []
    if TERM_RE is None:
        return problems
    for m in TERM_RE.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        problems.append(f"{rel}:{line}: forbidden term '{m.group(0)}'")
    return problems


def scan_name(rel: str) -> list[str]:
    problems = []
    if FORBIDDEN_NAME.search(rel):
        problems.append(f"{rel}: forbidden file or directory name")
    name = rel.rsplit("/", 1)[-1]
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES or (suffix == "" and name not in ALLOWED_NAMES_NO_SUFFIX):
        problems.append(f"{rel}: file type not allowed in the runtime")
    return problems


def scan_tree(root: Path, base: Path, skip_caches: bool = False) -> list[str]:
    problems: list[str] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(base).as_posix()
        if skip_caches and "__pycache__" in path.parts:
            continue  # local bytecode caches are never packaged (build_deb.sh excludes them)
        if path.is_dir():
            if FORBIDDEN_NAME.search(rel + "/"):
                problems.append(f"{rel}/: forbidden directory")
            continue
        problems += scan_name(rel)
        data = path.read_bytes()
        if path.suffix == ".gz":
            import gzip

            data = gzip.decompress(data)
        problems += scan_text(rel, data.decode("utf-8", errors="replace"))
    return problems


def scan_deb(deb: Path) -> list[str]:
    problems: list[str] = []
    listing = subprocess.run(
        ["dpkg-deb", "--contents", str(deb)], check=True, capture_output=True, text=True
    ).stdout
    for line in listing.splitlines():
        path = line.split()[5]
        if path.endswith("/"):
            continue
        if not path.startswith(DEB_ALLOWED_PREFIXES):
            problems.append(f"{path}: installed outside the allowed locations")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["dpkg-deb", "-R", str(deb), tmp], check=True)
        problems += scan_tree(Path(tmp), Path(tmp))
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deb", type=Path)
    ap.add_argument("--root", type=Path, default=REPO / "runtime")
    ap.add_argument("--stdin-path", help="check content from stdin as if it were this path")
    args = ap.parse_args()

    if args.stdin_path:
        problems = scan_name(args.stdin_path) + scan_text(args.stdin_path, sys.stdin.read())
    elif args.deb:
        problems = scan_deb(args.deb)
    else:
        if not args.root.is_dir():
            print(f"no such directory: {args.root}", file=sys.stderr)
            return 2
        problems = scan_tree(args.root, args.root.parent, skip_caches=True)

    for p in problems:
        print(f"ISOLATION: {p}", file=sys.stderr)
    if not problems:
        suffix = "" if FORBIDDEN_TERMS is not None else " (no private term list)"
        print(f"isolation gate: clean{suffix}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
