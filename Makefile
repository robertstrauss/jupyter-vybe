# Development targets. The test and build commands live in package.json and
# pyproject; this holds the things that need more than one command.

VENV ?= .venv
PY := $(VENV)/bin/python
UPSTREAM ?= $(HOME)/Desktop/thoughtcode/code-llm

.PHONY: help test check sync-vendor doctor bench

help:
	@echo "make test         pytest, jest, and the invariant gates"
	@echo "make check        the gates alone (fast)"
	@echo "make sync-vendor  diff the vendored copies against upstream (docs/13 R8)"
	@echo "make doctor       check this installation and its agents"
	@echo "make bench        tool-choice bench against the default agent"

test:
	$(PY) -m pytest tests -q
	PATH="$(PWD)/$(VENV)/bin:$$PATH" jlpm jest
	bash scripts/check-invariants.sh

check:
	bash scripts/check-invariants.sh

# R8: copies rot. This makes the rot visible rather than silent -- it reports
# drift and does not "fix" it, because the vendored file is a reference copy
# and the adapter that reuses its probe order may have diverged deliberately.
sync-vendor:
	@status=0; \
	for file in turbofieldfare_client.py setup_turbofieldfare.sh; do \
		ours="jupyter_vybe/adapters/vendored/$$file"; \
		theirs="$(UPSTREAM)/$$file"; \
		if [ ! -f "$$theirs" ]; then \
			echo "skip  $$file: no upstream at $$theirs"; \
			continue; \
		fi; \
		if diff -q "$$ours" "$$theirs" >/dev/null 2>&1; then \
			echo "same  $$file"; \
		else \
			echo "DRIFT $$file"; \
			diff -u "$$ours" "$$theirs" | head -40; \
			status=1; \
		fi; \
	done; \
	if [ $$status -eq 0 ]; then \
		echo; echo "Vendored copies match upstream."; \
	else \
		echo; \
		echo "Upstream has changed. Review the diff above, then copy across"; \
		echo "deliberately and update the date in vendored/README.md."; \
	fi; \
	exit $$status

doctor:
	$(VENV)/bin/jupyter-vybe-doctor

bench:
	$(PY) scripts/bench-tools.py --runs 2
