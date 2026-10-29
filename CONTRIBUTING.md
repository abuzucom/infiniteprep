# Contributing

## Development Setup

- Install dependencies with `python -m pip install -e ".[dev]"`.
- Run the test suite with `pytest`.
- Run the agent policy tests with `python scripts/run_tests.py`.
- Run lint checks with `ruff check src tests`.
- Run the agent policy lint with `make lint PYTHON=python`.

## Conventions

- Name branches `<type>/<kebab-description>`. Use feat, fix, chore, docs, or
  test as the type.
- Write commit subjects in `type: description` form. Use imperative mood.
  Limit subjects to 50 characters. Omit the trailing period.

## Safety

- Use parameterized queries for untrusted input.
- Run subprocesses with argument arrays. Disable shell interpretation.
- Keep public APIs backward compatible.
- Never commit secrets, credentials, or private user data.
- Use SHA-256 or SHA-3 for general hashing.
- Use bcrypt, scrypt, or Argon2 with a salt and work factor for passwords.
- Run containers under a non-root runtime account.
- Set `actions/checkout` `persist-credentials: false` unless the job needs the
  checked-out credential afterward. Valid needs include pushes. Valid needs
  include Git credential helper access. Valid needs include private submodule
  fetches. Valid needs include private LFS object fetches. Document each
  exception with
  `# persist-credentials: true: this job <reason> (Rule 11 exception).`
- Add commits instead of rewriting shared branch history.

## Dependencies

- Before changing a dependency, propose the name and pinned version. State the
  purpose. List alternatives. Obtain maintainer approval.
- Pin approved dependencies to exact versions.

## Change Workflow

1. Create a branch following the naming convention above.
2. Add a test that exercises the real behavior. Run the test. Confirm the
   expected failure.
3. Implement the smallest change that makes the new test pass.
4. Run `pytest`. Run `ruff check src tests`. Fix failures without suppressing
   checks.
5. Update README for substantial changes. Update CHANGELOG for all changes.
   Follow Semantic Versioning.
6. Commit using the subject convention above.
7. Open every pull request as a draft. Describe the change. Describe the risks.
   Include verification results.
