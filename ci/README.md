# ci

This directory holds no code. The `abuzucom/agents-personas` engine supplies
the review adapter.

The CodeQL default setup reads `.github/codeql/config.yml` from `main`. A
change that deletes this directory must land after `main` drops `ci` from that
file. A declared path must exist for the analysis to run.
