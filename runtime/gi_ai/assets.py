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

"""Read-only access to the files that ship inside the gi_ai package."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

ASSET_DIR = Path(__file__).resolve().parent / "assets"


def asset_path(*parts: str) -> Path:
    path = (ASSET_DIR.joinpath(*parts)).resolve()
    if ASSET_DIR not in path.parents:
        raise ValueError("asset path escapes the asset directory")
    return path


def load_toml(*parts: str) -> dict[str, Any]:
    with asset_path(*parts).open("rb") as fh:
        return tomllib.load(fh)


def load_json(*parts: str) -> Any:
    return json.loads(asset_path(*parts).read_text(encoding="utf-8"))


def read_text(*parts: str) -> str:
    return asset_path(*parts).read_text(encoding="utf-8")
