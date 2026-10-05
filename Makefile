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

# gi-ai developer commands. `make verify` is the single "is it done?" command.
# An optional local.mk adds developer-only paths and steps (see the variables below).
-include local.mk
.DEFAULT_GOAL := verify-fast

PY ?= python3
VERIFY_STAMP ?=
LINT_PATHS = runtime tests tools $(LINT_EXTRA)
TEST_PATHS = tests/unit tests/contracts tests/isolation $(TEST_EXTRA)
DEB = $(shell ls -t dist/gi-ai_*_all.deb 2>/dev/null | head -1)
IMAGES ?= ubuntu:24.04 ubuntu:26.04 debian:12 debian:13 linuxmintd/mint22.1-amd64

.PHONY: verify verify-fast lint test gate deb install-test clean

# Touch $(VERIFY_STAMP) after a green run, when one is configured.
STAMP = $(if $(VERIFY_STAMP),@mkdir -p $(dir $(VERIFY_STAMP)) && touch $(VERIFY_STAMP))

verify-fast: lint test gate
	$(STAMP)
	@echo "VERIFY OK (fast)"

verify: lint test gate deb
	$(STAMP)
	@echo "VERIFY OK"

lint:
	ruff check $(LINT_PATHS)
	ruff format --check $(LINT_PATHS)

test:
	$(PY) -m pytest -q -p no:cacheprovider $(TEST_PATHS)

gate:
	$(PY) tools/isolation_gate.py
	@for script in $(GATE_EXTRA); do echo "$(PY) $$script"; $(PY) $$script || exit 1; done

deb:
	bash tools/build_deb.sh
	$(PY) -m pytest -q -p no:cacheprovider tests/package

install-test: deb
	@for img in $(IMAGES); do \
	  echo "== $$img"; \
	  docker run --rm -v "$$PWD/dist:/dist:ro" -v "$$PWD/tests/install:/t:ro" $$img bash /t/install_smoke.sh /dist/$(notdir $(DEB)) || exit 1; \
	done

clean:
	rm -rf dist .pytest_cache .ruff_cache
	find . -name __pycache__ -prune -exec rm -rf {} +
