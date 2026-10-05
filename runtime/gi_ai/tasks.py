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

"""Local task records for the user: new requirement (req), bug, vulnerability (vuln)."""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path

from gi_ai import assets, workspace
from gi_ai.contracts import validate

TYPES = ("req", "bug", "vuln")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def next_id(task_dir: Path, kind: str, today: str) -> str:
    prefix = f"GI-{kind.upper()}-{today}-"
    nums = [int(p.stem.rsplit("-", 1)[1]) for p in task_dir.glob(f"{prefix}*.toml")]
    return f"{prefix}{(max(nums) + 1 if nums else 1):03d}"


def new(root: Path, kind: str, title: str) -> Path:
    if kind not in TYPES:
        raise ValueError(f"type must be one of {TYPES}")
    title = title.strip()
    if not 1 <= len(title) <= 200:
        raise ValueError("title must be 1-200 characters")
    workspace.require(root)
    task_dir = root / "tasks"
    today = datetime.now(UTC).strftime("%Y%m%d")
    task_id = next_id(task_dir, kind, today)
    text = assets.read_text("templates", f"task-{kind}.toml")
    text = (
        text.replace("{{id}}", task_id)
        .replace("{{title}}", _escape(title))
        .replace("{{created}}", datetime.now(UTC).strftime("%Y-%m-%d"))
    )
    record = tomllib.loads(text)
    validate(record, assets.load_json("schemas", "task.schema.json"))
    path = task_dir / f"{task_id}.toml"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    workspace.audit(root, "task.new", {"id": task_id, "type": kind})
    return path


def list_tasks(root: Path) -> list[dict[str, str]]:
    workspace.require(root)
    out = []
    for p in sorted((root / "tasks").glob("GI-*.toml")):
        with p.open("rb") as fh:
            rec = tomllib.load(fh)
        out.append(
            {"id": rec["id"], "type": rec["type"], "status": rec["status"], "title": rec["title"]}
        )
    return out
