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

# Runs inside a clean distro container: install the .deb like a user would.
set -euo pipefail
DEB="$1"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq "$DEB" >/dev/null
# shellcheck source=/dev/null
. /etc/os-release && echo "distro: $PRETTY_NAME, python: $(python3 --version)"
gi --version
useradd -m tester
su tester -c 'mkdir -p ~/.config/gi-ai && printf "[llm]\nbackend = \"echo\"\n" > ~/.config/gi-ai/gi.toml && chmod 600 ~/.config/gi-ai/gi.toml'
su tester -c 'gi --json health'
# echo backend returns the whole wrapped prompt: check the prefix and the question line
su tester -c 'gi ask "hello" < /dev/null' > /tmp/gi-ask.out
grep -q "^echo: " /tmp/gi-ask.out
grep -qx hello /tmp/gi-ask.out
su tester -c 'gi init-workspace >/dev/null && gi task new --type req --title smoke >/dev/null && gi selfcheck'
test "$(stat -c %a /home/tester/.local/share/gi-ai/workspace)" = 700
man -w gi >/dev/null 2>&1 || echo "(man-db not installed in image - skipped man check)"
apt-get purge -y -qq gi-ai >/dev/null
test ! -e /usr/bin/gi && test ! -e /etc/gi-ai/gi.toml
echo "INSTALL SMOKE OK"
