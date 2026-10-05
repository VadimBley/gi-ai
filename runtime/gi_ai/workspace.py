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

"""The user's private Ĝi workspace: tasks, evidence and a hash-chained audit log."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SUBDIRS = ("tasks", "evidence")
AUDIT_FILE = "evidence/audit.log"
GENESIS = "0" * 64

# Ĝi never ingests files that look like instructions for other AI tools:
# hidden files/directories and upper-case Markdown files such as README.md,
# AGENTS.md or similar instruction files written for other tools.
_FOREIGN_AGENT_FILE = re.compile(r"^[A-Z0-9_-]+\.md$")


class WorkspaceError(RuntimeError):
    pass


def is_ingestible(path: Path) -> bool:
    """True if Ĝi may read this file as user content."""
    if any(part.startswith(".") for part in path.parts if part not in (".", "..")):
        return False
    return not _FOREIGN_AGENT_FILE.match(path.name)


def init(root: Path) -> list[str]:
    created: list[str] = []
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    for sub in SUBDIRS:
        d = root / sub
        if not d.exists():
            d.mkdir(mode=0o700)
            created.append(str(d))
    log = root / AUDIT_FILE
    if not log.exists():
        log.touch(mode=0o600)
        created.append(str(log))
        audit(root, "workspace.init", {})
    return created


def require(root: Path) -> None:
    if not (root / AUDIT_FILE).exists():
        raise WorkspaceError(f"no workspace at {root}; run: gi init-workspace")


def _last_hash(log: Path) -> str:
    last = GENESIS
    if log.exists():
        with log.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = json.loads(line)["hash"]
    return last


def _entry_hash(prev: str, ts: str, event: str, data: dict[str, Any]) -> str:
    material = json.dumps([prev, ts, event, data], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def audit(root: Path, event: str, data: dict[str, Any]) -> None:
    log = root / AUDIT_FILE
    prev = _last_hash(log)
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {
        "ts": ts,
        "event": event,
        "data": data,
        "prev": prev,
        "hash": _entry_hash(prev, ts, event, data),
    }
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def verify_audit(root: Path) -> tuple[bool, str]:
    log = root / AUDIT_FILE
    prev = GENESIS
    with log.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            e = json.loads(line)
            if e["prev"] != prev or e["hash"] != _entry_hash(prev, e["ts"], e["event"], e["data"]):
                return False, f"audit log broken at line {n}"
            prev = e["hash"]
    return True, "audit log intact"


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)
