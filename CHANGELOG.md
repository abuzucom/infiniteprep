# Changelog

This file documents every notable project change.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.51.2] (2026-11-09)

### Added

- Each package build creates a CycloneDX SBOM from its locked Python environment. The SBOM tool runs in an isolated temporary environment and uploads a separate artifact for each target OS.

- `services/usb/macos.py` with read-only macOS drive inspection (`MacOSDriveInspector`), using fixed argument arrays for `diskutil list -plist` and `diskutil info -plist` without shell interpolation and `plistlib` parsing to record the same fields as the Linux inspector (USB-2, #154).
- Untrusted `diskutil` reply sanitization in `services/usb/macos.py` validating device identifiers and partition structure before any `diskutil info` invocation, filtering traversal mount points, truncating overlong labels, normalizing `MS-DOS FAT32` filesystem names, and deriving partition numbers from `diskNsM` identifiers (#154).
- APFS container correlation in `services/usb/macos.py` attaching container volumes to every owning physical drive through `APFSPhysicalStores` and skipping synthesized container entries as drives (#154).
- `MacOSDriveInspector` accepts an optional `timeout_seconds` override and a bounded default runner that discards stdout above `MAX_PLIST_OUTPUT_BYTES` during capture (#154).
- Unit tests in `tests/unit/test_usb_macos_inspection.py` covering recorded plists for a FAT32 stick, an exFAT stick, an APFS system disk with a synthesized container entry, a legacy APFS volume shape, and malformed replies, plus command failure, timeout, traversal rejection, oversized-reply rejection, targeted device resolution, platform guarding, and fixed-argv guarantees (#154).

### Changed

- Extract shared inspector helpers into `services/usb/_shared.py` (`clean_str`, `clean_int`, `sanitize_mount_point`, `default_command_runner`). The Linux inspector keeps its behavior.

### Fixed

- `MACOS_DEVICE_SAFE_RE` in `services/usb/macos.py` accepts only whole disks and single-suffix partitions. `_partition_number_from_identifier` documents that APFS container volumes keep the container volume index (#154).
- `bounded_command_runner` in `services/usb/macos.py` reads stdout and stderr through chunked capped pipe readers under one deadline. Replies never buffer beyond the caps. Stderr no longer spools to disk. The runner validates its timeout, runs the child in a POSIX session and kills the group on timeout, clamps every wait to nonnegative values, tolerates child exit races and stuck reaps, and closes pipes with guarded suppression (#154).
- `_parse_plist_dict` in `services/usb/macos.py` keeps the byte cap for injected command runners and raises `InspectionParseError` instead of `UnicodeEncodeError` on undecodable output (#154).
- `_build_drive_identity` in `services/usb/macos.py` maps the `Vendor`, `Manufacturer`, and `SerialNumber` info keys onto the drive identity when present (#154).
- `default_command_runner` in `services/usb/_shared.py` decodes output with `errors="replace"` so invalid UTF-8 no longer raises (#154).
- `bounded_command_runner` in `services/usb/macos.py` bounds the stdout read and every reap wait under a deadline, terminates stuck children with escalating terminate and kill signals, tolerates exit races, caps stderr at `MAX_STDERR_BYTES`, and reaps the child on every failure path (#154).
- `services/usb/macos.py` caps accepted `diskutil` stdout at `MAX_PLIST_OUTPUT_BYTES` (8 MiB) before `plistlib` parsing and raises `InspectionParseError` on oversized replies (#154).
- `inspect_device` in `services/usb/macos.py` runs `diskutil info` only for the matching drive and its mounted volumes instead of every disk (#154).

### Added

- Each package build creates a CycloneDX SBOM from its locked Python environment. The SBOM tool runs in an isolated temporary environment and uploads a separate artifact for each target OS.

- `services/usb/` package with read-only Linux drive inspection (`LinuxDriveInspector`), using fixed argument arrays for `lsblk` and `df` without shell interpolation (`shell=False`) to record drive identity, partition layout, filesystem attributes, and free space (USB-1, #153).
- Immutable `@dataclass(frozen=True)` models for drive identity, partition metadata, space statistics, and inspection records, with helper properties for FAT32 and exFAT detection.
- Untrusted USB device metadata sanitization in `services/usb/linux.py` stripping ANSI escape sequences, control characters, and NUL bytes, enforcing path traversal protections, validating mapping types for block devices and partitions, and resolving partition numbers from PARTN columns (#153).
- Unit tests in `tests/unit/test_usb_linux_inspection.py` covering recorded outputs for FAT32 and exFAT removable drives, a multi-partition system disk, untrusted metadata sanitization, and malformed command replies.

### Changed

- Update `platformdirs` from 4.14.2 to 4.14.5.
- Update `MarkupSafe` from 3.0.2 to 3.0.4.
- Update the development tool `ruff` from 0.17.3 to 0.17.4.
- Resolve Ruff and pytest CI versions from `requirements-lock.txt`.
- Pin `github/codeql-action/upload-sarif` to v4.39.3.
- Pin `actions/configure-pages` to v6.0.0.
- Pin `actions/upload-pages-artifact` to v5.0.0.

## [0.48.3] (2026-11-08)

### Added

- A pushed `v*` tag runs build-packages as a release. The tag must match `__version__`, `pyproject.toml`, and the top CHANGELOG entry, and must point to a commit on main. A release job then uploads the six packages and `SHA256SUMS.txt` to a draft release. Only the owner publishes it.
- A rerun of the release job reuses the draft. An asset already on the draft must match the new SHA-256, or the job stops. A draft asset that this build does not make also stops the job. A published release is never changed.
- `packaging/release.py` checks the tag, writes `SHA256SUMS.txt`, and writes the release notes from the CHANGELOG entry.

- `packaging/check_bundle.py` checks each built bundle before packaging. Every file must match a pattern in `packaging/bundle-allowlist/<os>.txt`. Qt tools, GPL-3-only Qt modules, and GNU Readline always fail the check. LICENSE, NOTICE, and ICU-LICENSE.txt must sit at the bundle root. Dangling symlinks count as files. A broken link cannot skip the check.
- build-packages fetches the ICU 73.2 license from the `release-73-2` tag and checks its pinned SHA-256. Every bundle and package ships the file as ICU-LICENSE.txt.
- NOTICE names the LGPL source locations for Qt, PySide6, and shiboken6. A three-year written offer covers the LGPL-2.1 components. Requests go to legal@infiniteprep.dev or a GitHub issue.

### Changed

- The `security-review-pr` and `quality-review-pr` workflows ignore `main` branch completion events on the `ci` workflow_run trigger, suppressing redundant post-merge review workflow runs on `main`.
- `tests/test_review_wiring.py` and `tests/test_push_event_workflow_wiring.py` check that `security-review-pr` and `quality-review-pr` specify `branches-ignore: [main]` on `workflow_run`.
- The deploy-pages workflow filters pushes to main with `website/**` and the workflow file path. Merged pull requests that do not touch website files skip deployment.
- `test_pages_deployment_workflow.py` checks that the push trigger specifies the path filters.
- NOTICE lists ICU 73.2 and the further Qt libraries that the bundles carry.
- NOTICE names GPL-2.0-or-later as the second option of the D-Bus and FreeType dual licenses. It names LGPL-2.0-or-later for libkeyutils.
- NOTICE names BSD-3-Clause as the chosen option of the zstd dual license.
- NOTICE adds the FreeType credit line and the libbsd four-clause BSD acknowledgments.

### Fixed

- The release job checks the six package names for the tag version. A missing or extra file stops the job and names the file.
- The tag check reports a missing version file, an unparsable `pyproject.toml`, or an empty version as a problem line instead of a traceback. The tag `v` alone no longer passes.
- The release notes no longer crash when the last CHANGELOG heading has no line break after it.
- The release upload compares each draft asset in a new temporary folder and deletes nothing.

- Qt files that no bundled binary loads leave the packaged builds. QtPdf and its PDF image plugin leave every bundle. The two EGLFS libraries and the unused PySide6 QtConcurrent and QtDBus bindings leave the Linux bundle. QML and Quick leave the macOS bundle, and the Windows build tries the same.
- The build stops on every OS when a kept binary still links a dropped Qt file. The log names each binary and the link.
- The build excludes setuptools and pkg_resources. No app module imports either package.
- The bundle smoke check fails with exit code 1 when Qt Multimedia finds no playback backend. Every packaged build now proves that the FFmpeg playback plugin loads.

- The CodeQL configuration lists the `packaging` folder. Code scanning now covers the bundle filters.
- The spec loads `packaging/bundle_filters.py` by exact file path and no longer changes `sys.path`. A different file named `bundle_filters` cannot replace the filters during a build.
- NOTICE lists the 72 Ubuntu 22.04 system libraries that the Linux bundle still carries after the trim, each with its license.

## [0.46.1] (2026-11-07)

### Fixed

- Packaged builds no longer ship GPL-3 code the app never uses (D302).
  - Qt Virtual Keyboard left every bundle. That covers its libraries, frameworks, QML module, and input plugin.
  - The spec excludes Python's `readline` module. GNU Readline left the Linux bundle.
- The Linux bundle drops Qt plugins the app does not use (D303). These are the GTK 3 theme, network information, EGLFS, LinuxFB, VNC, Vulkan display, minimal EGL, the evdev and TUIO input plugins, and the Adwaita Wayland decoration.
- The spec then removes each top-level Linux library that no kept binary links against, until nothing more changes (D304). The build log names every removed file and every kept library.
- The Linux job starts the built app on X11 under Xvfb. A library that only a real display needs cannot go missing unnoticed. CI installs the `xvfb` package for it (Rule 9 sign-off).
- New file `packaging/bundle_filters.py` holds the filters. New test file: `test_bundle_exclusions.py`.

- The manual `build-packages` workflow builds on the pinned `macos-15` image. GitHub no longer supports `macos-14`. GitHub cancelled the macOS job on that image before it started.

### Added

- A macOS app bundle and DMG from the manual `build-packages` workflow (D83a3c).
  - The PyInstaller spec builds `InfinitePrep.app` on macOS with the bundle identifier `dev.infiniteprep.app` and a macOS 14.4 minimum.
  - `InfinitePrep-<version>-macos-arm64.dmg` holds the app, an `Applications` shortcut, LICENSE, and NOTICE. The app also carries both notices in `Contents/Resources`.
  - The app has an ad-hoc signature. That signature needs no Apple certificate. macOS blocks the first launch of a downloaded ad-hoc app until a person allows it in System Settings. Developer ID signing and notarization wait for D82.
  - The macOS job mounts the DMG, checks its contents, the signature, the bundle identifier, and the minimum version, and runs the smoke check from the mounted app.
- New test file: `test_macos_packaging.py`.
- Settings has a `Delete InfinitePrep Data...` button on every OS (D83a3b).
  - Two unchecked boxes choose what goes. One covers the library, settings, recovery backups, waveform cache, and downloaded fpcalc. The other covers the saved API keys.
  - The dialog lists each folder it deletes. A final prompt names everything again and defaults to No.
  - InfinitePrep then closes its window and database, deletes the chosen items, lists what it removed and what remains, and quits.
  - The cleanup checks each folder's own name and removes only InfinitePrep folders. It never touches music files or exports.
  - InfinitePrep quits after the cleanup even when an unexpected error interrupts it.
  - While an import keeps the window open, the cleanup deletes nothing and says so.
- New test files: `test_data_cleanup.py` and `test_cleanup_dialog.py`.

- A macOS app bundle with a `portable.mode` file next to `InfinitePrep.app` keeps its database and settings in an `InfinitePrep-Data` folder there (D83a3a).
  - Without the file, the app uses the per-user folders in `~/Library/Application Support`.
  - A `portable.mode` file inside the bundle has no effect. The app never writes inside `InfinitePrep.app`.
  - macOS runs a downloaded app from a hidden read-only copy until the app moves out of Downloads. That copy sees no `portable.mode` file and uses the per-user folders.
  - When the app cannot write to the folder, the `Use my user profile` or `Quit` prompt appears, with `Remember`.
- New test file: `test_storage_macos_bundle.py`.
- Linux packages from the manual `build-packages` workflow (D83a2b).
  - `InfinitePrep-<version>-linux-x86_64.AppImage` keeps its library in `InfinitePrep-Data` beside the file. It uses a plain placeholder icon for now.
  - `InfinitePrep-<version>-linux-x86_64.tar.gz` uses the per-user folders.
  - `InfinitePrep-<version>-linux-x86_64-portable.tar.gz` carries `portable.mode`.
  - Each tarball unpacks into one `InfinitePrep-<version>` folder with LICENSE and NOTICE.
  - CI downloads linuxdeploy, its AppImage plugin, and AppImage runtime 20251108, and checks each pinned SHA-256 before use (Rule 9 sign-off). The build uses the pinned runtime and fails if it downloads another.
  - The Linux job unpacks both tarballs, checks the marker and notices, and runs the smoke check on each package.
  - The AppImage steps take the output path and the desktop file from `GITHUB_WORKSPACE`. A later folder change in the job cannot move the AppImage out of `dist/`.
- NOTICE lists the AppImage runtime and the libraries it links statically, including libfuse 3.15.0 under LGPL-2.1, with source links for relinking.
- New test file: `test_linux_packaging.py`.
- A Linux AppImage keeps its database and settings in an `InfinitePrep-Data` folder beside the `.AppImage` file (D83a2a).
  - The folder name stays the same across versions. A newer AppImage in the same place opens the same library.
  - Caches and recovery backups stay in the per-user folders, as on Windows.
  - When the app cannot write to that folder, the `Use my user profile` or `Quit` prompt appears, with `Remember`.
  - The app trusts the AppImage location only when its own executable runs from the AppImage mount. A copy started from inside another AppImage keeps its own storage.
  - A Linux tar.gz copy uses the `portable.mode` file like the Windows ZIP. It needs no code change.
- New test file: `test_storage_appimage.py`.

- Windows packages from the manual `build-packages` workflow (D83a1b).
  - `InfinitePrep-<version>-windows-x64-portable.zip` holds the bundle with `portable.mode`, LICENSE, and NOTICE.
  - `InfinitePrep-<version>-windows-x64-setup.exe` is a per-user NSIS installer that needs no admin rights. It installs to `%LocalAppData%\Programs\InfinitePrep`, apart from the data root `%LocalAppData%\InfinitePrep`.
  - The installer adds a Start Menu shortcut, an optional desktop shortcut that starts unchecked, and an Apps and Features entry.
  - An earlier installation leads to a prompt to uninstall it and continue. That uninstall keeps the library and saved keys.
  - The uninstaller keeps all data by default. One unchecked box deletes the library, settings, backups, and cache. A second unchecked box deletes the saved API keys. Music files and exports are never touched.
  - CI downloads NSIS 3.13 and checks its pinned SHA-256 before use (Rule 9 sign-off). The Windows job installs the built setup silently, runs the installed app's smoke check, checks the shortcut and the Apps and Features entry, and uninstalls silently with the data kept.
- Every bundle now carries LICENSE and NOTICE beside the executable.
- A hidden `--forget-credentials` flag removes every key InfinitePrep saved in the OS keyring. The uninstaller's saved-keys box runs it.
- New tests: `test_windows_packaging.py` and `test_forget_credentials.py`.
- Portable mode for packaged builds (D83a1a).
  - A `portable.mode` file beside the executable keeps the database and settings in a `Data` folder there. Caches, recovery backups, and the downloaded fpcalc stay in the per-user folders. Credentials stay in the system keyring.
  - Only a packaged build looks for the file. A run from source always uses the per-user folders.
  - A portable copy starts with its own empty library. It never reads or copies the per-user one.
  - When the app cannot write to `Data`, a prompt offers `Use my user profile` or `Quit`, with a `Remember` checkbox. A remembered choice clears itself once the app can write to `Data` again.
  - The write check uses a uniquely named temporary file that deletes itself. It never touches a file already in `Data`.
- When the library folder or database cannot open at startup, a plain-text message names the cause. InfinitePrep then exits with code 1 instead of stopping with a traceback.
- `src/infiniteprep/storage_paths.py` chooses every storage folder once at startup. Settings, backups, the fpcalc tool, and the waveform cache take the default folder from it.
- Five new test files cover portable mode:
  - `test_startup_storage.py`
  - `test_storage_paths.py`
  - `test_storage_probe.py`
  - `test_storage_defaults.py`
  - `test_storage_prompt.py`
- A manual `build-packages` workflow builds the PyInstaller bundle on each target OS (D83a0).
  - The jobs run on pinned `windows-2025`, `macos-15`, and `ubuntu-22.04` images with Python 3.12. The Linux baseline is glibc 2.35.
  - Each job runs `pip check` after the install. Drift between `requirements-lock.txt` and `pyproject.toml` fails the build.
  - Each job runs the built app's smoke check and fails when the written version does not match.
  - Each job uploads its bundle with `actions/upload-artifact` v7.0.2, pinned by commit. GitHub deletes each upload after 3 days.
  - The workflow runs only when started by hand. A later D83a change adds the release tag trigger.
- A hidden `--smoke-check <path>` flag loads numpy, scipy, soundfile, keyring, and Qt Multimedia on the offscreen platform. It then writes the version to the path and exits 0. The windowed Windows build has no stdout. The result therefore goes to a file. The flag writes only a new file and refuses an existing file or a directory.
- Three new test files cover the D83a0 changes and the fix below:
  - `tests/test_build_packages_workflow.py`
  - `tests/unit/test_smoke_check.py`
  - `tests/unit/test_folder_import_progress_reentrancy.py`
- The detail panel lists every `FieldName`. Track number, disc number, style, and the AcoustID, MusicBrainz, and Discogs IDs join the earlier twelve fields.
- A read-only Other Tags table under the fields table lists each raw custom tag from the file (D242).
  - Rows sort by key, ignoring case.
  - Keys that start with an underscore belong to the app and stay hidden (D245).
  - Binary values show the byte count and SHA-256. Values over 200 characters show cut short, with the full text in a tooltip. The cut never splits a visible character such as an accented letter or a joined emoji.
  - The tooltip shows the tag text literally. A value that looks like HTML, such as an `img` tag, never renders as markup or loads anything.
- `src/infiniteprep/user_agent.py` defines one `USER_AGENT` from the app version and `https://github.com/abuzucom/infiniteprep` (D221). It carries no email address.
- `src/infiniteprep/qt_logging.py` adds `qt.multimedia.ffmpeg*=false` to `QT_LOGGING_RULES` before `QApplication` starts. Rules already set stay in place.
- Six new test files cover the changes:
  - `test_ui_detail_panel_refresh.py`
  - `test_qt_logging_rules.py`
  - `test_user_agent.py`
  - `test_ui_detail_panel_audio_identity.py`
  - `test_ui_detail_panel_truncation.py`
  - `test_ui_detail_panel_tooltip.py`
- A `deploy-pages` workflow publishes the landing site to GitHub Pages. The workflow runs on every push to `main` and on demand from the Actions tab.
- Every action in that workflow names a full commit and the release that the commit carries.
- New test file: `test_pages_deployment_workflow.py`. It checks that the workflow publishes the site folder and no project document.
- A GitHub Pages landing page in `website/index.html` describes the current desktop workflow and the project's planned direction.
- `website/robots.txt` sets crawler preferences for AI training crawlers. The landing page carries noindex metadata.
- `website/CNAME` sets `infiniteprep.dev` as the GitHub Pages custom domain.

### Changed

- A key notation change, an accepted proposal, a saved edit, or a lock toggle refreshes the detail tables only. The preview player keeps its track, position, and play state. Only a different file ID loads new audio. A new stored path under the same file ID keeps the loaded track (D246).
- MusicBrainz, Discogs, AcoustID, and the fpcalc download send the shared `USER_AGENT`. `MB_USER_AGENT` and `DISCOGS_USER_AGENT` keep the same names and now hold that value.
- The FFmpeg backend of Qt Multimedia no longer writes decoder messages to the console.
- The site source moves from `docs/` to `website/`. The `docs/` folder keeps the project documents.
- The deployment uploads the `website/` folder alone. The project documents stay out of the published site.
- The package version moves to 0.42.0.

### Fixed

- A folder import that finished during a progress update could crash the progress dialog. `QProgressDialog.setValue` processes pending events on a modal dialog. A queued finish could clear the dialog inside that call. The update now sets the label first and calls `setValue` last.

## [0.34.0] (2026-11-06)

### Removed

- InfinitePrep drops the optional `EssentiaCliAnalyzer` subprocess adapter. Essentia carries the AGPL-3.0 license. The app runs no Essentia binary, including a user-installed one. `LocalDspAnalyzer` stays the only analyzer.
  - This release deletes `adapters/analyzers/essentia_ext.py` and its test.
  - `AppSettings` drops `prefer_essentia_cli` and `essentia_cli_path`. An older settings file with those fields still loads. The next save omits both fields.
  - Code that imports `EssentiaCliAnalyzer` or reads the two settings fields now fails. This change breaks compatibility.
- The README and `docs/technical-decision-record.md` no longer list the Essentia adapter.

### Added

- `tests/unit/test_essentia_removal.py` covers the removal and the load of an older settings file.
- A new `NOTICE` file lists each third-party component the app ships or downloads, with its license.
- Section 2.7 of `docs/technical-decision-record.md` records the 2026-11-06 copyleft scan.
  - A CI workflow read every pinned native wheel for Windows, macOS, and Linux, plus the pinned fpcalc archives.
  - No GPL or AGPL code ships in the parts InfinitePrep uses. The GCC runtime libraries carry the GCC runtime exception.
  - The PySide6 wheels carry GPL-3-only Qt modules and GPL-3 Qt tools. The app imports no such module and runs no such tool.
- Folder import runs on a worker thread (D78b1).
  - A modal progress dialog shows the current file, its number, and a Cancel button. Playback keeps running.
  - The scan runs first. Every file then imports in one database transaction, with a savepoint per file.
  - A file that fails with any error stays out and appears in the report, and the rest of the batch commits. An untrusted file can make a parser raise any error type. The log keeps the traceback.
  - The worker closes its own database connection before it reports. The window never waits on that close. The window's own connection stays open.
  - Cancel finishes the current file, then rolls back the whole batch. A crash also leaves nothing from the batch.
  - A report dialog shows the result, a count per category, and expandable per-file lists. Copy puts the report on the clipboard as plain text.
  - Closing the window during an import asks whether to wait for the commit or cancel. Either choice closes the window once the batch ends.
  - The progress label and the report headline show file names as plain text. A name that looks like HTML never renders as rich text.
  - A failed import shows a fixed message with no exception text.
  - A Wait or Cancel choice holds for the rest of the import. Later close requests wait without a new prompt.
  - One file or the scan running past 2 minutes raises an Import Not Responding prompt. Quit Now ends InfinitePrep at once and keeps nothing from the batch. Keep Waiting restarts the limit.
- `Repository.batch()` holds every write on one thread in a single transaction. `RepositoryBatch.savepoint()` scopes a rollback to one item.
  - An item whose savepoint cannot roll back raises `BatchIntegrityError`, and the whole batch rolls back. No partial item reaches the commit.
  - A failed batch rollback logs a warning and keeps the original error. SQLite discards the uncommitted batch when the connection closes.
  - A batch refuses to start on a connection with an uncommitted transaction. It never absorbs or discards other work.
  - Each savepoint scope gets its own generated name. No scope can collide with another.
- `ImportService.import_paths` takes optional `progress` and `should_cancel` callbacks. `ImportBatchReport` adds `failed_files`, `scanned_count`, and `outcome`.
- Added `tests/unit/test_import_batch_transaction.py` and `tests/unit/test_ui_folder_import.py`.

### Changed

- The package version moves to 0.34.0.
- Without callbacks, `ImportService.import_paths` also imports in one transaction. A file that raises any error now appears in `failed_files` and no longer stops the call.
- Item 4 of the decision record no longer claims that no runtime dependency carries a copyleft license. PySide6, Qt, and libsndfile carry LGPL licenses.
- A new item 5 in the decision record records that user installs come only from the packaged bundles, never from a pip install.

## [0.32.0] (2026-11-05)

### Changed

- Track duration cells now show whole-second `M:SS` or `H:MM:SS` values.
- Player clocks now show milliseconds and include hours for long tracks.
- File, detail, journal, write-plan, and recovery tables now support manual
  column resizing with the final column stretched.
- File and detail table layouts now persist across application restarts.

## [0.30.2] (2026-11-04)

### Changed

- The workflow action pins move to the versions that Dependabot proposed.
  - `actions/checkout` moves to `3d3c42e5`. The new pin names v7.0.1. The old pin named v4.4.0 and carried a stale `# v4.2.2` comment.
  - `actions/dependency-review-action` moves to `a1d282b3`. The new pin names v5.0.0.
  - `actions/github-script` moves to `3a2844b7`. The new pin names v9.0.0.
  - `github/codeql-action/upload-sarif` moves to `9b325408`. The new pin names v4.39.1.
  - `actions/setup-python` moves to `6d4fd22b`. The new pin names v7.1.0. The old pins named v5.6.0 and v7.0.0. Dependabot created that update but the proposal limit held it back.
  - The reusable security review workflow moves to `308e9643`. The new pin names foucault 3.6.1. The reusable workflow keeps the same inputs and the same permissions. The adapter files under `ci/` are byte-identical at both commits.
  - The privileged workflow retains the base-trusted checkout and setup-python pins.
    `scripts/check_compliance_tree.py` retains the matching trusted schema.
  - `tests/test_security_scan_workflows.py` approves the new commits for the two scan actions.
  - `tests/test_review_wiring.py` pins the new reusable workflow commit. The adapter digests stay the same.
  - `tests/test_gate_pr_integrity_timeline.py` names the new `actions/github-script` pin.
  - `docs/pr-review.md` records the new foucault release and commit.
- `platformdirs` moves to 4.14.2 in `pyproject.toml` and `requirements-lock.txt`. That proposal merged on its own and left no changelog entry.

### Added

- `scripts/check_action_versions.py` resolves every workflow action pin to the upstream release that its commit names.
  - The check fails when a version comment names another release than the pinned commit carries.
  - The check fails when a pin names no release at all.
  - The `ci` workflow runs that check on every pull request and push.
- `tests/test_action_versions.py` covers that checker without contacting GitHub.

### Changed

- `.github/dependabot.yml` raises `open-pull-requests-limit` to ten.
  - The repository holds more distinct action references than a limit of five. The first run created six proposals and the limit held the sixth back.

### Fixed

- Both theme selectors use the shared default for settings without a theme field.
- Theme loading and theme tests follow the import placement rules.
- Theme selector regression tests exercise legacy settings in both windows.
- Immutable Compliance retains checkout v4.4.0 and setup-python v5.6.0.
  The base-branch checker requires these exact pins for privileged execution.
  Regression tests verify the trusted pins and rejection of untrusted upgrades.
- `docs/dependabot.md` records how the Dependabot proposals behave.
  - Dependabot updates a reusable workflow reference when the referenced workflow file changes.
    The earlier text said that the reference cannot move because no newer tag exists.
  - The `abuzucom/euler` repository left its quality review workflow unchanged. That reference has no proposal.
  - A proposal that moves a reusable workflow reference changes the `uses:` line only.
    The `audit_ref:` input beside it keeps the old commit.
  - A proposal needs no changelog entry. The version advance check skips the dependabot bot.
  - A proposal that changes a commit SHA leaves the version comment beside it stale.
  - The example version comment now names a version that the repository uses.

## [0.28.0] (2026-11-03)

### Added

- UI theme adapter supporting Material Design themes via `qt-material` and fallback to native styling.
- Theme selector in `MainWindow` toolbar and `SettingsDialog`.
- Runtime theme persistence in `AppSettings`.
- `.github/codeql/config.yml` configures the code scanning analysis of the GitHub default setup.
  - The `security-extended` query suite adds the lower severity and lower precision security queries to the queries the default setup already runs.
  - The `paths` list names every directory that holds analyzed code.
- `tests/test_codeql_configuration.py` checks the CodeQL configuration against the repository.
  - The test reads the tracked files, because the tracked tree is the tree that CodeQL analyzes.
  - The test fails when a directory holds Python source and the `paths` list omits it.
  - The test fails when the configuration drops the extended query suite.
  - The test fails when the `paths` list holds a glob or a parent directory segment.
  - The test fails when an advanced CodeQL workflow appears beside the default setup.
  - The test fails when the document drops the remote property value or names a file that does not exist.
- `docs/code-scanning.md` records the CodeQL setup, the repository property that applies the configuration, the verification steps, and the open options.
  - The `make lint` target checks the new document for prose violations.
- `.github/dependabot.yml` configures the Dependabot version updates.
  - The `pip` ecosystem covers `pyproject.toml`, `requirements-lock.txt`, and `requirements-checkers.txt`.
  - The `github-actions` ecosystem covers the action pins in the workflow files.
  - The patch updates and the minor updates form one group each. A major update arrives on its own.
  - The commit message prefix gives every proposal a subject in the repository format.
- `tests/test_dependabot_configuration.py` checks the Dependabot configuration against the repository.
  - The test fails when an ecosystem or a schedule disappears.
  - The test fails when a group collects a major update.
  - The test fails when the commit message prefix leaves the repository subject format.
  - The test fails when the branch name prefix leaves the `dependabot` prefix that the bot exemption recognizes.
- `docs/dependabot.md` records the proposal workflow, the red checks, and the review steps.
  - The `make lint` target checks the new document for prose violations.

### Fixed

- The `github-codeql-config-file` repository property must name a remote address.
  - A local path makes the configuration file mandatory on every analyzed ref.
  - The analysis of a pull request branch that predates the file fails with a missing configuration file.
  - The remote address reads the file from the `main` branch. Every ref analyzes successfully.
  - The property value is `remote=abuzucom/infiniteprep@main:.github/codeql/config.yml`.

## [0.25.0] (2026-11-02)

### Removed

- The app no longer depends on mutagen. `pyproject.toml`, `requirements-lock.txt`, and the `infiniteprep.spec` hidden imports drop it. Mutagen is GPL-2.0-or-later. A bundled build no longer carries its terms.

### Changed

- `MetadataTagAdapter` writes MP3, WAV, AIFF, and FLAC tags with the in-house codecs. The public methods stay the same.
  - An ID3 write keeps every frame outside the written fields, PRIV:TRAKTOR4 included. The tag renders as ID3v2.3 with UTF-16 text, as before. Every byte after an MP3 tag stays the same.
  - A YEAR write drops any old TYER, TDAT, and TIME frames before it writes the new date.
  - WAV writes still synchronize the RIFF LIST-INFO chunk.
  - FLAC writes keep every other Vorbis field and block. LABEL still also writes ORGANIZATION. A file without a Vorbis comment gets one whose vendor is `InfinitePrep` and the package version.
  - Every tag write refuses a file over 1 GiB with an error that names the limit, and the file stays unchanged. The writers read the whole file, as the WAV LIST-INFO step already did under the same cap.
- `MutagenAdapter` is now `MetadataTagAdapter`. The module moved to `adapters/metadata_io/metadata_tag_adapter.py`. The old class name and the old module path are gone. No shim keeps the old names working.
- The package version in `pyproject.toml` and `infiniteprep/__init__.py` moves from 0.1.0 to 0.25.0, matching this file.
- A v2.3 date reads as `YYYY-MM-DD` from TYER plus TDAT, with `THH:MM` from TIME when present, as mutagen read it. A full date then passes the write read-back check.
- `docs/technical-decision-record.md` names the in-house codecs for tag reading and writing. It also records that the runtime dependencies carry no copyleft library.
- Added `tests/unit/test_in_house_metadata_write.py`.
- A test fails when any module under `src/` imports a package outside the standard library, the first-party package, and the approved runtime dependencies. The check reads the approved list from `scripts/approved_runtime_dependencies.txt`. Added `tests/unit/test_dependency_allowlist.py`.
- `scripts/approved_runtime_dependencies.txt` lists the runtime packages the source tree may import. A test requires that list to match the runtime dependencies in `pyproject.toml`. A dependency added to `pyproject.toml` alone fails the build.
- The `TagWriter` port declares `detect_format` and `render_filename` now. `ImportService` calls `detect_format`, and the adapter offers both.
- `compute_sha256` moved to `src/infiniteprep/hashing.py`. A file hash helper no longer sits in the tag adapter module. Every importer now reads it from the new home.
- `docs/technical-decision-record.md` uses ASCII hyphens in its ranges now. The file carries no non-ASCII character.
- The bash tests skip when the runner's bash cannot run a script by its path. WSL bash reports every Windows path as missing. Those tests no longer fail on such a runner.
- The playlist mode test skips on a platform that does not carry POSIX file mode bits. Windows `chmod` only toggles the read-only attribute.

- `MetadataTagAdapter.read_metadata` reads every format with the in-house codecs instead of mutagen. Its signature and return type stay the same.
  - It maps the file read-only. A large WAV or AIFF then stays on disk while the reader takes its headers and tag chunk. The Ogg reader still loads the whole file.
  - A non-text ID3 frame, such as `PRIV:TRAKTOR4` or a cover picture, now appears in `raw_custom_tags` as `<PRIV TRAKTOR4, N bytes, sha256 ...>`. It no longer appears as a mutagen object representation. Cover pictures keep the `APIC:<description>` key. COMM and USLT frames read as text.
  - An MP3 without a Xing, Info, or VBRI header keeps constant-bitrate timing from its first frame.
  - An empty or unparseable file returns one "Damaged or unreadable audio container" warning, as before. An AIFF sample rate past 32 bits counts as damage.

### Added

- `adapters/metadata_io/audio_properties.py` reads duration, sample rate, channels, bitrate, and bit depth.
  - It covers MPEG frame headers (MPEG-1, 2, and 2.5, layers 1 to 3, with Xing, Info, and VBRI), WAV `fmt ` and `data` chunks, AIFF and AIFC `COMM` chunks, and FLAC STREAMINFO.
  - It walks only headers and never copies the audio.
  - Added `tests/unit/test_audio_properties.py` and `tests/unit/test_in_house_metadata_read.py`.
- `id3v2.decode_language_text` returns the text of a COMM or USLT frame. Added `tests/unit/test_id3v2_language_text.py`.

- `adapters/metadata_io/ogg_reader.py` reads metadata, codec parameters, and Vorbis comments
  from Ogg Vorbis and Ogg Opus audio bytes. Damage raises `OggFormatError`.
  No caller uses the module yet. Added `tests/unit/test_ogg_reader.py` and
  `tests/fixtures/ogg_bytes.py`.

- Spectrum waveform analysis streams bounded audio into low, mid, high, and overall peaks.
  Content-addressed compressed caches enforce a size cap. Overview sampling preserves peaks.
  Generated sine tests cover band separation, column counts, cache reuse, and eviction.

### Fixed

- Ogg parsing reports an invalid capture pattern before a truncated-header error for non-Ogg data.
  Ogg source formatting matches the pinned Ruff formatter.
- The ID3v2.3 render of a v2.4 timestamp handles three more cases.
  - Second 60, a UTC leap second, keeps TIME.
  - A fractional part after the seconds parses. TIME holds no seconds. The fraction drops.
  - A date that does not exist writes TYER alone. TIME no longer appears without its TDAT.
  - A Z or UTC offset still keeps the written wall-clock TIME.
  - Added `tests/unit/test_id3v2_codec_review_dates.py`.
- The v2.3 date rejoin names its field widths. The length checks and slices share the widths.
- Spectrum waveform arrays reject inconsistent dimensions or lengths.
- Zero and undersized cache budgets skip archive writes. Cache updates reserve space before
  publishing an archive. A failed eviction stops new writes. Regression tests cover these
  budgets, file growth during hashing, and immutable overviews with a peak in the final sample.
- Cache warnings report the exception type and OS error code without exposing source paths.
  Cache updates perform one eviction pass. Temporary archives use a shorter basename.

## [0.18.0] (2026-11-01)

### Changed

- `tests/fixtures/audio_factory.py` tags its WAV, AIFF, MP3, and FLAC fixtures with the spec-written byte builders in `tests/fixtures/` instead of mutagen. `tests/unit/test_security_review_remediation.py` and `tests/integration/test_milestone6_write_transcode_restore.py` read written tags with `id3_containers.py`, `id3v2.py`, and `flac_vorbis.py`. No test imports mutagen now. The adapter still uses mutagen until the adapter switch.

### Added

- `adapters/metadata_io/mp4_reader.py` reads iTunes-style tags and audio properties from MP4 and M4A bytes. It decodes text, number pairs, integers, freeform `----` items, and cover art sizes. Duration comes from `mvhd`. Channels, sample rate, and bitrate come from the first sound track, including ALAC above 65,535 Hz. A FairPlay or common-encryption sample entry sets `encrypted`. The module never writes, and no caller uses it yet. Added `tests/unit/test_mp4_reader.py` and `tests/fixtures/mp4_bytes.py`.

- `scripts/dev/update_and_check.cmd`, `scripts/dev/update_and_check.ps1`, and `scripts/dev/update_and_check.sh` pull `main`, refresh `.venv`, run every local check, print a PASS or FAIL summary, and launch the app when all checks pass. A dirty checkout or a branch other than `main` stops the run. `scripts/dev/make_test_copies.ps1` and `scripts/dev/make_test_copies.sh` copy a few files of each format into `scratch/test-library/`. Added `tests/unit/test_dev_scripts.py`.
- `.gitignore` keeps the tracked `infiniteprep.spec` and ignores local state, stray databases, music files, `.env` variants, and OS and editor files.

- `adapters/metadata_io/flac_vorbis.py` reads and writes the Vorbis comment block of FLAC bytes. Field names match without regard to case. Entries without `=` appear in `dropped_entries`. APPLICATION, SEEKTABLE, PICTURE, and other blocks stay byte for byte and in order. Padding absorbs size changes where it can. A leading ID3v2 tag and the audio frames stay byte for byte. No caller uses the module yet. Added `tests/unit/test_flac_vorbis.py` and `tests/fixtures/flac_bytes.py`.

- `adapters/metadata_io/id3_containers.py` locates, reads, and replaces the ID3v2 tag in MP3, WAV, and AIFF bytes. MP3 tags sit at the start of the file. WAV and AIFF tags sit in an `id3 ` or `ID3 ` chunk. Audio, other chunks, and a trailing ID3v1 tag stay byte for byte. A smaller tag fills the old tag's space. The audio offset then stays the same. No caller uses the module yet. Added `tests/unit/test_id3_containers.py` and `tests/fixtures/audio_container_bytes.py`.

- `adapters/metadata_io/id3v2.py` reads ID3v2.2, 2.3, and 2.4 tags and writes ID3v2.3. Unknown frames keep original bytes. No caller uses the module yet. Added `tests/unit/test_id3v2_codec.py`, `tests/unit/test_id3v2_codec_frame_paths.py`, `tests/unit/test_id3v2_codec_spec_conformance.py`, `tests/unit/test_id3v2_codec_dates.py`, `tests/unit/test_id3v2_codec_limits.py`, `tests/unit/test_id3v2_codec_v24_unsync.py`, `tests/unit/test_id3v2_codec_calendar.py`, and `tests/unit/test_id3v2_codec_header_flags.py`, with shared byte builders in `tests/fixtures/id3v2_bytes.py`.
- An ID3v2.4 tag header with the unsynchronisation flag reverses unsynchronisation in every frame body, once per frame.
- Compressed ID3v2 frames inflate to at most 16 MiB each and 64 MiB per tag. The codec drops frames past either cap and records dropped frame IDs in `dropped_frames`. `render_id3v23` raises `ValueError` for padding outside 0 to 64 KiB.
- ID3v2.3 output keeps a TDRC or TDOR frame whose value does not start with a year. A timestamp must match the whole v2.4 form, with an optional Z or offset after a time, or the frame stays as it is. TDAT needs a real calendar date, leap years included, and TIME needs a valid time of day. TIME keeps the written hour and minutes.

### Fixed

- `_split_language_frame` in `adapters/metadata_io/id3v2.py` validates the 4-byte header length before slicing language bytes and raises `Id3FormatError` on truncated bodies.
- String decoding in `adapters/metadata_io/id3v2.py` preserves detected UTF-16 endianness across null-separated values to prevent secondary string mojibake in big-endian frames.

## [0.12.1] (2026-10-31)

### Added

- README **Roadmap** section lists the planned stages in order.

- The audio preview has a **Playback position** slider with an accessible name and time. The arrow keys seek 1 second, and Page Up and Page Down seek 10 seconds.
- The audio preview shows playback failures and an unavailable audio backend as plain text under the transport. The Play button follows the real player state.
- `MainWindow` and `DetailPanel` accept an optional keyword-only `enable_audio_backend`, defaulting to False. Added `tests/unit/test_audio_preview_playback.py`.

- **Change Journal** asks before **Restore Backup** and names the file it replaces. When the file changed after the recorded write, a second question names the change before the restore overwrites anything.
- `WriteService.restore_operation` raises the new `RestoreConflictError` when the file changed after the write. The new optional `allow_modified` keyword restores anyway. Each restore first copies every file it replaces or removes to the backup folder as `{asset_id}_{id}_pre_restore_{filename}`.
- `MutagenAdapter.apply` accepts an optional `backup_path` keyword. `WriteService` passes the backup path it journaled. The journal and the written backup always agree.
- Added `tests/unit/test_verified_copy_writes.py`, `tests/unit/test_write_hardening.py`, `tests/unit/test_write_race_guards.py`, and `tests/unit/test_restore_guard.py`.

### Changed

- README **Capabilities** separates features the desktop application runs from modules that exist without a caller. **Current limitations** lists the known gaps in identification, analysis, writing, export, and the interface.
- A tag write goes to a copy next to the file. The copy must parse, keep its pre-write sample rate and channel count, and read back every written tag. It then atomically replaces the original. A crash or failed write leaves the original bytes in place. A successful write gives the file a new inode. `shutil.copy2` carries over the mode and timestamps, plus extended attributes on platforms with xattr support.
- Just before the swap, the write checks the source SHA-256 again. A file another program changed during the write keeps that change. The journal records the write as failed, and no rollback runs.
- Each tag-write backup takes the name `{asset_id}_{operation_id}_{filename}`. Each in-place transcode backup takes `{asset_id}_{operation_id}_transcode_{filename}`. A later backup of the same track no longer replaces an earlier one.
- Backups use exclusive file creation. A backup never overwrites an existing file. The app deletes a backup that fails its SHA-256 check. Backup names refuse any part that could leave the backup folder on any platform, such as a slash, backslash, colon, control character, or blank part.
- Four recovery test assertions now expect the original file intact after a crash mid-write. The maintainer approved the edit.

### Fixed

- The desktop app's audio preview plays sound. The launcher now turns on the Qt multimedia backend. Before, no caller turned it on, and **Play** only changed the button label.
- Write preflight refuses a symbolic link, a file with more than one hard link, or a file owned by another user. Swapping in a written copy would replace the link, split the hard links, or change the owner. The apply step checks again before the backup.

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
- Added `tests/unit/test_restore_interrupted_confinement.py`. **Restore from backup** refuses a tampered `previous_path` or `new_path` outside the library root, and no file changes.
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
