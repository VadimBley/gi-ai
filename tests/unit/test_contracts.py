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

import pytest

from gi_ai.contracts import ContractError, validate

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["a"],
    "properties": {"a": {"type": "integer", "minimum": 1}, "b": {"type": "string", "enum": ["x"]}},
}


def test_valid():
    validate({"a": 2, "b": "x"}, SCHEMA)


@pytest.mark.parametrize("data", [{}, {"a": 0}, {"a": True}, {"a": 1, "c": 1}, {"a": 1, "b": "y"}])
def test_invalid(data):
    with pytest.raises(ContractError):
        validate(data, SCHEMA)


def test_unsupported_keyword_is_loud():
    with pytest.raises(ContractError, match="unsupported"):
        validate({}, {"type": "object", "oneOf": []})
