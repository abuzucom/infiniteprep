.PHONY: sync check lint test identity changelog

# Overridable so a platform without this name can supply its own:
#   make test PYTHON=py
PYTHON ?= python3

PROSE_FILES = AGENTS.md README.md CHANGELOG.md \
	docs/project-orientation.md docs/template-drift.md \
	plan/HANDOFF.md.example SECURITY.md CONTRIBUTING.md

sync:
	$(PYTHON) scripts/sync.py

check:
	$(PYTHON) scripts/sync.py --check

changelog:
	$(PYTHON) scripts/check_changelog.py

lint:
	$(PYTHON) scripts/lint_style.py
	$(PYTHON) scripts/check_us_spelling.py $(PROSE_FILES)
	$(PYTHON) scripts/check_english_only.py $(PROSE_FILES)
	$(PYTHON) scripts/check_hedging.py $(PROSE_FILES)
	$(PYTHON) scripts/check_conflict_markers.py

test:
	$(PYTHON) scripts/run_tests.py

identity:
	$(PYTHON) scripts/check_git_identity.py --advise
