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

"""Minimal JSON-Schema subset validator (stdlib only).

Supports: type, required, properties, additionalProperties (bool), enum,
minLength, maxLength, pattern, minimum, maximum, items, const.
That is enough for the contracts Ĝi ships with. Anything else raises,
so an unsupported keyword is noticed in tests instead of silently ignored.
"""

from __future__ import annotations

import re
from typing import Any

_SUPPORTED = {
    "$schema",
    "$comment",
    "title",
    "description",
    "type",
    "required",
    "properties",
    "additionalProperties",
    "enum",
    "minLength",
    "maxLength",
    "pattern",
    "minimum",
    "maximum",
    "items",
    "const",
    "default",
    "format",
}

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


class ContractError(ValueError):
    """Raised when data does not match its contract."""


def _type_ok(value: Any, expected: str) -> bool:
    if expected in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[expected])


def validate(data: Any, schema: dict[str, Any], path: str = "$") -> None:
    unknown = set(schema) - _SUPPORTED
    if unknown:
        raise ContractError(f"{path}: unsupported schema keywords {sorted(unknown)}")

    if "const" in schema and data != schema["const"]:
        raise ContractError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and data not in schema["enum"]:
        raise ContractError(f"{path}: {data!r} not in {schema['enum']}")

    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(data, t) for t in types):
            raise ContractError(f"{path}: expected type {expected}, got {type(data).__name__}")

    if isinstance(data, str):
        if "minLength" in schema and len(data) < schema["minLength"]:
            raise ContractError(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(data) > schema["maxLength"]:
            raise ContractError(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], data):
            raise ContractError(f"{path}: does not match {schema['pattern']}")

    if isinstance(data, (int, float)) and not isinstance(data, bool):
        if "minimum" in schema and data < schema["minimum"]:
            raise ContractError(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and data > schema["maximum"]:
            raise ContractError(f"{path}: above {schema['maximum']}")

    if isinstance(data, dict):
        props: dict[str, Any] = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in data:
                raise ContractError(f"{path}: missing required field '{key}'")
        if schema.get("additionalProperties") is False:
            extra = set(data) - set(props)
            if extra:
                raise ContractError(f"{path}: unexpected fields {sorted(extra)}")
        for key, sub in props.items():
            if key in data:
                validate(data[key], sub, f"{path}.{key}")

    if isinstance(data, list) and "items" in schema:
        for i, item in enumerate(data):
            validate(item, schema["items"], f"{path}[{i}]")
