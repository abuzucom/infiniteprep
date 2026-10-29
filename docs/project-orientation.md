# Per-repo orientation

Repository: `abuzucom/infiniteprep`. Source policy: `abuzucom/agents`.

InfinitePrep is a PySide6 desktop application for DJ music metadata.
Application code lives under `src/infiniteprep/`.

Commands:

- Install: `python -m pip install -e ".[dev]"`
- Lint: `ruff check src tests`
- Format: `ruff format --check src tests`
- Tests: `pytest`
- Policy lint: `make lint PYTHON=python`
- Policy tests: `python scripts/run_tests.py`

Obtain consent before tests, scripts, or Makefile targets.

Do not touch without active-human approval:

- `ci/` holds verbatim foucault review adapters.
- `.github/workflows/security-review-pr.yml` and
  `quality-review-pr.yml` pin review workflows.
- `tests/test_review_wiring.py` asserts those pins and digests.
- `scripts/check_pr_review_response.py` follows the foucault copy.

Ruff skips the copied gate files. The agents lint covers those files.
