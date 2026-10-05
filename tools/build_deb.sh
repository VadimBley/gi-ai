#!/usr/bin/env bash
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

# Build dist/gi-ai_<version>_all.deb from runtime/ ONLY (nothing else is copied).
# Usage: tools/build_deb.sh            (version from runtime/gi_ai/__init__.py)
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(python3 -c 'import re,pathlib;print(re.search(r"__version__ = \"([^\"]+)\"",pathlib.Path("runtime/gi_ai/__init__.py").read_text()).group(1))')
MAINTAINER="${GI_MAINTAINER:-Vadim Bley <bugs@gi-ai.app>}"
GH_USER="${GH_USER:-VadimBley}"
OUT=dist
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

# 1) Gate the source tree first: never package something that fails isolation.
python3 tools/isolation_gate.py --root runtime

# 2) Lay out the file system exactly as it will be installed.
install -d "$STAGE/DEBIAN" "$STAGE/usr/lib/gi-ai" "$STAGE/usr/bin" "$STAGE/etc/gi-ai" \
           "$STAGE/usr/share/man/man1" "$STAGE/usr/share/doc/gi-ai"
# copy the python package without caches
tar -C runtime --exclude='__pycache__' --exclude='*.pyc' -cf - gi_ai | tar -C "$STAGE/usr/lib/gi-ai" -xf -
install -m 0755 runtime/bin/gi-ai "$STAGE/usr/bin/gi-ai"
ln -s gi-ai "$STAGE/usr/bin/gi"
install -m 0644 runtime/etc/gi.toml "$STAGE/etc/gi-ai/gi.toml"
gzip -9n -c runtime/share/man/gi-ai.1 > "$STAGE/usr/share/man/man1/gi-ai.1.gz"
ln -s gi-ai.1.gz "$STAGE/usr/share/man/man1/gi.1.gz"
install -m 0644 runtime/share/doc/README.txt "$STAGE/usr/share/doc/gi-ai/README.txt"
# DEP-5, AGPL-3.0-only with the full licence text (Debian has no common-licenses copy of it)
install -m 0644 packaging/debian/copyright "$STAGE/usr/share/doc/gi-ai/copyright"
printf 'gi-ai (%s) stable; urgency=medium\n\n  * Release %s.\n\n -- %s  %s\n' \
  "$VERSION" "$VERSION" "$MAINTAINER" "$(date -R -d @"${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct 2>/dev/null || date +%s)}")" \
  | gzip -9n > "$STAGE/usr/share/doc/gi-ai/changelog.gz"

# 3) Metadata
sed "s|@VERSION@|$VERSION|; s|@MAINTAINER@|$MAINTAINER|; s|@GH_USER@|$GH_USER|" \
  packaging/debian/control.in > "$STAGE/DEBIAN/control"
echo "/etc/gi-ai/gi.toml" > "$STAGE/DEBIAN/conffiles"
(cd "$STAGE" && find usr -type f -print0 | sort -z | xargs -0 md5sum > DEBIAN/md5sums)
find "$STAGE" -type d -exec chmod 0755 {} +
find "$STAGE/usr/lib" "$STAGE/etc" "$STAGE/usr/share" -type f -exec chmod 0644 {} +

# 4) Build reproducibly
mkdir -p "$OUT"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct 2>/dev/null || date +%s)}"
dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$OUT/gi-ai_${VERSION}_all.deb" >/dev/null

# 5) Gate the finished package too
python3 tools/isolation_gate.py --deb "$OUT/gi-ai_${VERSION}_all.deb"
echo "$OUT/gi-ai_${VERSION}_all.deb"
