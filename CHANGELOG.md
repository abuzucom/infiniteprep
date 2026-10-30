# Changelog

This file documents every notable project change.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.0] (2026-10-30)

### Added

- At launch, the desktop app opens **Recover Interrupted Writes** when a tag write or in-place transcode left a journal record `IN_PROGRESS`. Each row offers **Restore from backup** or **Mark as finished**.
- `WriteService.list_interrupted_operations`, `restore_interrupted`, and `mark_interrupted_finished` back the dialog. A restore verifies the backup SHA-256 and records `ROLLED_BACK`. A failed restore leaves the record pending.
- `WriteService.apply_batch` and `apply_single_plan` raise `PendingRecoveryError` before any file change while an interrupted write is pending. **Preview & Apply Writes** shows the reason and stays open.
- Added `tests/unit/test_interrupted_write_recovery.py` and `tests/unit/test_ui_recovery_dialog.py`.

- `scripts/check_branch_name.py` rejects generated `<adjective>-<surname>-<suffix>` branch names such as `chore/dreamy-goldberg-nwjlsu`. Detection covers only the listed words. Added `tests/test_check_branch_name_generated.py`.

- Playlist export writes track paths relative to the playlist folder by default. A crate copied to another drive or computer with its music keeps working. A track on another Windows drive keeps its absolute path.
- A relative line that starts with `#` gains a `./` prefix. M3U8 players cannot read it as a comment or directive.
- `AppSettings.playlist_relative_paths` defaults to `True`. A settings file without the key loads that default. **Settings & Providers** adds the **Write paths relative to the playlist** checkbox.
- `PlaylistExporter.export_m3u8`, `export_m3u8_report`, and `export_playlist_with_feedback` accept an optional `relative_paths` keyword. It defaults to `False`. Existing callers keep absolute paths.
- Added `tests/unit/test_playlist_relative_paths.py`.

### Changed

- Upgraded the foucault security review caller to foucault 3.5.0 (`fbb09487cdbbe85ca64b2e8c46605c3120b62c54`). Oversized review input now routes to the `ollama-large` profile. A diff above every model budget receives a `NEEDS-HUMAN` verdict instead of no verdict.
- Recopied `ci/build_pr_case.py`, `ci/call_model.py`, and `ci/model_providers.json` from the pinned foucault commit.
- Updated the pinned commit and adapter digests in `tests/test_review_wiring.py`.
- Documented the large-diff behavior in `docs/pr-review.md`.
- Synchronized policy from `abuzucom/agents` 2.14.0. Branch preflight now runs `check_branch_name.py --strict-agent-preflight`. A harness-assigned branch receives no exemption. `docs/agent-policy/clients.md` discloses that a multi-repository Claude Code cloud session loads no repository hooks.

### Fixed

- A rollback that fails with any exception type keeps the original write error. The journal records the rollback failure, and the write error still reaches the caller.
- A failed rollback keeps its journal record `IN_PROGRESS`. The possibly half-written file then appears in **Recover Interrupted Writes**, and new writes stay blocked. An unreadable backup during rollback takes the same path.
- **Mark as finished** stores the file's current path and SHA-256 on its library entry. The next write on that file passes preflight without a re-import.
- Added `tests/unit/test_recovery_edge_cases.py`.

### Security

- **Mark as finished** checks both journal paths against the library root before it uses either one. A tampered journal record can no longer point a later tag write at a file outside the library. Added `tests/unit/test_recovery_path_confinement.py`.
- Each in-memory `DatabaseManager` gets a private database named with a random UUID. The name came from `id(self)`. Python can reuse that value after garbage collection. A new manager could then open a database that an old connection still held, and see its rows. Added `tests/unit/test_in_memory_database_isolation.py`.

## [0.5.0] (2026-10-29)

### Added

- Added the `dependency-review` workflow. It audits pull request dependency changes and fails on a high-severity vulnerability.
- Added the `security-devops` workflow. It runs Microsoft Security DevOps on pushes and pull requests to `main` and uploads the SARIF results to the Security tab.
- Added `tests/test_security_scan_workflows.py` for the approved action commits, permissions, and checkout credentials of both workflows.
- The main window adds **Export Playlist (.m3u8)**. It writes the visible rows to a chosen path through `PlaylistExporter` and appends a missing `.m3u8` suffix. A failed write shows a warning.
- `MainWindow` accepts an optional `on_export_playlist` callback.
- Added `tests/unit/test_ui_playlist_export_action.py`.
- `PlaylistExporter.export_m3u8_report` returns the written path, the written track count, and the skipped file IDs. `export_m3u8` still returns the written path. Added `tests/unit/test_playlist_exporter_safety.py`.
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

- `WriteService.apply_single_plan` saves an `IN_PROGRESS` journal record before any file change. A crash mid-write now leaves a journal entry that names the backup.
- A tag write that fails after its backup restores the original file from the verified backup and records `ROLLED_BACK`. A failure before the backup records `FAILED` and leaves the file untouched.
- `restore_operation` copies the backup beside the target, verifies its SHA-256 against the pre-write hash, and then swaps it in with `os.replace`. A failed swap keeps the current file and removes the staging copy.
- Added `tests/unit/test_journal_first_writes.py`.
- Added `tests/unit/test_unsafe_plan_side_effects.py`. An unsafe plan raises its preflight error before any backup file, file change, or journal entry exists.
- In-place **Transcode to XDJ** no longer fails with `AttributeError`. `OperationStatus` gains the `IN_PROGRESS` value that the in-place journal record uses before the replacement. Added `tests/unit/test_in_place_transcode_journal.py`.
- The `sync-check` suite steps drop `GITHUB_EVENT_PATH`. Push events to `main` no longer fail the compliance scanner tests in `tests-windows`, `tests-macos`, and `check-sync`.
- The `security-review-pr` `resolve-pr` job runs only for pull request `ci` runs. A push to `main` now skips the review instead of failing it.
- Added `tests/test_push_event_workflow_wiring.py` for both workflow guards.

### Security

- The playlist export leaves out a track whose stored or resolved path holds a line break, another C0 control character, or DEL. One entry can no longer split into extra playlist lines. The export dialog names each skipped track with its control characters shown as spaces. A stale file ID shows as an unknown track. Added `tests/unit/test_playlist_export_review_fixes.py`.
- The export button appends `.m3u8` to a name with another suffix instead of replacing that suffix. A chosen `set.v2` becomes `set.v2.m3u8`, and `set.M3U8` keeps its name.
- The playlist export opens its destination with `O_NOFOLLOW` where the OS defines it. A symlink at the destination raises an error and leaves the link target unchanged. A new playlist file gets mode `0o600`, readable and writable by its owner only. An overwritten playlist gets the same mode on POSIX. Added `tests/unit/test_playlist_overwrite_mode.py`.
- The playlist export dialogs render as plain text through `show_plain_message`. A destination or file name that holds HTML tags shows literally. `DialogRecorder` records the helper. Added `tests/unit/test_plain_text_dialogs.py`.
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
