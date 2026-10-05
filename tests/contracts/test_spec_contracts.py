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

"""The runtime must ship exactly the contracts the approved specs define.

Specs live in spec/ (read-only copy synced from the private specification repo).
Each spec declares JSON schemas in fenced blocks: ```json schema=<name>
"""

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO / "spec"
RUNTIME_SCHEMAS = REPO / "runtime" / "gi_ai" / "assets" / "schemas"
# Development-tooling contracts: they live under tools/ and must never ship in the runtime.
TOOLING_SCHEMAS = {"feature_inventory": REPO / "tools" / "lmstudio_features.schema.json"}
BLOCK = re.compile(r"```json schema=([a-z0-9_-]+)\n(.*?)\n```", re.DOTALL)
IGNORED_KEYS = {"$id", "$comment", "title", "description"}


def _strip(node):
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k not in IGNORED_KEYS}
    if isinstance(node, list):
        return [_strip(v) for v in node]
    return node


def spec_schemas():
    for spec in sorted(SPEC_DIR.glob("SPEC-*.md")):
        text = spec.read_text(encoding="utf-8")
        if "\nstatus: approved" not in text:
            continue
        for name, body in BLOCK.findall(text):
            yield pytest.param(spec.name, name, json.loads(body), id=f"{spec.stem}:{name}")


@pytest.mark.parametrize("spec_name,schema_name,schema", list(spec_schemas()))
def test_runtime_schema_matches_spec(spec_name, schema_name, schema):
    runtime_file = RUNTIME_SCHEMAS / f"{schema_name}.schema.json"
    if schema_name in TOOLING_SCHEMAS:
        tool_file = TOOLING_SCHEMAS[schema_name]
        assert tool_file.exists(), f"{spec_name} defines '{schema_name}' but {tool_file} is missing"
        assert _strip(json.loads(tool_file.read_text(encoding="utf-8"))) == _strip(schema)
        assert not runtime_file.exists(), f"tooling schema '{schema_name}' must not ship"
        return
    assert runtime_file.exists(), f"{spec_name} defines '{schema_name}' but runtime lacks it"
    assert _strip(json.loads(runtime_file.read_text(encoding="utf-8"))) == _strip(schema)


def test_spec_schemas_are_valid_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    for param in spec_schemas():
        jsonschema.Draft202012Validator.check_schema(param.values[2])


def test_tooling_schemas_are_defined_by_an_approved_spec():
    present = {name for name, path in TOOLING_SCHEMAS.items() if path.exists()}
    if not present:
        pytest.skip(
            "tooling schema tools/lmstudio_features.schema.json not present "
            "(public tree: the feature watch is development tooling and is not exported)"
        )
    defined = {param.values[1] for param in spec_schemas()}
    assert present <= defined


def test_tooling_schema_check_skips_when_the_file_is_absent(monkeypatch, tmp_path):
    monkeypatch.setitem(TOOLING_SCHEMAS, "feature_inventory", tmp_path / "absent.schema.json")
    with pytest.raises(pytest.skip.Exception, match="not present"):
        test_tooling_schemas_are_defined_by_an_approved_spec()


def test_no_tooling_schema_anywhere_in_the_runtime():
    shipped = {p.name for p in (REPO / "runtime").rglob("*.json")}
    for path in TOOLING_SCHEMAS.values():
        assert path.name not in shipped
