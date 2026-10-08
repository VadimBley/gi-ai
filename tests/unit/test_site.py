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

"""The website (site/): static HTML and CSS only, no scripts, nothing loaded from elsewhere."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SITE = REPO / "site"
FINGERPRINT = "D523 F5BF 268D FD9F CD08 2391 E1B6 1810 1C64 54A0"
PAGES = sorted(SITE.rglob("*.html"))
STYLES = sorted(SITE.rglob("*.css"))

# Attributes and CSS constructs that make a browser load something.
LOADS = re.compile(
    r"""<(?:link|img|source|iframe|embed|object|video|audio|input)\b[^>]*?\b(?:href|src|srcset|data)\s*=\s*["']?([^"'\s>]+)""",
    re.IGNORECASE,
)
CSS_LOADS = re.compile(r"""(?:url\(\s*["']?|@import\s+["'])([^"')\s]+)""", re.IGNORECASE)


def test_site_has_pages():
    assert (SITE / "index.html") in PAGES
    assert (SITE / "install" / "index.html") in PAGES and (SITE / "apt" / "index.html") in PAGES


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.relative_to(SITE).as_posix())
def test_no_scripts(page):
    text = page.read_text(encoding="utf-8")
    assert not re.search(r"<script\b", text, re.IGNORECASE), "script element"
    assert not re.search(r"\son[a-z]+\s*=", text, re.IGNORECASE), "inline event handler"
    assert "javascript:" not in text.lower(), "javascript: URL"


def _local_target(source: Path, ref: str) -> Path | None:
    """The local file a reference points to, or None when it points elsewhere."""
    if re.match(r"^(?:[a-z][a-z0-9+.-]*:|//)", ref, re.IGNORECASE):
        return None
    ref = ref.split("#", 1)[0].split("?", 1)[0]
    base = SITE if ref.startswith("/") else source.parent
    return (base / ref.lstrip("/")).resolve()


@pytest.mark.parametrize("page", PAGES + STYLES, ids=lambda p: p.relative_to(SITE).as_posix())
def test_scripts_styles_images_are_local_files(page):
    text = page.read_text(encoding="utf-8")
    refs = LOADS.findall(text) if page.suffix == ".html" else []
    refs += CSS_LOADS.findall(text)
    for ref in refs:
        target = _local_target(page, ref)
        assert target is not None, f"{ref} is loaded from elsewhere"
        assert target.is_file() and SITE.resolve() in target.parents, f"{ref} is not a site file"


def test_detects_external_loads():
    assert LOADS.findall('<img src="https://x.example/a.png">') == ["https://x.example/a.png"]
    assert _local_target(SITE / "index.html", "https://x.example/a.png") is None
    assert CSS_LOADS.findall("a{background:url('//cdn.example/x.png')}") == ["//cdn.example/x.png"]


@pytest.mark.parametrize("rel", ["install/index.html", "apt/index.html"])
def test_fingerprint_on_install_and_apt_pages(rel):
    assert FINGERPRINT in (SITE / rel).read_text(encoding="utf-8")


def test_custom_domain():
    assert (SITE / "CNAME").read_text(encoding="utf-8").strip() == "gi-ai.app"


def test_security_page_key_lookup():
    """Key download, WKD lookup and fingerprint, with the project address only."""
    text = (SITE / "security" / "index.html").read_text(encoding="utf-8")
    assert "https://gi-ai.app/apt/gi-ai.asc" in text
    assert "gpg --locate-keys maintainer@gi-ai.app" in text
    assert FINGERPRINT in text


def _plain(rel: str) -> str:
    html = (SITE / rel / "index.html").read_text(encoding="utf-8")
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_docs_page_states_the_1_4_rules():
    # SPEC-0001 1.4.0: where the docs page states the endpoint and token_file rules, it is current.
    text = _plain("docs")
    assert "169.254.169.254" in text and "metadata" in text
    assert "without links" in text or "symbolic link" in text


def test_security_page_states_the_metadata_refusal():
    text = _plain("security")
    assert "metadata" in text
