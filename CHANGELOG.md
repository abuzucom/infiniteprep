# Changelog

This file documents every notable project change.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows Semantic Versioning.

## [0.1.0] (2026-10-28)

### Added

- Added the `ci` workflow. It runs the review wiring tests on every pull
  request.
- Added the foucault security review caller pinned to foucault 3.4.7
  (`c398b5a29890ba01dee5065a7dc97a91e3e2b08b`).
- Added the euler quality review caller pinned to euler 0.6.5
  (`1a3f67cba9d6ed7f45af4822f9ca27dd14db6a46`).
- Added verbatim copies of the foucault review adapter files.
- Added `tests/test_review_wiring.py` for pins, triggers, secrets, fork
  handling, action pins, and adapter digests.
- Added `docs/pr-review.md` with the wiring, settings, and upgrade steps.
