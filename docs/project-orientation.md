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

Obtain consent before each workflow that the command execution gate routes to
consent. Workflows the gate allows run without consent.

Do not touch without active human approval:

- `ci/` holds verbatim foucault review adapters.
- `.github/workflows/security-review-pr.yml` and
  `quality-review-pr.yml` pin review workflows.
- `tests/test_review_wiring.py` asserts those pins and digests.
- `scripts/check_pr_review_response.py` follows the foucault copy.

Ruff skips the copied gate files. The agents lint covers those files.

Application copy:

- The `AGENTS.md` prose requirements govern code, code comments,
  documentation, and agent interaction with the human.
- The requirements do not govern application or website copy.
- On-screen strings under `src/infiniteprep/ui/` use plain UX copy:
  short labels, plain verbs, and second person where it helps.
- Owner ruling, 2026-11-21, recorded in UI-R (#245).
