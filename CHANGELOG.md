# Changelog

This file documents every notable project change.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] (2026-10-29)

### Added

- The main window adds **Export Playlist (.m3u8)**. It writes the visible rows to a chosen path through `PlaylistExporter` and appends a missing `.m3u8` suffix. A failed write shows a warning.
- `MainWindow` accepts an optional `on_export_playlist` callback.
- Added `tests/unit/test_ui_playlist_export_action.py`.

- The main window adds **Transcode to XDJ**. It confirms the file count, mode, and preset from Settings, transcodes the visible rows, and lists each output and failure. `services/transcode_batch.py` isolates per-file failures.
- `MainWindow` accepts an optional `on_transcode_batch` callback.
- Added `tests/unit/test_ui_transcode_action.py` and the `tests/fixtures/qt_dialogs.py` dialog recorder.
- Adopted the `abuzucom/agents` policy at commit `b452348`. `AGENTS.md` holds the canonical policy. `scripts/sync.py` generates the tool copies.
- Added the complete gate set under `hooks/` with Claude Code, Codex, Gemini, and Antigravity registrations.
- Added the agents checkers under `scripts/` and the agents test suites under `tests/`.
- Added `shared-files.json`, `hook-coverage-baseline.json`, `.local-path-allowlist`, and `.pre-commit-config.yaml`.
- Added the `agents-compliance`, `agents-md-compliance`, `sync-check`, `immutable-conflict-check`, and `gate-integrity` workflows.
- Added the `agents-lint` job to the `ci` workflow.
- Added `SECURITY.md`, `CONTRIBUTING.md`, `docs/project-orientation.md`, and `docs/template-drift.md`.

### Changed

- `TranscodePreset.XDJ_LOSSLESS_AUTO` replaces `XDJ_RX3_LOSSLESS_AUTO` as the default preset. `XDJ_RX3_LOSSLESS_AUTO` stays as an alias, and a stored `"XDJ_RX3_LOSSLESS_AUTO"` settings value still resolves. Settings menus list the preset once.
- Sibling transcode output files end in `_XDJ` instead of `_XDJ_RX3`.
- The Settings labels read **XDJ Transcode Mode** and **XDJ Transcode Preset**.
- Ruff skips the copied agents gate files. The `agents-lint` job and `scripts/run_tests.py` cover those files.
- `.gitattributes` pins LF line endings for the policy files.
- The `checks` and `test-matrix` jobs install `requirements-checkers.txt` for the agents tests.
- The `checks` and `test-matrix` test steps unset `GITHUB_EVENT_PATH`. Push events no longer reach the compliance scanner tests.
- The README holds a full setup and usage walkthrough. It covers prerequisites, per-OS install, optional tools, data locations, credentials, each desktop workflow step, the `evaluate-sample` harness, and current limitations.
- The README documents the transcode step. Its limitations list drops the missing transcode action and records the `MP3_320_CBR` and in-place OGG or M4A behavior.
- The README documents the playlist export step. Its limitations list drops the missing playlist export action.
- The README test section names the agents policy and gate suites in the `unittest` run. It installs `requirements-checkers.txt` first. Its commands run from an active virtual environment on every OS.

### Fixed

- The `sync-check` suite steps drop `GITHUB_EVENT_PATH`. Push events to `main` no longer fail the compliance scanner tests in `tests-windows`, `tests-macos`, and `check-sync`.
- The `security-review-pr` `resolve-pr` job runs only for pull request `ci` runs. A push to `main` now skips the review instead of failing it.
- Added `tests/test_push_event_workflow_wiring.py` for both workflow guards.

### Security

- `FpcalcBootstrapService` pins the SHA-256 digest of the official Chromaprint 1.5.1 Windows and macOS archives. Extraction requires a 64-character hexadecimal digest and compares it in constant time.
- `install_fpcalc` refuses Linux and other platforms without a pinned archive. Those platforms install fpcalc from the system package manager.
- `install_fpcalc` always verifies the download against the pinned digest. A caller `expected_sha256` that differs from the pin raises before any download.
- ZIP and TAR extraction of `fpcalc` read at most `MAX_FPCALC_BINARY_BYTES` plus one byte. The untrusted header size no longer gates the read.
- `MutagenAdapter.render_filename` substitutes only known `{token}` names and keeps the `{{` and `}}` literal brace escapes. Other brace syntax selects the default `artist - title` layout and logs a warning. Positional fields, index and attribute access, conversions, and format specs no longer render.
- `DiscogsAdapter.fetch` accepts only numeric release IDs without a leading zero. An unconfigured adapter still returns an empty list.
- `OllamaBootstrapService.pull_model` accepts only `[host/][namespace/]model:tag` references. Each part starts with a letter or digit and contains no run of two dots.
- The Discogs, MusicBrainz, and AcoustID HTTP clients cap each response at 5 MiB.
- ID3 writes remove an existing `TXXX` frame with the same description before adding the new value.
- Added `tests/unit/test_security_review_remediation.py`.

## [0.1.0] (2026-10-28)

### Added

- Added the `ci` workflow. It runs the review wiring tests, Ruff lint/style checks, and multi-OS test matrix on every pull request and push.
- Added the foucault security review caller pinned to foucault 3.4.7 (`c398b5a29890ba01dee5065a7dc97a91e3e2b08b`).
- Added the euler quality review caller pinned to euler 0.6.5 (`1a3f67cba9d6ed7f45af4822f9ca27dd14db6a46`).
- Added verbatim copies of the foucault review adapter files.
- Added `tests/test_review_wiring.py` for pins, triggers, secrets, fork handling, action pins, and adapter digests.
- Added `docs/pr-review.md` with the wiring, settings, and upgrade steps.
- **Milestone 1 (Foundation)**:
  - Technical Decision Record (`docs/technical-decision-record.md`) and `pyproject.toml` with pinned dependencies.
  - Domain models (`SourceGroup`, `FileAsset`, `ReleaseDetails`, `CandidateMatch`, `FieldProposal`, `AnalysisResult`, `ApprovedField`, `ChangeRecord`) and canonical key formatting (`Standard`, `Camelot`, `Open Key`).
  - SQLite database schema (`src/infiniteprep/db/schema.sql`) and `Repository` with natural-key idempotency and locked field protection.
  - `MutagenAdapter` supporting MP3, FLAC, WAV (`ID3v2.3` + RIFF `LIST-INFO`), and AIFF tag inspection and writing while preserving `PRIV:TRAKTOR4` / `TRAKTOR4`.
  - Non-destructive `ImportService` with bounded directory discovery, SHA-256 duplicate detection, and Pioneer XDJ-RX3 hardware compatibility warnings.
- **Milestone 2 (Proposal Workflow & Desktop UI)**:
  - Offline mock providers (`MockFingerprintAdapter`, `MockCatalogProviderAdapter`, `MockAudioAnalyzerAdapter`, `MockReasoningAdapter`).
  - `ReviewService` supporting proposal acceptance, rejection, manual edits, field locking, non-conflicting bulk approval, and `SourceGroup` release propagation.
  - `PySide6` desktop UI (`MainWindow`, `DetailPanel`, and `AudioPreviewWidget` with waveform overview bar and `Intro`, `25%`, `50% Drop`, `75%`, `Outro` needle-drop buttons).
- **Milestone 3 (Identification & Job Orchestration)**:
  - `SettingsManager` with SSRF endpoint validation and `CredentialStore` backed by OS `keyring`.
  - Consent-gated, SHA-256 verified `FpcalcBootstrapService` and `ChromaprintAcoustIDAdapter`.
  - `MusicBrainzAdapter` (`ws/2`) with `1 req/sec` rate limiting and SQLite response caching.
  - Durable `JobOrchestrator` coordinating fingerprinting, catalog enrichment, and local audio analysis.
- **Milestone 4 (DJ Audio Analysis & Rip Diagnostics)**:
  - Built-in BSD-compatible `LocalDspAnalyzer` computing BPM, Krumhansl-Kessler chroma musical key, waveform overview envelope, digital clipping warnings, DC offset warnings, and silence warnings.
  - Optional external `EssentiaCliAnalyzer` subprocess adapter (`shell=False`).
- **Milestone 5 (Reconciliation, Discogs, Optional LLM & Artwork)**:
  - `DiscogsAdapter` with 6-hour TTL cache and vinyl position (`A1`, `B2`) extraction, plus `ACRCloudOptInAdapter` and `BeatportStubAdapter`.
  - `ClosedWorldValidator`, `OllamaOpenAIReasoningAdapter` (`qwen2.5:3b` default), and consent-gated `OllamaBootstrapService`.
  - `ArtworkService` scaling cover art to `<= 800x800` baseline RGB JPEG for Pioneer XDJ-RX3.
  - `MatchingEngine` and `ReconciliationService` implementing all 10 reconciliation rules and Vinyl Turntable Pitch Drift detection.
- **Milestone 6 (Safe Application, XDJ-RX3 Transcoder & Restore)**:
  - `SafeTranscoderAdapter` (`SIBLING_EXPORT` default + `IN_PLACE_BACKED_UP` mode) with presets `XDJ_RX3_LOSSLESS_AUTO`, `FORCE_WAV_48K_24B`, `FORCE_WAV_44K_16B`, and `MP3_320_CBR`.
  - `PlaylistExporter` for UTF-8 `.m3u8` crate export.
  - `WriteService` with preflight SHA-256 verification, filename collision prevention, full file backups, `ChangeRecord` journal, and one-click verified restore.
  - `WritePlanDialog`, `JournalDialog`, and `SettingsDialog`.
- **Milestone 7 (Pilot Packaging & Evaluation Harness)**:
  - `src/infiniteprep/main.py` desktop launcher and `infiniteprep evaluate-sample` CLI evaluation harness.
  - `infiniteprep.spec` PyInstaller configuration and full end-to-end test suite.
