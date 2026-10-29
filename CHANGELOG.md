# Changelog

This file documents every notable project change.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] (2026-10-29)

### Added

- Adopted the `abuzucom/agents` policy at commit `b452348`. `AGENTS.md` holds the canonical policy. `scripts/sync.py` generates the tool copies.
- Added the complete gate set under `hooks/` with Claude Code, Codex, Gemini, and Antigravity registrations.
- Added the agents checkers under `scripts/` and the agents test suites under `tests/`.
- Added `shared-files.json`, `hook-coverage-baseline.json`, `.local-path-allowlist`, and `.pre-commit-config.yaml`.
- Added the `agents-compliance`, `agents-md-compliance`, `sync-check`, `immutable-conflict-check`, and `gate-integrity` workflows.
- Added the `agents-lint` job to the `ci` workflow.
- Added `SECURITY.md`, `CONTRIBUTING.md`, `docs/project-orientation.md`, and `docs/template-drift.md`.

### Changed

- Ruff skips the copied agents gate files. The `agents-lint` job and `scripts/run_tests.py` cover those files.
- `.gitattributes` pins LF line endings for the policy files.

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
