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

"""SPEC-0001 v1.3.0 B19: the man page names the licence, bugs address and advisories URL."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MAN = (REPO / "runtime" / "share" / "man" / "gi-ai.1").read_text(encoding="utf-8")
ADVISORIES = "https://github.com/VadimBley/gi-ai/security/advisories/new"


def section(name: str) -> str:
    m = re.search(rf"^\.SH {name}\n(.*?)(?=^\.SH |\Z)", MAN, re.MULTILINE | re.DOTALL)
    assert m, f"man page lacks a {name} section"
    return re.sub(r"\\-", "-", m.group(1))


def plain(text: str) -> str:
    text = re.sub(r"\\f[BIRP]", "", text)
    text = re.sub(r"^\.UR (\S+)$", r"\1", text, flags=re.MULTILINE)
    return " ".join(line for line in text.splitlines() if not line.startswith("."))


def test_copyright_section_matches_version_statement():
    text = " ".join(plain(section("COPYRIGHT")).split())
    assert "Copyright (C) 2026 Vadim Bley" in text
    assert "License AGPL-3.0-only: GNU Affero General Public License version 3" in text
    assert "https://www.gnu.org/licenses/agpl-3.0.html" in text
    assert "This is free software: you are free to change and redistribute it." in text
    assert "There is NO WARRANTY, to the extent permitted by law." in text


def test_reporting_bugs_names_bugs_address_and_advisories_only():
    text = plain(section("REPORTING BUGS"))
    assert "bugs@gi-ai.app" in text
    assert ADVISORIES in text
    emails = set(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text))
    assert emails == {"bugs@gi-ai.app"}, "security problems are never reported by email"


def test_synopsis_mentions_version():
    assert "--version" in section("SYNOPSIS") or "--version" in section("OPTIONS")


def _all_plain() -> str:
    return " ".join(plain(re.sub(r"\\-", "-", MAN)).split())


def test_at24_man_page_states_the_metadata_refusal():
    text = _all_plain()
    for needle in ("169.254.169.254", "169.254.170.2", "fd00:ec2::254", "a cloud metadata address"):
        assert needle in text, needle
    assert "METADATA" in text


def test_at25_man_page_states_the_token_file_rule():
    text = _all_plain()
    assert "token_file must be a regular file inside ~/.config/gi-ai/ reached without links" in text
    assert "symbolic link" in text and "hard link" in text


def test_at27_man_page_states_the_normalisation():
    text = _all_plain()
    assert "NFKC" in text
    assert "format characters" in text


def test_at28_man_page_states_the_connect_time_rule():
    text = _all_plain()
    for needle in (
        "looked up once per request",
        "localhost does not resolve to loopback",
        "cannot resolve",
        "Redirects are never followed",
        "64:ff9b:1::/48",
        "2002::/16",
        "zone id",
    ):
        assert needle in text, needle
    assert "A host name is not looked up for this check" not in text


def test_at30_at31_man_page_states_marks_and_both_limits():
    text = _all_plain()
    for needle in ("invisible characters", "combining marks", "after normalising"):
        assert needle in text, needle
    assert "The length limit applies to the question as typed." not in text
