# Changelog

This file documents every notable project change.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.94.0] (2026-11-27)

### Added

- M4A files accept tag writes (FMT-3, #104).
  - The new `adapters/metadata_io/mp4_writer.py` rebuilds the movie atom, keeps every item it does not write, and moves the chunk offsets of every track when the movie changes size. The media data keeps its bytes.
  - A free atom takes the size change where the file holds one. The media data then stays where it is.
  - The writer stores the artist, the title, the mix name, the remixer, the release title, the label, the catalog number, the track and disc numbers, the vinyl position, the genre, the style, the tempo, the key, the year, the Acoustid id, the MusicBrainz recording and release ids, and the Discogs release id. A field with no native atom becomes a freeform item in the iTunes namespace.
  - The writer rounds the tempo half up to the integer the format holds. The track and disc numbers use the native number pair atoms.
  - The writer refuses a DRM protected file, a fragmented file, a file with a top-level metadata atom, and a file with no chunk offset table. The refusal reaches the preview before any file changes.
  - The M4A locator joins the audio payload check. Every M4A tag write then compares the media digest before and after the write.
  - M4A joins the write-enabled formats. The reader maps every stored item back to its field.
- Regression tests cover the writer on AAC and ALAC fixtures, the payload digest, and every refusal.

### Changed

- The main window opens at a share of the available screen and restores saved geometry when available.
- A large-window layout gives the sidebar and track details more room from 1680 logical pixels.
- The waveform and playback controls stay visible above the library. The queue and workflow controls leave the remaining height to the library (UI-7, #362).
- Compact player spacing keeps the cue buttons visible at the minimum window size (UI-7, #362).

## [0.93.7] (2026-11-26)

### Added

- The queued Identify & Analyze batch runs the BPM and key analysis on worker processes (INFRA-2, #110).
  - The new `services/analysis_pool.py` starts at most four worker processes. The count never passes the processor count.
  - The new `services/analysis_worker.py` holds the entry point of one analysis task in a worker process.
  - The batch submits the files ahead of the current one. The workers stay busy while the thread reads the metadata.
  - The batch keeps its progress reports, its per-file failure rule, and its stop-on-failure rule.
  - A cancel stops the worker processes at once. The results of the files analyzed before the cancel stay in the library.
  - A worker that dies costs one file. The file runs once more on a worker of its own, and the batch keeps going.
  - Every result carries the analyzer version that wrote it.
- A stale flag marks the analysis results of an older analyzer version (INFRA-2, #110).
  - Migration 6 adds the flag to `analysis_results`.
  - The app flags the stored results when it starts. A track then reads as needing a new analysis.
  - The library shows a warning on such a track. The detail panel marks the row as outdated.
  - A stale result makes no proposal. It states no BPM and no key.
- The Analysis tab of the Settings dialog gains an "Analysis workers" count. The count bounds the worker processes of the analysis pool.
- Regression tests distinguish missing numeric metadata from zero in library searches and smart playlists.

- Library search accepts natural language and field comparisons. Saved searches create live smart playlists.

- `WorkflowBar` adds an Import DJ library tool button wired to `MainWindow` (IMPORT-1, #143).
  - The button stays disabled while the batch queue is busy.
  - `LibraryImportDialog` builds the import plan when advancing from the Map step to the Review step.
  - The Review step displays conflicts, pending values, and unresolved paths before commit.
  - Accepting the dialog commits the plan in one transaction and refreshes the library table.
  - `NoImporterError` and invalid roots display a plain-text warning and keep the dialog on the Map step.
- The agents policy template moves from `abuzucom/agents` `88f58b5` to `4797f14` (v2.46.0).
  - The template adds the session zone, single question, prose option, anti-delegation, pull request base, and GitHub MCP write hooks. Each hook brings its shared modules, checkers, and tests.
  - The template adds the client skills under `skills/`, `.claude/skills/`, and `.agents/skills/`, plus `docs/enforcement-matrix.md`.
  - The template adds `policy-size-report.yml`, `policy-digests.json`, and the test name checker `scripts/check_test_names.py`.
  - `docs/template-drift.md` lists the taken and declined files and the local differences.

### Fixed

- A track with a current analysis result shows no warning about a new analysis. The warning appears only while every stored result is stale.
- The detail panel names an outdated analysis instead of showing the BPM and key of an older analyzer version.
- A batch that stops at its first failed file ends at once. The prefetched analyses of the files ahead no longer delay the batch.
- A rollback to an earlier analyzer version makes the results of that version current again.
- A file that ended before a worker crash keeps its result. The pool no longer analyzes that file twice.
- The pool runs each file at most twice. The first run and one retry settle a file after a worker died.
- The crash recovery of the analysis pool observes a cancel request. A cancel ends the pool at once, also after a worker died.
- A file that takes longer than the time limit of the analysis pool ends as one failed file. The pool rebuilds for the files after it.
- A requested file that no longer exists in the library counts as one failed file. The batch reports it in the summary.
- A cancel stops the worker processes of the analysis pool only. A child process of another component stays untouched.
- The analysis pool logs an unexpected worker error with the file path and the traceback.

### Changed

- The analysis pool test records the number of analyses that wait for a worker. The count never passes the worker count of the pool.
- The analysis pool cancel test runs a slow analyzer. The cancel reaches the worker before the result arrives.
- The test suite now covers the desktop entrypoint, USB manifest confinement, library import boundaries, adapter fallbacks, credential storage, settings validation, and waveform worker outcomes.
- Every taken hook, checker, test, client setting, and policy document matches the template revision. The policy copies come from `scripts/sync.py`.
- The `pr-checks` job of `agents-compliance.yml` runs on draft pull requests. `agents-md-compliance.yml` has no branch filter and also runs on the `edited` event.
- `sync-check.yml` runs on pull requests only and adds the delegated recovery command check.
- Every workflow keeps its current action pins.
- Every test name follows the five-word rule of `scripts/check_test_names.py`. The renames change no test body.
- The README handoff note uses the template term "active human request".
- `README.md`, `docs/project-orientation.md`, and earlier `CHANGELOG.md` entries drop the wording that the agents prose checks flag. The facts, versions, and issue numbers stay the same.
- `security-review-pr.yml` calls the `abuzucom/agents-personas` `foucault-security-review.yml` wrapper. `quality-review-pr.yml` calls the `euler-quality-review.yml` wrapper.
  - Both pin `uses:` and `personas_ref` to agents-personas commit `c43f4592191b42b9f66072622b082fc2f4dc3217` (0.22.0).
  - The `abuzucom/foucault` and `abuzucom/euler` pins are gone.
- The review check runs carry the names `foucault / security` and `euler / quality`. Branch protection that requires `security-review` or `quality-review` needs the new names.
- The engine publishes the skipped check for a fork pull request. Each caller drops its fork job. The classify job keeps the same-repository guard.
- Every completed `ci` run gets a review. No existing check run skips it, since any workflow with `checks: write` can publish a check with any title.
- `tests/test_review_wiring.py` covers the wrapper pins, the permissions, the secret mapping, and the check names.
- `ci/README.md` kept the `ci` path that the CodeQL configuration declared. The default setup reads that configuration from `main`.
- The CodeQL configuration drops the `ci` path.
- Mounted device discovery in `locator.py` enforces containment inside the mount point (IMPORT-1, #143).
  - Every component beneath the mount point and the library file itself must be a real path resolving inside the mount point.
  - The mount point itself may remain an OS-managed mount link.
- Relocation hash caching in `relocation.py` keys cached digests by device, inode, size, and modification timestamp, and rechecks file metadata after reading (IMPORT-1, #143).
- File URL normalization and remainder extraction in `path_mapping.py` collapse slashes, normalize Windows drive letters, and split canonical segments (IMPORT-1, #143).
- The version advances from 0.88.0 to 0.93.7 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.

### Removed

- The foucault adapter copies `ci/build_pr_case.py`, `ci/build_retry_case.py`, `ci/call_model.py`, `ci/model_providers.json`, and `ci/run_model_command.py`. The agents-personas engine supplies the adapter.
- `scripts/check_pr_review_response.py`. The engine validates each reply.
- The `ci/call_model.py` entry in `hooks/workflow-scripts.txt`.
- `ci/README.md` and the `ci` directory. `main` no longer declares `ci` in the CodeQL configuration.

## [0.88.0] (2026-11-24)

### Added

- A shared framework imports a DJ library into the app (IMPORT-1, #143).
  - One importer interface reads any source format. EXPORT-7 calls the same interface with no user interface.
  - The merge compares every field against the value the app holds. A value with no conflict becomes an approved field. A conflicting value becomes a proposal in pending review.
  - The commit reads every field back before it writes it. A value that changed after the merge becomes a proposal. A concurrent edit survives the import.
  - The pending changes stay in memory until one commit writes the whole set in one transaction. A cancel drops the set, and the database holds no half-finished import.
  - The library record and the path mappings join the same transaction as the tracks.
  - The source library stays read-only. The import never writes to it.
  - A field with no storage yet stays pending. The app writes no field name it cannot store.
  - A file with a failing parser leaves its track unresolved. One unreadable file never stops the import.
  - The framework handles only the errors the audio parser documents. A defect in the adapter stops the import instead of hiding it.
  - The dialog picks the library of the row the DJ picked. A library the app cannot read never takes the pick.
  - A track that moved keeps its library row. The import leaves no second row for the same track.
  - The change count covers every row the commit writes. An import that found no track still reports the library record and the path mappings.
  - The import root must be an absolute folder that exists. A root that cannot hold an import stops the run before the plan starts. The result names the problem and holds no change.
  - The plan keeps every pending value with its payload and its track until the commit.
- A shared step finds the DJ libraries installed on this computer (IMPORT-1, #143).
  - It lists the path, the format, the version where readable, and the last changed time of every library it finds.
  - The DJ picks one. The step opens no file before that choice.
  - The search walks the standard parent folders to a shallow depth and follows no link out of the profile.
  - A folder declared for another platform holds no candidate.
  - The mounted drives come from the USB inspection layer. The drive class rule applies, and a fixed disk keeps its note.
- The known library locations carry the vendor page that states the location (IMPORT-1, #143).
  - Traktor, Serato, Mixxx, and rekordbox each name the folder per platform.
  - The rekordbox USB export names the hidden device folder, PIONEER on FAT and .PIONEER on HFS. The device library and the OneLibrary database sit in a rekordbox folder inside it.
  - The framework marks a location with no vendor page as observed. The entry names the version seen on.
- Prefix mappings move a source library path onto this computer (IMPORT-1, #143).
  - The framework reads four path styles. Windows drive letters, macOS and Linux slashes, URL-encoded file URLs, and the Traktor volume notation.
  - The DJ names the folder the music lives in. Every imported file must resolve inside it.
  - A path outside that folder stays unresolved. The import does not read it.
  - A symbolic link inside the folder cannot carry a path out of it. The check runs again right before the read.
  - The framework opens the file once and checks the file identity after the read. A file that changed during the read does not reach the app.
  - The open compares the identity of the directory entry. No link follows that identity. A file that a link replaced never opens. The framework checks the path against the root again after the open. A folder swapped for a junction does not reach a file outside the root.
  - The metadata reads from the same handle. The framework never opens the file a second time.
  - The known library folders stay inside the profile folder. A folder that is a link holds no candidate at all.
  - The step checks every candidate against the profile folder at the moment it looks at it. A folder that a link replaced after the walk holds no candidate.
  - An imported track names the source group of its import. The group holds the tracks that came with it.
  - A scan root that is a link holds no candidate. A scan root that resolves outside the profile folder holds no candidate either.
  - The reverse mapping puts a local path back into the source layout for the exporters and EXPORT-7.
  - The framework refuses a reverse mapping that climbs above the target prefix. The result stays under the source prefix.
  - Both directions refuse a path that names a drive of its own.
- A bounded search finds an audio file that moved (IMPORT-1, #143).
  - The DJ picks a root. The search matches the file name and the size inside the root. A hash check confirms the match.
  - The search visits a bounded number of folders as well as a bounded number of files.
  - The search reads each candidate once, however many references name it. The search stops hashing at the work bound.
  - The search hashes a candidate through a handle that stays inside the root. A file that a link replaced never sends the read outside the root.
  - The search never leaves the root and follows no link out of the root.
- The imported libraries and the path mappings reach the database through migration 4 (IMPORT-1, #143).
- One production registry holds the importer of every format the app reads (IMPORT-1, #143).
  - The desktop dialog and the headless conversion pass of EXPORT-7 both read libraries through it.
  - A format with no importer raises a named error that lists the formats the registry holds.
  - The error names the issue that adds the reader of the format.
  - The dialog takes the formats the app can read. A library of another format stays out of the list. The production registry names those formats by default.
  - A file the DJ names by hand carries the format of its header. The registry picks the reader that goes with it.

### Changed

- Review statuses show plain labels in the library Status column and the detail panel badge (UI-2, #342, item 12).
  - The labels read Not analyzed, Analyzing, Has suggestions, Needs review, Conflicting matches, Partly matched, Partly approved, No match, Approved, Ready to save, Applied, Saved, and Failed.
  - Needs review and Ready to save match the sidebar filters.
  - Each label is unique. The status filter maps a label back to one stored value.
  - An unknown stored value shows as stored.
- The detail panel and its player use plain copy (UI-2, #342, item 12).
  - The Warnings section reads Issues. An empty list reads "No issues.".
  - The header shows the file name. A file without tag support adds "(can't save tags to this file)".
  - The Changes columns read Now, Suggested, Source, and New value. The Source cell reads such as "acoustid, 88% match".
  - Candidate rows read such as "A - T (Original), Label CAT1. From discogs, 91% match". Analysis rows read such as "Detected: 128 BPM, key 8A".
  - Buttons read Play, Load paused, and Lock field. With no track picked, the panel says "Pick a track to see its details."
  - The player reads "No track loaded". Its playback and waveform failures use plain sentences and keep the reason.
  - The binary tag summary in Other tags stays, since the tag reader stores binary frames in that form.
- The batch queue and its stuck prompts use plain copy (UI-2, #342, item 12).
  - The queue panel reads Queue. Its buttons read Move up, Move down, Cancel, See results, and Clear finished.
  - A pending cancel reads "Stopping after the current file.".
  - Stuck prompt titles read such as "Saving isn't responding". The buttons read Quit now and Keep waiting.
  - Each stuck prompt names its limit, says what stays done, and offers the two choices in plain words. The save prompt says InfinitePrep offers to restore or finish the file at the next start.
  - The import stuck prompt follows the same shape.
  - Batch states, result counts, and failure lines stay unchanged.
- The write preview uses plain copy (UI-2, #342, item 12).
  - The window title reads Review changes. The controls read Rename files, Refresh, and Save changes.
  - The status line reads such as `"3 of 5 files will be saved."`.
  - With nothing to save, the preview says "Nothing to save. Fix the skipped files or close this window."
  - A blocked save shows the title "Can't save yet".
  - Rename template placeholders such as {artist} stay, since the renamer reads those placeholders.
- Settings uses plain copy (UI-2, #342, item 12).
  - The window title reads Settings. The save button reads Save.
  - Row labels name what each setting does, such as "Key format in tags", "Fake lossless check", "AI server address", and "Delete after (days)".
  - The local AI option reads "Let a local AI pick between matches". The batch option reads "Stop when a file fails".
  - USB recovery cleanup asks "Delete USB recovery data?" and reports such as "Deleted 3 USB recovery entries.".
  - The hidden reserve row keeps its MiB unit, since the value counts mebibytes.
- History uses plain copy (UI-2, #342, item 12).
  - Screen readers name the list of writes History.
  - A failed restore reads such as "Couldn't restore this file (disk full). It's unchanged. Check the backup drive and try again."
- The version advances from 0.87.0 to 0.88.0 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.

### Fixed

- A rescan in the USB export dialog drops the drive shown on Confirm. After Back and a rescan, no drive counts as confirmed until Next runs again, even when the listing fails.

## [0.85.0] (2026-11-23)

### Added

- A USB export dialog walks through two steps, Choose drive and then Confirm (UI-2, #342).
  - Choose drive lists USB drives and memory cards. Next turns on for a drive that passed inspection.
  - Confirm names the drive and the players. Drive details hold the location, model, device ID, serial number, and each fact the check could not confirm. The details start closed.
  - A space bar shows the share of free space the export needs, with a line such as "1.2 GB needed of 16.0 GB free".
  - What happens on the drive lists the planned changes, or says nothing on the drive changes.
  - Export turns on only when the drive passes every check. Enter cancels. A drive that must never take an export hides its identity and space.
  - No window opens the dialog yet. The older drive picker and confirm dialog stay unchanged.
- The decision register records D408. USB Update matches by verified stable ID, then exact normalized path. Relative path, title, and artist only propose a match the DJ confirms (#309).
- History lists past tag writes on the left and the chosen write on the right (UI-2, #342).
  - Each write shows when it ran, the file, and a plain status such as Saved, Undone, or Stopped partway.
  - The right side lists each field the write changed, with its value before and after.
  - A sentence states what the backup can do now. Restore sits under it and turns on only for a finished write.
  - The newest write opens chosen.

### Changed

- The change journal window takes the title History. One Restore button beside the chosen write replaces the button on each row.
- A write preview of up to 10 files opens every file row to its field changes. A longer preview keeps one closed row per file.
- A file with new values that match its tags and no new name reads Skipped with the reason Nothing to change. The counts and Save leave it out. Save writes no same-tag copy and no backup for it.
- The Batch Queue strip paces its time left by the finished files only. Time on the file in progress leaves the estimate unchanged.
- The Storage tab hides the two USB export reserve rows. USB export has no entry point in the app yet. No code reads the reserve. A save keeps the stored reserve values.
- The dependency review action pin moves to `bbc44d1`. The pin names the v5.0.1 release, and the action inputs stay the same.
- The foucault security review pin moves to 3.8.9. Both `uses:` references and both `audit_ref` values name commit `9ec9681`. The upstream release publishes a cancelled review run as a cancelled check run.
- `platformdirs` moves to 4.14.8. The per-user folder paths stay the same on every platform.
- `ruff` moves to 0.17.6. The lint and format checks pass on the current tree.
- The SBOM tool `cyclonedx-bom` moves to 9.0.0. The release raises the `cyclonedx-python-lib` floor to 11.12, and the lock holds 11.12.0.
- The main window and its sidebar use plain copy (UI-2, #342, item 12).
  - Columns read Artist, Title, Mix, Status, and Issues. The Issues cell counts issues, such as "2 issues". Label / Cat# stays.
  - The sidebar filters read With issues and Ready to save.
  - The window title is InfinitePrep. The key picker reads Key format.
  - Dialog titles read Analyze, Approve all matches, Review changes, Transcode, Export playlist, Results, Import failed, and Still working.
  - Approve all matches reports "Approved 3 fields.". An Analyze batch reads "Analyze 3 files" in the queue.
  - With no tracks shown, a message says `"Nothing here yet. Add a folder or clear your filters."`
  - The quit prompt offers Keep working and Quit now.
- The version advances from 0.83.0 to 0.85.0 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.

### Fixed

- Desktop Identify & Analyze stores no mock candidate matches. The app previously fell back to the mock fingerprint and catalog adapters. Every run stored made-up Deep Dish / TRIUK014 candidates in the library. Analyze now runs local analysis only until the app connects online identification.
- Technical details, the write preview, and History name each field as the Changes table does, such as Artist and BPM.
- The Batch Queue strip forgets the start time of each batch once it leaves the queue.
- History shows a plain sentence for a change with a state it doesn't recognize, and Restore stays off for it.
- The Batch Queue strip tests drain the queue before the test window closes. The window owns the worker thread, and Qt ends the process when a running thread loses its owner.

## [0.83.0] (2026-11-22)

### Added

- The detail panel stacks its content in sections (UI-2, #342).
  - Waveform and player, Changes, Warnings, Other tags, and Candidates each open by default. A section arrow closes it.
  - Technical details starts closed. It holds the tag frame name of each field, the content hash, the latest backup, and the decoder message.
  - Badges under the track name show the status, the format, the BPM, and the key in its key color.
  - Changes lists fields with a proposal or a changed value first, with the field name in bold. Every field keeps its row.
  - Each warning sentence sits on its own line.
- A one-line Batch Queue strip at the bottom of the window shows the running batch (UI-2, #342).
  - The strip shows the batch name, the current file, a progress bar, a done-of-total count, and the time left.
  - The strip hides while no batch runs or waits.
  - A click on the strip shows the full queue above it. A second click hides the queue.
- The write preview opens with a summary bar and a row per file (UI-2, #342).
  - The summary bar counts the files that change, the files that lose some tag data, and the skipped files. A note says each file gets a backup first.
  - Each file row opens to list every field that changes, with the current and the new value.
  - Each file shows Ready, Ready (some tag data will be lost), or Skipped. The reasons sit in the status tooltip.
  - The save button names the number of files it saves.
- Settings lists the sections on the left with an icon each, and shows the chosen section on the right (UI-2, #342).
  - Each section states when its changes take effect.
  - The Identification note states that the app stores those keys for later. Analysis reads none of those keys yet.

- The main window follows the UI-R layout (UI-2, #342).
  - A workflow bar shows Import, Analyze, Review, Apply, and Export with a track count each. The first stage with waiting tracks carries its action as the main button. Transcode, History, and Settings sit at the right end.
  - Alt+1 to Alt+5 run the five stages.
  - A sidebar holds the stages, the library filters (All tracks, Needs review, Has warnings, Ready to write), the source folders with track counts, and the planned playlists and crates.
  - From 1600 px the sidebar stays open. Below 1600 px it opens from a button beside the search field, and the bar keeps only the current stage's label. Below 1280 px the detail panel moves under the table.
  - The window opens no smaller than 960 x 600 and remembers its size and position. A window saved on a monitor that is gone moves back onto a screen.
- The library table follows the window width.
  - Filename, Artist, and Title share the spare width equally. Each other column fits its cells.
  - Label / Cat# and Filename show from a 1100 px table. Format, Duration, and Mix hide below a 1000 px table. Artist, Title, BPM, Key, Status, and Warnings always show.
  - A right-click on the header shows or hides any other column, and that choice persists. Reset columns hands every column back to the width rules. A drag moves a column into a new order. The new order persists.
  - The Status column shows a state icon in its status color. The status name stays as tooltip and spoken text.
  - The Warnings column shows a warning icon beside the count.
  - Each key cell takes the Open Key color of its key. Minor keys use the darker shade.
  - Rows are 24 px tall at the Standard text size.

### Changed

- The sidebar filters replace the review status dropdown.
- The Key Notation picker moved to the row above the table. The theme picker left the main window and stays in Settings.
- Every unit test under `tests/unit/` states its behavior in a one-line docstring. The playlist storage tests and the `add_track` helper carry type hints.
- New unit tests cover gaps in existing checks.
  - The ID3v2 date check covers a zero day as well as a zero month.
  - The Discogs cache serves a repeat search inside the 6-hour TTL and fetches again after it.
  - Reconciliation Rules 1, 2, and 5 each have a dedicated test. The full reconcile test no longer claims all 10 rules, because the source names only these three. The three rule tests carry names under 50 characters.
- Test function names stay at 50 characters or fewer, `test_` included.
  - 582 tests under `tests/unit/`, `tests/integration/`, `tests/e2e/`, and `tests/` take shorter names. Only the `def` lines change.
  - Ruff joins the signatures that now fit on one line.
  - The copied gate tests listed in the ruff `extend-exclude` keep the upstream names.
  - Nine UI test files that #347 also changes keep the old names until that pull request lands.
- The version advances from 0.82.0 to 0.83.0 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.

### Fixed

- The USB recovery launch scan sends its notices to any passed callback, including a falsy one such as an empty recording list. Before, an empty recorder fell through to the real notice dialog. The dialog waited for a click and hung the test suite on every platform.
- Every injected function and callback counts as passed unless it is `None`. Before, a falsy stand-in fell back to the real default, and ten tests reached the network or skipped the callback.
  - The Discogs, iTunes, MusicBrainz, AcoustID, and Ollama adapters keep an injected request function.
  - The fpcalc installer keeps an injected downloader.
  - The Linux, macOS, and Windows drive tools keep an injected command runner or info reader. The USB target list keeps a passed inspector and probe.
  - The main window toolbar actions and the detail panel change callback run a falsy callback.

## [0.81.1] (2026-11-21)

### Added

- UI-R visual system (UI-2, #342).
  - Two new themes, Dark and Light, built from the logo colors. Every text color reaches 4.5:1 and every graphic color 3:1 in both themes.
  - Each Open Key number has its own key color. Minor keys use a darker shade of the same hue. Key cells use black or white text, whichever reads better.
  - A Text Size setting on the General tab picks Compact, Standard, or Large. Spacing and button sizes follow the chosen size.
- 54 icons from Tabler Icons v3.50.0 and Lucide 1.70.0, copied unchanged from the pinned releases. NOTICE names both sets, and each set's license ships beside its icons.

### Changed

- The theme pickers offer Dark, Light, and Match System. Native left the pickers and applies only when the theme package cannot load.
- Settings from an older version that name Dark Teal, Light Teal, or Native load as Dark or Light.
- The project orientation notes that the agent prose rules do not govern on-screen text.
- Unit tests share the repeated helpers through `tests/fixtures/`.
  - `memory_keyring.py` holds the keyring fakes. `tests/conftest.py` holds `restore_keyring`.
  - `storage_fallback.py` holds the storage prompt recorder, the per-user folder redirects, and the fallback list reader.
  - `sqlite_version.py`, `fake_http.py`, `memory_repository.py`, `id3v2_render.py`, and `stereo_wav.py` hold the version reader, the response fake, the in-memory repository, the v2.3 render helpers, and the stereo WAV writer.
  - Two window tests import the existing `MemorySettingsManager`.
- Unit tests replace recorder and stand-in lambdas with the callable `CallRecorder` in `tests/fixtures/call_recorder.py` or with named functions. `qt_dialogs.py` and `waveform_stub.py` name the argument positions the recorders keep.
- Two Discogs release id tests merge into one parametrized test with all nine ids. A named constant sets the overlong id length. A short stream decode test that repeated another line for line is gone. The metadata read test derives its expected fields from the written tags.
- The version advances from 0.81.0 to 0.81.1 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.

### Fixed

- The library table gives its spare width to the columns that need it (UI-1, #244).
  - Filename, Artist, and Title take two parts of the spare width each. Mix / Version and Label / Cat# take one part each. The other columns fit the cell contents.
  - No column stretches to fill the table. A saved layout from an older version loads without the last-column stretch.
  - A dragged column keeps its width. A saved layout keeps every width as saved.
  - Each header shows its full name in a tooltip.
  - The warnings column shows a count such as "2 warnings". The full warnings show in the cell tooltip. The search box no longer matches warning text.
- The detail panel field table fits its Field, Source & Score, and Actions columns. Existing Tag, Top Proposal, and Approved / Edit share the spare width equally.
- The Field column shows readable names such as "Mix name" and "Catalog number". Stored field keys stay unchanged.
- The Key Notation box shows Standard, Camelot, and Open Key. The stored notation values stay unchanged.

## [0.80.6] (2026-11-20)

### Fixed

- `scripts/check_action_versions.py` names a failed or timed-out `gh` fetch instead of reporting every pin as one that names no release. A pin that names its release costs one ref lookup. An annotated tag resolves to its commit. Other pins list the tags 100 per page.
- `packaging/release.py check-tag` reports an unparsable `pyproject.toml` as a parse failure. A directory in place of a version file gives the missing-file line instead of an `IsADirectoryError`.
- `packaging/check_bundle.py` reads the notice rule from one set of regular files. `compile_pattern` rejects two adjacent `**` parts and two `*` joined only by `?`. A bundle folder without read access now fails the check.
- `infiniteprep.spec` stops the build with a message when the package init has no version line.

### Changed

- CI and packaging code does less repeated work.
  - The coverage summary compiles the condition pattern once and keeps the lowest files with `heapq.nsmallest`.
  - The bundle check indexes the allowlist by exact path and by literal first path part. One `os.scandir` walk lists the bundle.
  - The size report walks each bundle once, the macOS `Contents` layout included. The prune loop classifies each destination once.
  - The spec reads the links of each binary once per build.
  - `build-packages.yml` reads the app version once into `APP_VERSION`. One size step covers every operating system. Each release step views the release once and looks up asset names in an associative array. The save step reuses draft bundles through `reuse_draft_bundle`.
  - `scripts/classify_changed_paths.py` reads version file texts apart from the version decision.
- Nine helpers in `packaging/release.py` carry verb-noun names. The old names have no aliases. Decision D321 names `compute_file_sha256`.
- Owned tests spend less time waiting and rebuilding the same inputs.
  - The waveform duration cap test lowers the cap to 2 s. The stuck child runner test uses a 0.2 s deadline. The stderr flood test keeps 2.0 s because its child must finish inside the window.
  - The migration, rate limit, two-thread lookup, folder import, and layout persistence tests wait for the checked event instead of a fixed sleep. The migration and lookup tests now assert the overlap that the old sleeps only made likely.
  - The local HTTP servers poll every 10 ms. Shutdown returns at once.
  - The WAV fixture factory renders each argument set once and writes a fresh copy for every caller. The new `tests/unit/test_audio_factory_cache.py` pins that the cached bytes match a direct write.
  - Three detail panel tests keep the waveform pool idle through the shared stub in `tests/fixtures/waveform_stub.py`.
  - Module-scoped fixtures share three read-only inputs.
    - The ID3 frame at the inflation limit.
    - The analyzed test tones.
    - The parsed `src/` files for the invariant tests.
  - Five root test modules parse each workflow once per class. The comment script tests extract the script once per class.
  - The git-backed tests build each template repository once and copy the template into every test.
  - The ASCII checks use `str.isascii`. The in-memory isolation test checks ten closed managers. The digest test builds its content from a repeated byte run.
  - The pip runs stop after 120 s. The folder import fixtures open each import gate in teardown.
- The version advances from 0.80.4 to 0.80.6 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.

## [0.80.4] (2026-11-19)

### Changed

- Every test runs against its own storage registry.
  - An autouse fixture in `tests/conftest.py` installs a fresh registry per test. The fixture configures each folder below `tmp_path` in the per-user layout. No test reads or writes the real per-user profile through the default folders.
  - The fixture skips the swap when the app package is absent. The `lint-and-style` job runs one invariant test without the package.
  - The new `tests/unit/test_storage_registry_isolation.py` pins the fresh registry and the `tmp_path` folders.
  - Ten tests reverted every patch with `monkeypatch.undo()` and lost the registry swap. Each now patches inside a scoped `monkeypatch.context()`.
- Qt tests share the pytest-qt `qapp` fixture. The private `QApplication` helpers are gone. Each General tab test registers its widget with `qtbot`.
- Tests that changed session state restore the state afterwards. The state covers the application icon, the clipboard text, and the shared network error instances.
- The version advances from 0.80.3 to 0.80.4 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.
- Owned tests that could not fail now fail on the defect named in each test.
  - The restore tests corrupt the real source before a fake crash. Each recovery must repair it.
  - The bulk readers, the limiter, the coverage sort, the symlink filter, the lock hashes, and the release asset count each get an assertion that a defect breaks.
  - The comment-script tests check the Node exit code against the recorded calls.
  - Silent returns in the USB tests become skips with a reason. Checks that restated the test setup are gone.
  - `test_action_versions.py` checks every external pin in the owned workflows. `scripts/check_action_versions.py` now also reads owner/repo/sub/path references such as `github/codeql-action/upload-sarif`.
- Dead code leaves the owned tests. Each unused constant either gains the check its name describes or disappears.
- `ci.yml`, `os-tests.yml`, and `dependency-review.yml` drop the push fallbacks. Each runs on pull requests only.
  - The concurrency groups key on the pull request number. A newer commit always cancels the older run.
  - The classify steps read the pull request revisions directly. `security-devops.yml` and `sync-check.yml` still run on a push and keep the fallbacks.
  - The coverage comment job no longer repeats the event check.
- The coverage and security scan comment scripts await `main`. Both test harnesses run the script inside an async function as `actions/github-script` does.
- The `check-tag` and `release` jobs in `build-packages.yml` test `github.event_name == 'push'` alone. The push trigger lists `v*` tags only.
- Workflow comments on the matrix timeout, the launcher probe order, and the removed push paths now match the code.
- The staging cleanup race test pins the refused no-follow open. The cleanup logs a warning, keeps the link, and leaves the real folder untouched.
- The version advances from 0.80.2 to 0.80.3 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.
- The ci test matrix skips the upstream test copies that other jobs already run.
  - The new `tests/conftest.py` adds the opt-in pytest flag `--exclude-upstream`. A local `pytest` run without the flag collects every test.
  - The new `tests/upstream_tests.py` reads the `tests/test_*.py` entries of the `pyproject.toml` `[tool.ruff] extend-exclude` list. That list names the `abuzucom/agents` copies and the review wiring test. A missing file, an entry outside `tests/`, or an empty result stops the run with a usage error.
  - Both `test-matrix` pytest steps in `ci.yml` pass the flag. The `ci.yml` checks job, `os-tests.yml`, and the `sync-check.yml` hook coverage step still run the upstream tests on every operating system.
  - `tests/test_prose_policy_backtracking.py` joins the upstream list. Ruff skips it like the other upstream copies.
- The ci checks job runs the gate suite through the parallel shard runner `scripts/run_tests.py` instead of a serial `unittest discover`.
- `os-tests.yml` runs on Python 3.12, the version of the ci test matrix and not the latest 3.x release.
- `ci.yml` and `os-tests.yml` cache pip downloads. Each cache key names every requirements file the job installs. `--require-hashes` still verifies every cached wheel.
- `ci.yml` keeps the pip bundled with the pinned `setup-python` release. The unpinned `pip install --upgrade pip` steps are gone.
- `security-devops.yml` cancels the scan of a superseded pull request commit. A push to `main` always finishes its scan and its Security tab upload.
- The new `tests/test_upstream_test_exclusion.py`, `tests/test_pip_cache_wiring.py`, and `tests/test_security_devops_concurrency.py` pin the loader, the flag wiring, the cache keys, the Python pin, and the concurrency group.
- The version advances from 0.80.1 to 0.80.2 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.
- A new test swaps the staging folder for a link after the cleanup opens its folder descriptor.
  - The new `tests/unit/test_staging_cleanup_post_open_swap.py` makes the swap right before the listing. The moved staging folder loses its files. The link target keeps its file. The link stays.
  - The AI quality review on pull request 330 found this gap in the test set.
- The changelog prose passes the agent prose gate.
  - `compliance / pr-checks` runs `scripts/check_agent_prose_gate.py` on each pull request that discloses an agent. The gate reads the whole `CHANGELOG.md`. It failed on 46 findings in older entries.
  - Separate sentences replace comma-led tails. Nouns replace personal pronouns. Two inline lists become nested bullets. One past-habit phrase now names the old behavior with "before". Every entry keeps the same meaning.
  - The version advances from 0.80.0 to 0.80.1 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.
- The `release` job in `build-packages.yml` runs in the `release` deployment environment.
  - A pushed `v*` tag on a `main` commit no longer starts the write job alone. The repository owner approves each release run first.
  - The job receives its `contents: write`, `id-token: write`, and `attestations: write` token only after that approval.
  - The repository owner creates the `release` environment in the repository settings with the repository owner as a required reviewer.
  - The new `tests/test_release_environment.py` pins `environment: release` on the release job and rejects an environment on any other job.
  - The version advances from 0.79.3 to 0.80.0 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.

## [0.79.3] (2026-11-18)

### Added

- The main window works from the keyboard (accessibility audit).
  - Every toolbar button carries an Alt key.
    - `Alt+I` Import Folder
    - `Alt+A` Identify & Analyze All
    - `Alt+B` Bulk Approve
    - `Alt+P` Preview & Apply Writes
    - `Alt+T` Transcode
    - `Alt+E` Export Playlist
    - `Alt+J` Change Journal & Restore
    - `Alt+S` Settings & Providers
  - `Alt+K` focuses the key notation picker. `Alt+H` focuses the theme picker.
  - `Ctrl+O` opens the folder import picker. `Ctrl+,` opens Settings & Providers.
  - `Ctrl+P` and the media Play/Pause key press the player `Play` button. The button tooltip names the key. Space stays with the focused button or field.
  - The new `tests/unit/test_keyboard_shortcuts.py` covers the labels, the Alt keys, the accessible names, and the shortcuts.

### Changed

- Variables, parameters, and module constants in `src/` carry descriptive names in place of short names and abbreviations.
  - Role names such as `row`, `proposal`, `candidate`, and `frame` replace one-letter names such as `r`, `p`, `c`, and `f`.
  - Full words replace abbreviations such as `std`, `cand`, `rel`, `val`, `idx`, and `btn`. Examples include `standard_tags`, `candidate`, `release`, `value`, `index`, and `button`.
  - `COL_*` constants become `COLUMN_*`. `MAX_METADATA_STR_LEN` becomes `MAX_METADATA_TEXT_LENGTH`. The other renamed constants follow the same pattern.
  - Public keyword parameters change names. Examples include `clean_str(value, max_length)`, `default_command_runner(command)`, `read_dropped_tag_data(file_path, audio_format)`, `duration_similarity(file_duration, candidate_duration)`, and `Repository(database)`. A call with an old keyword name now fails. The repository owner approved this incompatible change.
  - Ten unit test files import the renamed constants. The imports and references in those files follow the new names. The repository owner approved these test edits. The edits change no assertion.
  - Unit suffixes, chunk identifiers, enum note names, public `id` fields, the `__exit__` parameters, and the ignored Qt `_c` placeholder keep the existing names.
  - The version advances from 0.78.4 to 0.79.0 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.
- Every workflow job that runs on a runner states a timeout, apart from three pinned workflows.
  - The jobs in `agents-compliance.yml`, `deploy-pages.yml`, `gate-integrity.yml`, `os-tests.yml`, and `sync-check.yml` had none. A hung step held a runner for the six hour default.
  - The Windows gate tests stop after 30 minutes and the macOS gate tests after 20. A normal Windows run takes about 8 minutes. The other jobs stop after 10 or 15 minutes.
  - `security-review-pr.yml` and `quality-review-pr.yml` pin the reusable review workflows. Each edit to those files needs owner approval. `scripts/check_compliance_tree.py` accepts only an exact key set for the privileged job in `immutable-conflict-check.yml`. A timeout there fails that check. These three stay unchanged.
  - The new `tests/test_workflow_timeouts.py` requires a whole-minute timeout below the default on every other runner job.
  - The hash-pinned lockfile, the pinned build backend, and the uninstaller folder guard from the same review were already on `main`.
  - The version advances from 0.78.2 to 0.78.3 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.
- The build backend pins `setuptools==84.2.1` and `wheel==0.48.0` (issue 288). Each build resolved the newest versions before. The repository owner approved the `wheel` version.
- Hygiene from the wholesale quality review (issue 288).
  - Conditions that earlier guards make constant are gone from the BPM estimate, the pitch drift check, the review status refresh, and the expiry days check.
  - The DSP thresholds, the Discogs match scores, the fpcalc, Ollama install, and LLM request timeouts, and the default provider cache lifetime carry names. Each value stays the same.
  - `deduplicate_space_entries` in `services/usb/_shared.py` replaces three copies in the Linux, macOS, and Windows inspectors.
  - Comments and docstrings match the code in the macOS runner and plist parser, the Windows interpreter notes, the backup version reader, the transcoder class, and the pitch drift range.
  - The import stuck prompt names the timeout the watcher uses. It stated 2 minutes for every timeout before. The new `stuck_import_text` builds the text.
  - The empty player clock uses the clock format of a loaded track.
  - `split_time_seconds` divides minutes by the new `MINUTES_PER_HOUR`.
  - The new `tests/unit/test_phase5_hygiene.py` covers the stuck prompt wording, the space entry dedupe, and the hour split.
  - Issue 322 tracks the USB confinement helpers and the RIFF chunk walkers. A merge of those helpers changes error behavior on sensitive paths.
  - The version advances from 0.77.0 to 0.78.0 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.

### Fixed

- The staging cleanup does not delete through a link swapped in after its link check.
  - The cleanup checked the staging name for a link and then listed the folder by name. A link swapped in between could lead the unlink loop into another folder. The AI security review found this race.
  - On POSIX the cleanup opens the staging folder once with `O_DIRECTORY` and `O_NOFOLLOW`. The listing and each unlink go through that descriptor. A link swapped in before the open fails it and keeps its target whole. A swap after the open leaves the descriptor on the real folder. Windows has no `dir_fd` and keeps the name based cleanup.
  - The new `tests/unit/test_staging_cleanup_race.py` covers the swapped link, a normal cleanup, and the `ELOOP` stop on a link cycle in the library path.
  - The version advances from 0.79.2 to 0.79.3 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.
- A portable library on FAT or exFAT media gets its pre-migration backup again. Links above the library no longer redirect it.
  - FAT and exFAT media ignore folder modes. The backup and restore staging folder therefore could not become owner-only there. Migration backup and restore failed with `PermissionError` since 0.78.4. A staging folder with shared modes or another reported owner now logs a warning. The copy goes on. A link in place of the staging folder still fails the copy.
  - The staging cleanup removes only a link under the staging name. It followed such a link into its target before.
  - The library opens through its real folder. `resolve_library_path` resolves every link above the library once, at open. SQLite, the side files, the backup, and the restore then use that path. A link above the library that changes later no longer moves any of these files. A link at the library file name itself still fails the open.
  - The shared folder warning covers every folder that the path lookup reads. That set includes each folder that holds a link on the way and the folders of each link target. Above the library, a folder owned by this account or root counts as safe when other accounts cannot write it or when the sticky bit protects its entries, as on `/tmp`. Any other folder logs a warning that names it. The library still opens there. macOS `/var` and `/tmp` links and linked data folders keep working.
  - The new `tests/unit/test_portable_staging_and_parent_links.py` covers each change.
  - The version advances from 0.79.1 to 0.79.2 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.
- Four regular expressions no longer backtrack quadratically or cubically on long whitespace runs.
  - `PAREN_PATTERN` and `BY_REMIXER_PATTERN` in `services/matching_engine.py` parse titles and mix names from audio tags. Each capture before a whitespace run now ends on a non-space character. A 60,000-space title took minutes and now takes milliseconds. The captures stay the same for trimmed text.
  - `TUTORIAL_PATTERN` in `scripts/prose_policy.py` kept whitespace runs across lines. 2,000 blank lines took over 40 seconds. Each run now stays on one line. The finding now names the keyword line instead of the first blank line above it.
  - `INLINE_ENUMERATION_PATTERN` in `scripts/prose_policy.py` split one whitespace run two ways. A lookbehind now takes the place of the separate run. The matches stay the same.
  - The new `tests/test_prose_policy_backtracking.py` and `tests/unit/test_matching_engine_backtracking.py` run each pattern on a long whitespace run under a 10 second bound. The tests also compare each new pattern with the previous pattern on every short token sequence.
  - The version advances from 0.79.0 to 0.79.1 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.
- The write preview names tag data that a rewrite cannot keep.
  - The ID3 parser skips encrypted and truncated frames. The FLAC parser skips Vorbis entries without `=`. Each writer rebuilds the tag from the parsed frames. A write lost that data without notice before.
  - The new `tag_data_warnings` field of `FileWritePlan` lists each item. The Safety Status cell shows `READY WITH DATA LOSS` and names each item. The write stays allowed.
  - A compressed ID3v2.2 tag yields no frames at all. The preview now blocks that write, because it would remove the whole tag.
  - A tag or file that the check cannot read blocks the write with a validation error that names the reason. The preview showed `READY` for such a file before.
  - The write runs the check again on the copy it writes. A result that differs from the preview stops the write before any tag changes. The new `TagCheckChangedError` records the write as failed without a restore, as the payload and source-change errors do.
  - The new `tests/unit/test_dropped_tag_data_preview.py`, `tests/unit/test_unreadable_tag_preview.py`, and `tests/unit/test_shared_folder_warning_and_apply_recheck.py` cover each case.
- The library, its backups, and the settings stay readable by the owning account only.
  - The new `private_files.py` creates missing app folders with mode 0700 and library files with mode 0600. An existing library loses its group and other bits on open. SQLite copies that mode to the WAL and SHM files. The migration backup and the staged restore copy start owner only. An existing folder keeps its mode. Windows ignores these bits.
  - The mode change does not follow a link. It checks the name with `lstat`, opens it with `O_NOFOLLOW`, confirms the same file, and changes the mode through that descriptor. The backup copy and the staged restore copy start with an exclusive create. A link planted at either name fails the copy.
  - The backup copy and the staged restore copy now live in a new owner-only staging folder beside the library until the final rename. Each step reopens its file by name. Another account cannot swap a name inside that folder between the steps. The folder and its files go away after each run.
  - The library refuses to open through a symbolic link at its name. SQLite followed such a link before.
  - A library folder owned by another account, or with group or other write access, logs a warning that names the fix. The library still opens there. A portable Data folder on FAT or exFAT media shows such modes. The link check and the SQLite open use the name separately. Only a folder that other accounts cannot change closes that gap.
  - The per-user data folder that startup creates gets mode 0700. It got the default mode before.
  - The settings write syncs the temporary file before the rename and the folder after it on POSIX. A power loss after the rename could leave an empty settings file before.
  - The fpcalc installer extracts to a random hidden name and renames it into place. An interrupted install left a partial binary under the real name before.
  - The two `uuid5` proposal ID builders state the SHA-1 use outside security that Rule 7 requires.
  - The new `tests/unit/test_private_storage_files.py`, `tests/unit/test_private_files_symlinks.py`, and `tests/unit/test_private_staging_and_library_links.py` cover each change.
  - The settings dialog still loads the stored API keys into its masked fields. The fix there changes assertions in `tests/unit/test_settings_tabs.py`. The repository owner left it out of this change.
  - The version advances from 0.78.3 to 0.78.4 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.
- Writes, recovery, transcodes, and playlist exports no longer replace or delete a file that the app did not write.
  - A rename moves the file with one atomic call that fails on an existing name.
    - Linux uses `renameat2` with `RENAME_NOREPLACE`.
    - macOS uses `renamex_np` with `RENAME_EXCL`.
    - Windows uses `MoveFileEx` without replace.
  - A file that appeared after the existence check stayed exposed to a POSIX rename. That rename replaces the file without a word. A system or file system without such a call refuses the rename and keeps the old name. A hard link followed by an unlink is no safe substitute, because another program can replace the new name between the two calls.
  - A rollback and an interrupted write restore do not delete the file at the planned name. A refused rename, or a crash before the rename, deleted the file that already held the name before. An unfinished write records no hash of its output. Nothing proves that a file at the planned name came from it. The restore puts the original back, keeps the file at the planned name, and saves a verified copy of that file in the backup folder.
  - A restore of a finished write removes the renamed file only while it still holds the bytes the write produced. The cleanup moves the file to a private name with the same atomic move first. It then checks the device, the inode, and the SHA-256 there, and deletes the file only when all three still match. An in-place edit made before the move keeps the file. A file another program put at that name in between moves back and stays. The cleanup unlinked the name after a check before.
  - Mark as Finished points the record at the source when the rename did not run. It picked the file at the planned name before.
  - A transcode refuses an output name that already holds a file. Sibling export replaced an earlier export or any file with the `_XDJ` name. The finished output moves into place with the same atomic move.
  - A playlist export writes a new file with a short fixed name beside the playlist and swaps it in. A failed write truncated the previous playlist before.
  - The `#EXTINF` text replaces every control character, NEL, U+2028, and U+2029 with a space. A parser that splits on Unicode line boundaries read extra lines before.
  - The new `adapters/metadata_io/no_clobber_move.py` holds the shared move. The new `tests/unit/test_write_and_export_no_clobber.py`, `tests/unit/test_no_clobber_move.py`, `tests/unit/test_restore_discard_race.py`, `tests/unit/test_restore_preserves_renamed_copy.py`, and `tests/unit/test_discard_content_check.py` cover each change. The repository owner approved the edit that dropped the hard link cases from `tests/unit/test_no_clobber_move.py`.
  - The version advances from 0.78.1 to 0.78.2 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.
- USB manifest and recovery journal parsing refuses unsafe paths and reports every bad file as a `ValueError`.
  - A manifest omission track path, a journal track path, and a journal backup path must each be a relative path below the matching root. A `..` segment, an absolute or drive path, a UNC path, or a control character fails the read. A Windows device name fails in any component, with or without an extension. The names are `CON`, `PRN`, `AUX`, `NUL`, `CONIN$`, `CONOUT$`, and `COM` or `LPT` with a port digit from 0 to 9 or a superscript one, two, or three. A colon inside a component fails, because Windows reads it as a data stream. A path made only of `.` components names the root itself and fails. A component made only of dots and spaces, other than `.`, fails, because Windows trims `.. ` and `...` to `..`.
  - A journal mount point must be an absolute POSIX or Windows path without a `..` segment or a control character. The recovery dialog shows that text.
  - A probed mount path keeps its exact text or yields no mount point. The probe cleanup dropped invisible characters, stripped spaces, and cut the path at 512 characters before. The cleaned text could name another folder. Only a `..` segment rejects a mount path. Two dots inside a name, as in `/media/USB..2024`, rejected the path before.
  - A manifest or journal nested past the recursion limit fails as malformed. The JSON parser raised `RecursionError` before. The marker check did not catch that error.
  - A timestamp that leaves the datetime range on conversion to UTC fails with `ValueError`. `OverflowError` escaped before.
  - The new `tests/unit/test_usb_manifest_parsing_hardening.py`, `tests/unit/test_usb_path_component_rules.py`, `tests/unit/test_usb_relative_path_root.py`, `tests/unit/test_usb_windows_device_variants.py`, and `tests/unit/test_usb_windows_dot_space_parts.py` cover each change.
  - The version advances from 0.78.0 to 0.78.1 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.
- The Identify & Analyze All, Preview & Apply Writes, Change Journal & Restore, and Settings & Providers buttons show the ampersand again. Qt read the single ampersand as a hidden Alt marker before.
- The version advances from 0.76.4 to 0.77.0 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.
- The theme pickers and the Settings dialog read clearly (accessibility audit).
  - The toolbar and Settings theme pickers show Dark Teal, Light Teal, Native, and Match System. The pickers showed file names such as `dark_teal.xml` before. Settings still store the theme identifier. Existing settings files stay valid.
  - The Settings dialog leaves out the Playback tab. That tab held no setting and only said so.
  - The new `tests/unit/test_theme_labels.py` covers the labels, the stored identifier, and the hidden tab. With active human approval, five existing theme and settings test files now select themes by identifier instead of picker text, and the tab list no longer expects Playback.
  - The version advances from 0.76.3 to 0.76.4 in `pyproject.toml`, `src/infiniteprep/__init__.py`, and `requirements-lock.txt`.
- Field row actions and the write recovery prompt guard typed text and backups (accessibility audit).
  - A Save or Accept that raises no longer leaves its row marked. The marker dropped that row's unsaved text on the next refresh before.
  - A refused Accept keeps the row's typed text. Only an accepted value replaces it.
  - Lock on a field without an approved value says to save or accept a value first. It changed nothing and said nothing before.
  - Mark as finished in the interrupted write recovery dialog asks first, with No as the default. One click ended the backup restore path before.
  - The new `tests/unit/test_row_action_failures.py` covers the failed save, the refused and the successful accept, and Lock. `tests/unit/test_ui_recovery_dialog.py` answers the new prompt and adds a declined prompt test.
  - The version advances from 0.76.1 to 0.76.3 in `pyproject.toml` and `src/infiniteprep/__init__.py`.

## [0.76.1] (2026-11-17)

### Added

- The library sorts from the keyboard (accessibility audit).
  - A Sort by picker and an Ascending or Descending button sit next to the filters. The table header took no keyboard focus. Only a mouse could sort before.
  - A header click and the sort controls stay in step.
- Every control that Tab reaches in the main window and the write, journal, and recovery dialogs carries an accessible name. The filter field, the status filter, the key notation and theme pickers, the waveform view and zoom pickers, the rename template field, and every table and list announced no purpose before.
- The library, field, write preview, journal, and recovery tables open no cell editor. An edit typed in a cell changed nothing and vanished on the next refresh.
- The other tags, write preview, journal, and recovery tables pass Tab to the next control. Tab walked every cell before.
- The new `tests/unit/test_accessible_names_and_keyboard_sort.py` covers the names, the read-only tables, and the keyboard sort.
  - The version advances from 0.74.1 to 0.75.0 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- The library selection no longer stops playback (issue 249, D407).
  - A row click updates the detail panel only. The player keeps its track, its position, and its play state until an explicit load.
  - A `Play Selected` button in the detail panel header loads and plays the selected file.
  - `Ctrl+Enter` on the library table loads and plays the selected file.
  - A `Play` item in the library row context menu loads and plays the row under the pointer. A right-click outside a row opens no menu.
  - A double-click on a library row loads and plays that row.
  - The player `Play` button loads the selection when the selection differs from the loaded track.
  - A library row dragged onto the waveform loads that track paused at the start. A drop while a track plays asks first. `Cancel` keeps the playing track and its position.
  - The player shows a `Loaded:` line with the file name it holds. A mismatch with the detail panel stays visible.
  - The new `tests/unit/test_selection_keeps_playback.py` covers the selection, the four play paths, and the drop paths.
  - The version advances from 0.75.3 to 0.76.0 in `pyproject.toml` and `src/infiniteprep/__init__.py`.

- The new `tests/unit/test_destructive_action_guards.py` checks the guards in front of destructive UI actions (accessibility audit).
  - The version advances from 0.72.4 to 0.72.5 in `pyproject.toml` and `src/infiniteprep/__init__.py`.

- The new `tests/unit/test_keyboard_defaults.py` checks three keyboard paths (accessibility audit).
  - Enter in the rename template field keeps the write preview open.
  - Tab leaves the library table.
  - Tab from a field edit box reaches the action buttons of the same row.
  - The new `tests/unit/test_write_preview_enter_refresh.py` checks that Enter rebuilds the preview once with the typed template.
  - The new `tests/unit/test_detail_panel_row_tab_order.py` checks that Tab visits Save, then Lock, in one field row.
  - The version advances from 0.72.1 to 0.72.2 in `pyproject.toml` and `src/infiniteprep/__init__.py`.

- The bundle job reports the size of the built bundle, the size of every package, and the largest folders in the bundle (D354, issue 181).
  - The new `packaging/bundle_size.py` measures the unpacked bundle, reads the limit, and writes a Markdown report and a JSON report.
  - The new `packaging/bundle_size_limits.json` holds the measured size of each operating system on `main`. The limit is that size plus 50 percent.
  - The measured sizes are 248709436 bytes on Windows, 168151858 bytes on macOS, and 363129630 bytes on Linux. The limits are 373064154 bytes, 252227787 bytes, and 544694445 bytes.
  - A bundle past the limit fails the job. A record of null means the size was not measured yet. The check then reports the sizes and passes.
  - Every operating system needs a record in the limits file, and every record needs a byte count. A missing record or a record without a byte count is an error. A lost record therefore cannot disable a limit.
  - The new `tests/test_bundle_size.py` checks the measurement, the limit, the command, and the workflow wiring.
  - The README documents the size report and the limits file.
  - The version advances from 0.76.0 to 0.76.1 in `pyproject.toml` and `src/infiniteprep/__init__.py`.

### Changed

- A push to `main` after a merge skips checks that already passed on the pull request.
  - `ci.yml` and `os-tests.yml` trigger on pull requests only. A push to `main` runs neither the lint jobs nor the test matrix nor the Windows and macOS jobs.
  - The `check-sync` job of `sync-check.yml` runs on pull requests only.
  - `security-devops.yml` still runs on a push to `main`. Its scan keeps the code scanning baseline of `main` current.
  - `agents-md-compliance.yml` triggers on pull requests to `main` instead of pushes to `main`. The `static-checks` job now runs before merge.
  - The version advances from 0.72.5 to 0.72.6 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- `JobOrchestrator.process_files` takes `raise_on_failure` in place of `raise_on_unexpected` (issue 288). The flag raises for the expected job errors. The old name misstated the flag. No caller in `src/` passed the old keyword.
- The library status filter lists every review status (issue 288). The filter lacked `ready_for_review`, `conflicting_candidates`, `no_match`, and four more statuses that the pipeline sets.
- The library search reads the cells the table shows (issue 288). The search read the stored tag structure before. `tags` then matched every file. An approved artist matched none.
- The version advances from 0.72.7 to 0.73.0 in `pyproject.toml` and `src/infiniteprep/__init__.py`. The keyword rename is an incompatible change of a public method.
- Library work runs in a fixed number of queries and off the GUI thread (issue 288).
  - A library table refresh reads approved fields, proposals, and analysis results for every file in three queries. It ran about four queries per file before, on each search keystroke.
  - A playlist sort reads its tracks in three queries inside its write transaction. It ran three queries per track before and held the write lock for every track.
  - The interrupted-write check reads only the in-progress journal records. It loaded the whole journal before each write.
  - A column resize saves the table layout once, 300 ms after the drag settles, and on window close. It rewrote the settings file on each pixel of a drag before.
  - Transcode to XDJ runs as a queued batch on a worker thread, with progress, cancel, and a stuck-file prompt. It froze the window for the whole batch before. The batch dock shows the result.
  - `Repository` gains `approved_fields_by_file`, `field_proposals_by_file`, `analysis_results_by_file`, `files_by_id`, and `list_change_records_with_status`.
  - `run_transcode_with_confirmation` gains an optional `queue_batch` keyword. Without it, the function runs the transcode on the calling thread as before.
  - `transcode_files` gains optional `should_cancel` and `progress` keywords. `TranscodeBatchSummary` gains `stopped_early`. `BatchKind` gains `TRANSCODE`.
  - `tests/unit/test_ui_d78_layout_persistence.py` waits for the layout save before it opens the second window. The repository owner approved this edit.
  - The new `tests/unit/test_library_refresh_and_queue_performance.py` covers each change.
  - The version advances from 0.73.0 to 0.74.0 in `pyproject.toml` and `src/infiniteprep/__init__.py`.

### Fixed

- Latent defects in the online adapters and key removal (issue 288).
  - A network failure, a timeout, or a malformed response from MusicBrainz, Discogs, iTunes Search, or AcoustID fails that lookup only. The identification job continues with the other providers. AcoustID returns the failure as a warning. A failed lookup stays out of the cache. A response that is not a JSON object counts as malformed and returns no candidate. It raised out of the adapter before. The iTunes Search adapter runs the same check before its cache write.
  - Candidate ids from MusicBrainz, Discogs, and iTunes Search hold the file id. The same release found for two files collided on one candidate id before. Each id part is percent-encoded and joined with `:`. No two part tuples share an id.
  - Each Discogs track keeps its own candidate row. A Discogs year of 0 reads as a missing year. A track without a position takes its list position. An entry without a position keys its candidate with `#` and its list number. A heading before track `1` therefore keeps its own row.
  - A MusicBrainz release lookup returns a release candidate. It returned nothing after the request before. An unsupported entity type fails before any request. A missing track number reads as an unset track number.
  - The fpcalc step catches process, file, and parse errors only. A defect in the runner now surfaces.
  - The Beatport and ACRCloud stubs report unavailable with the reason. The stubs reported available while every lookup returned nothing.
  - `OllamaBootstrapService` gains an optional `base_url` keyword. The model list follows the configured server. A server address that fails the endpoint rules lists no models.
  - `forget_all_credentials` attempts every key after a failed delete. It stopped at the first failure before.
  - The new module `infiniteprep.adapters.http_json` holds the bounded HTTPS JSON fetch, the request limiter, and `cached_lookup`. The MusicBrainz, Discogs, and iTunes Search adapters run the cache check, fetch, JSON object check, and cache write through `cached_lookup`. `RequestLimiter` stays importable from the iTunes adapter.
  - The new `tests/unit/test_adapter_latent_defects.py` covers each change.
  - `test_discogs_never_caches_a_response_that_is_not_an_object` in `tests/unit/test_provider_and_llm_validation.py` checks for an empty result and a second request in place of a raised error. The repository owner approved this edit.
  - The version advances from 0.75.3 to 0.76.0 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- Requests that carry a credential no longer follow a redirect.
  - The Discogs, MusicBrainz, and AcoustID adapters and the LLM reasoning adapter send through the new `adapters/http_openers.py` opener. That opener holds no redirect handler. A redirect response raises an error. The three adapters pass it to the shared `adapters/http_json.py` fetch.
  - urllib copied every header, the Discogs token and the LLM Bearer key included, to the redirect target. A redirect could also move a request from https to plain http. The AcoustID key rides in the query string and followed every redirect too.
  - The LLM endpoint check covered the first URL only. A redirect now ends the request before it reaches a host the check did not see.
  - The version advances from 0.76.0 to 0.76.1 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- Status changes reach screen readers. Toolbar actions report each result (accessibility audit).
  - A playback status, a spectral analysis failure, a finished batch, and the write preview status each raise a screen reader announcement. A label update alone was silent before. The new `announce` helper in `ui/accessible_names.py` sends each announcement.
  - Identify & Analyze, Bulk Approve, Preview & Apply Writes, Transcode, and Export name the reason when no file is visible and nothing ran. Each ended without a word before.
  - Bulk Approve reports how many fields it approved.
  - The library status column shows labels such as "Ready to Write" instead of stored values such as `ready_to_write`. The detail header shows Yes or No for write support instead of True or False.
  - The new `tests/unit/test_status_feedback.py` covers the announcements, the empty library messages, the approved count, and the labels.
  - The version advances from 0.75.2 to 0.75.3 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- Warning text and the waveform reach WCAG contrast minimums (accessibility audit).
  - The detail panel warnings use a dark amber on a light theme and a light amber on a dark theme. The fixed amber read at about 3:1 on a light theme. Each choice reaches 4.5:1, and a theme switch picks the amber again.
  - Unplayed waveform bars use gray 500. Gray 600 read at 2.3:1 on the waveform background. The bars now reach 3:1.
  - The zoom view draws the low, mid, and high bands in Okabe-Ito sky blue, orange, and yellow. Red and green ran together for red-green color blindness. Each band reaches 3:1 at its overlay alpha.
  - The zoom view shows a legend that names each band next to its color. Color alone named the bands before.
  - The new `ui/contrast.py` computes WCAG contrast. A new unit test module for contrast and the legend checks every color pair and the legend.
  - The version advances from 0.75.1 to 0.75.2 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- The field rows of the detail panel keep keyboard and screen reader users in place (accessibility audit).
  - Each row's edit box and its Accept, Save, and Lock buttons name the row field, such as "Save artist". Every row announced the same "Save" before.
  - Accept, Save, and Lock keep focus on the same control of the same row. The rows rebuild after each action, and focus fell out of the table before.
  - Text typed in one row stays when another row's action runs. The rebuild wiped every unsaved row before. The acted row shows its new stored value.
  - The new `tests/unit/test_detail_panel_row_actions.py` covers the names, the focus, and the unsaved text.
  - The open list of each named picker carries the picker's name. A screen reader announced an unnamed list once a picker opened. The new `ui/accessible_names.py` names a picker and its list together.
  - The version advances from 0.75.0 to 0.75.1 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- Catalog, fingerprint, and LLM responses pass type and length checks before reaching a proposal.
  - The new `adapters/provider_fields.py` checks every field the Discogs, MusicBrainz, and AcoustID adapters read. Text stays printable and stops at 500 characters. A field of the wrong type reads as absent. A list keeps at most 100 items.
  - A response that is not a JSON object raises an error ahead of the cache write. A malformed body does not reach the shared cache.
  - An AcoustID score outside 0 to 1 reads as a missing confidence.
  - The Ollama model probe reads at most 1 MiB. It keeps a name only when the name passes the model reference grammar, and it lists each name once.
  - A provider number past the float range reads as absent.
  - AcoustID evidence stores only the checked recording fields. It stored the whole recording object before.
  - The LLM output check requires the selected ID and the proposed value to belong to one candidate. A value with no candidate value to match counts as invented. A non-string field or a field above 1000 characters fails.
  - A validated LLM answer returns the stored ID and value of its candidate. The rationale stays printable and stops at 500 characters. A failed request names only the exception class.
  - The version advances from 0.74.0 to 0.74.1 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- Wrong results and failed operations from the wholesale quality review (issue 288).
  - An unreadable subfolder no longer stops an import. A folder and a file inside it import the file once.
  - Silent or flat audio yields no key. The analyzer proposed A minor before.
  - A partial date such as `2020-05` writes to MP3, WAV, and AIFF. The read-back check compares the part ID3v2.3 holds.
  - A TXXX write replaces a frame with a description that differs only in case.
  - A file name with a colon, as macOS stores a Finder slash, writes and transcodes with a backup.
  - A transcode refuses a WAV or AIFF output past the tag write limit before it decodes. It failed after the full encode before.
  - A USB Resume or Restore records the journal action before it removes the drive marker. A failed journal write can retry.
  - An infinite number in drive inspection output falls back to the default size. A USB JSON read closes its descriptor once.
  - Each Win32 error carries its Windows meaning. A sharing violation read as "Broken pipe" before.
  - The macOS inspector reads stdout and stderr together. A child that filled stderr first stalled until the timeout.
  - The Windows inspector sets UTF-8 output. A non-ASCII volume label decoded as replacement characters.
  - The toolbar theme picker follows a theme the Settings dialog applied.
  - A failed job step reads failed with its error in the jobs table. It stayed queued before.
  - A failed queued write logs its traceback.
  - A library file that is not a database releases its handle. The evaluation run closes its database on an error.
  - New test modules `test_tag_write_round_trips.py`, `test_import_analysis_and_jobs.py`, `test_usb_recovery_and_runner_fixes.py`, and `test_library_filters_and_theme_sync.py` under `tests/unit/` cover these fixes.
- The transcoder, the tag reader, and the Ogg reader no longer hold a whole file or a whole tag value in memory.
  - The transcoder reads, resamples, and writes in blocks of about 262,000 frames. Each block reads enough frames on both sides to cover the resampling filter. The output equals the whole-file resample. One block stays in memory at a time.
  - A stream without a declared frame count gets a counting pass before the block transcode.
  - A raw custom tag value above 4096 characters shows as its first 200 characters, its length, and its SHA-256 digest. The summary reaches the library row and the Other Tags table only. A tag write reads the file itself.
  - Catalog matching compares the first 512 characters of an artist or a title. The cut runs before the case fold. No full copy of a long text forms.
  - The MP4 reader caps a tag data payload at 16 MiB, the same cap the ID3, FLAC, and Ogg readers apply during a parse. A larger payload reads as its size and is never copied or decoded. An integer payload longer than 8 bytes reads as binary.
  - The Ogg reader maps the file and touches only the pages it parses. It read the whole file before.
  - A new test covers the analyzer rejection of a sample rate above 384 kHz.
  - The version advances from 0.72.6 to 0.72.7 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- Enter in the USB export confirm dialog cancels the export. Enter confirmed the export before. Confirm Export takes an explicit click or Space.
- Enter in the interrupted USB export prompt leaves the drive unchanged. Enter resumed the export before. Resume and Restore take an explicit click or Space.
- Clean Up USB Recovery Journal asks before it deletes any entry. No is the default. The result shows in a message. The result showed only in a tooltip before.
- Apply Safe Writes with no safe plan keeps the write preview open and names the reason. The preview closed without a word before.
- A crafted audio header no longer claims unbounded decode memory or analysis time.
  - The local analyzer decodes in blocks and keeps only the mono mix. The memory in use follows the frame count, not the frame count times the channel count. The mono mix decodes into one buffer and never exists twice. A stream without a declared length decodes twice. The first pass counts its frames, and the second pass fills the buffer.
  - The local analyzer refuses a file that declares more than 8 channels or a sample rate above 384 kHz. It decodes at most 30 minutes at 48 kHz, about 691 MB of mono samples. A file above 48 kHz analyzes a shorter span.
  - The tempo estimate computes only the lags of the tempo range. A full autocorrelation of a 30 minute file needed over 100 billion steps.
  - The transcoder refuses a file that declares more than 8 channels, a sample rate above 384 kHz, or more decoded samples than a 2 hour stereo file at 48 kHz holds.
  - A decode that runs out of memory fails that one file. The analysis reports a decode failure, and a transcode batch continues with the next file.
  - The version advances from 0.72.3 to 0.72.4 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- Enter in the rename template field of the write preview refreshes the preview. Enter applied the writes and closed the dialog before. Refresh Preview holds the default button role.
- Tab leaves the library table in one press. Tab walked every cell before.
- Tab from a field edit box in the detail panel reaches the Accept, Save, and Lock buttons of the same row. Tab returned focus to the table and skipped the buttons before.
- Crafted tag data costs bounded time and memory in the tag parsers and the library table sort.
  - An ID3 text frame decodes at most 4096 values and at most 16 MiB of text. A larger frame shows as a byte summary in Other Tags. The ID3v2.3 renderer rejects a tag that holds such a frame.
  - An ID3 picture frame reads its description without a text split of the picture bytes.
  - The Ogg reader checks the packet size with a running total. A packet near the 16 MiB cap needed billions of steps before.
  - The library table sorts a text with a digit run above 4300 digits. Such a tag value stopped every table refresh before.
  - The version advances from 0.72.0 to 0.72.1 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- The library table sorts by Key and by Review Status when a row holds no value for the column (issue 288).
  - A file without a key and a file with a status outside the sort order join the unsorted group. The sort no longer raises.
- An in-place transcode updates the library record of the file (issue 288).
  - The record takes the path, the SHA-256 hash, the size, and the format of the new file. A later tag write accepts the file.
  - A blank approved value no longer erases the tag on the transcoded copy. The transcoder and the tag write share one rule for approved values.
- A WAV tag write keeps the LIST-INFO fields the write does not name (issue 288).
  - The write replaces the subchunk of each written field and keeps every other stored subchunk.
- The title parser reads a mix keyword only as a whole word (issue 288).
  - `Live in Dublin` and `feat. Meredith` no longer count as a mix.
  - A mix name built from descriptors alone, such as `Extended Club Mix`, yields no remixer.
  - A contraction, such as `It's Alright Mix`, yields no remixer.
- Bulk approve reads pending proposals only (issue 288).
  - An accepted, rejected, or superseded proposal takes no part in a new approval. A second run approves nothing twice.
- The library table and the playlist order skip rejected and superseded proposals (issue 288).
  - Both read the top proposal through the new `top_proposal_values` in `domain/metadata_values.py`.
- A re-analysis with a changed BPM or key no longer fails the file (issue 288).
  - The ID of an analysis proposal covers the whole unique key of the proposal table.
  - Only the newest result of each analyzer supplies proposals. An earlier pending value of that analyzer turns superseded.
- A file that processes after an earlier failure no longer keeps the old error message (issue 288).
  - `Repository.update_file_status` takes the new `clear_error` keyword and updates the row in one statement.
- Six new test modules under `tests/unit/` cover these fixes.
- The version advances from 0.72.2 to 0.72.3 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- The change journal and the recovery prompt show local time.
  - Both tables showed the stored UTC text with microseconds and an offset. Both tables now show the local date and time of this computer. Stored values stay unchanged.
  - A MusicBrainz release date yields a year only when it starts with four digits followed by the end or a hyphen. Any other shape yields no year.
  - `WriteService`, `MetadataTagAdapter`, and `SafeTranscoderAdapter` accept an optional `now_iso` clock. Each default is `utc_now_iso`. Every journal timestamp therefore carries the fixed-width UTC form.
  - The version advances from 0.71.1 to 0.72.0 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- File names and decoder warnings show as literal text in the detail panel and the transcode dialogs.
  - The detail panel header and warnings labels used the default Qt text format. A file name with HTML tags rendered as markup. Both labels now use the plain text format.
  - The transcode confirmation and the transcode summary now use the plain text message helpers. Both dialogs list file names from imported files.
  - The version advances from 0.71.0 to 0.71.1 in `pyproject.toml` and `src/infiniteprep/__init__.py`.

## [0.71.0] (2026-11-16)

### Added

- The Microsoft Security DevOps scan reports its result as a pull request comment.
  - The new `scan-comment` job in `.github/workflows/security-devops.yml` posts the comment after a passed scan and after a failed scan. A passed scan with no finding still gets a comment.
  - The comment holds the result, the finding total, the findings by tool, and a link to the workflow run. The job updates the comment on every push and removes a second comment from the bot.
  - The new `scripts/build_security_scan_summary.py` builds a bounded JSON document from the SARIF report. The comment job validates the document before it posts and builds the Markdown itself.
  - The scan job holds no pull request permission. The comment job holds `pull-requests: write` and checks out nothing.
  - A light change runs no scan and gets no new comment. The job skips a pull request from a fork.
  - `tests/test_build_security_scan_summary.py` covers the builder. `tests/test_security_scan_comment.py` covers the wiring and runs the comment script under Node against a stubbed API.
  - The version advances from 0.70.0 to 0.71.0 in `pyproject.toml` and `src/infiniteprep/__init__.py`.

- The pull request checks report the code coverage of the application.
  - The Ubuntu leg of the test matrix runs the suite under `coverage run -m pytest`. The report costs no second suite run. The other legs run the plain suite.
  - The coverage step writes a bounded JSON document with the total and the files with the least coverage. The new `scripts/build_coverage_summary.py` builds the document from the coverage report.
  - A separate `coverage-comment` job posts the table as a pull request comment. The job updates the comment on every push and removes a second comment from the bot.
  - A pull request from a fork receives a read-only token. The comment job skips those pull requests. The report stays in the artifacts.
  - The job validates the document before it posts. The document holds only the expected fields. Every number sits in the allowed range. Every path holds only safe characters. The file list and the comment length stay inside the caps. The job builds the Markdown itself. A file name never ends the code fence.
  - The job runs one at a time for a pull request. A newer push cancels the older job. The comment carries the run that wrote it. An older run never replaces a newer result.
  - The job that runs the suite holds read-only permissions. The comment job holds `pull-requests: write` and checks out nothing. No test code can post on the pull request.
  - The HTML report and the XML report go into artifacts with a three-day retention.
  - The `coverage==7.17.3` development dependency carries the Apache-2.0 license. It stays out of the runtime dependencies and out of the app bundle.
  - The coverage number never gates a pull request. No coverage threshold applies. The reporting and the comment run as ordinary steps. A failure in either fails the job like any other step.
  - The version advances from 0.67.1 to 0.68.0 in `pyproject.toml` and `src/infiniteprep/__init__.py`.

- USB exports persist `.infiniteprep/manifest.in-progress.json` and `.infiniteprep/manifest.json` with a canonical SHA-256 `My Tags` digest (D116 to D121, D139 to D148, D179, D200, issue 157). The computer-side recovery journal at `usb_recovery_journal.json` matches reconnecting USB drives and gates changed paths behind confirmation (D166, D191, D192, issue 157). `UsbRecoveryDialog` offers Resume, Restore, and Leave unchanged actions with plain-text drive labels. `StorageTab` adds 30-day expiry settings, 1-day advance warning notices, launch overdue cleanup notices, and manual journal cleanup (D193 to D198, issue 157). The version advances from 0.65.1 to 0.66.0 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- Written key tags use Camelot, Open Key, or Standard notation (issue 128).
  Camelot is the default. Camelot and Open Key can use a leading zero.
  Written BPM supports integers or one or two decimal places with half-up rounding.
  Import parses Beatport and Essentia key styles and warns about unreadable key text.
  Settings > General exposes these write preferences to the desktop app.
- The iTunes Search adapter supplies a coarse genre and a release-date fallback (D404, issue 243).
  - The new `adapters/catalog/itunes.py` queries `https://itunes.apple.com/search` with the shared D221 User-Agent. The lookup sends the artist name and the track title as the search term, plus the storefront country.
  - The adapter waits 3 seconds between calls for the published limit of about 20 calls per minute. It caches every response in the shared SQLite cache for 24 hours.
  - The matching engine ranks the rows over the artist and the title. The earliest release date wins among the rows that tie for the best match.
  - The adapter reads `primaryGenreName` and `releaseDate`. It keeps no artwork from the response because Apple limits promotional content, album art included, to promoting store content. The shared cache holds the read fields only. No artwork URL stays in it.
  - A row supplies a value only when it matches the artist and the title. The mean of the two field similarities reaches the match score. The candidate keeps that score.
  - The request path follows no redirect, and the request timeout is a named constant.
  - The limiter lives in the process. A second adapter or a second thread waits for the same interval.
  - The provider slot holds the cache check and the request together. A second thread that misses the cache finds the cached response and sends no second request.
  - Every proposal records iTunes Search as its source. The provider name is `itunes` and the entity id is the track id.
- The new `domain/source_priority.py` holds the per-field order of the sources (D394, issue 243).
  - The genre order is Discogs, then iTunes. The release date order is MusicBrainz, then Discogs, then iTunes.
  - A higher ranked source keeps the field. A lower ranked proposal for that field does not reach the proposal table. An owner-set value outranks every provider.
  - The table also limits the fields a provider may supply. The iTunes adapter proposes the genre and the release year only.
  - A stored proposal from a lower ranked source turns superseded when a higher ranked source holds the field. The rule also covers a source that no longer produces a proposal. An approved value from a higher ranked source holds the field too, and an owner-set value holds it on its own.
- The text similarity helper moves to `domain/similarity.py`. `services/matching_engine.py` keeps its name and its behavior.
- The Identification settings tab names the host and the query data of the iTunes Search lookup. The README holds the same disclosure in a new section.
- `tests/unit/test_itunes_search_adapter.py`, `tests/unit/test_itunes_endpoint.py`, `tests/unit/test_source_priority_filter.py`, and `tests/unit/test_itunes_disclosure.py` cover a match with a genre and a date, the empty response, an unrelated row, a malformed duration, a field another source already supplied, a response with artwork, the rate limit, the cache, the redirect refusal, and the provenance on each proposal. The recorded responses live under `tests/fixtures/itunes_search/`.

### Changed

- Two pinned Python packages advance.
  - `requirements-lock.txt` pins `pyinstaller-hooks-contrib` 2026.10 for the application environment. The previous pin was 2026.9.
  - `requirements-sbom.txt` pins `jsonpointer` 3.12 with the SHA-256 of its wheel for the SBOM tool environment. The previous pin was 3.10.
  - Both file headers state that the two files lock separate environments. A package that both environments need appears in both files at the same version.
- The application lockfile pins the SHA-256 digest of every artifact PyPI publishes for each pinned release.
  - `requirements-lock.txt` lists the digest of each wheel and source distribution under its pin. pip verifies the bytes it installs against that list.
  - The `ci` and bundle jobs install the file with `--require-hashes`. A substituted artifact fails the install instead of reaching the build.
  - `tests/test_requirements_lock_hashes.py` checks that every pin carries a digest and that pip rejects an artifact with a digest that the file does not name.
- The foucault security review pin advances from 3.8.1 to 3.8.8.
  - `.github/workflows/security-review-pr.yml` pins both `uses:` references and both `audit_ref` values to commit `d50c22c6589fa6843e1bf50d3da8b29b637a1395`.
  - `ci/build_pr_case.py`, `ci/call_model.py`, and `scripts/check_pr_review_response.py` match that commit byte for byte. The other three adapter files carry no upstream change.
  - `tests/test_review_wiring.py` holds the new commit and the new SHA-256 digests. `docs/pr-review.md` lists the new pin.
  - The version advances from 0.68.0 to 0.68.1 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- The theme picker lists the themes the build can apply, and a theme that cannot apply stays out of the picker (issue 247).
  - `apply_application_theme` returns a result that names the theme in effect and the reason a theme did not apply.
  - The main window and the Settings dialog keep the picker on the theme in effect. A failed choice reapplies the theme that was in effect. The app keeps its look. The stored theme names the theme in effect. A failed application logs a warning that names the theme and the reason.
  - A failed theme choice never leaves a picker or a settings file naming a theme that the app does not use.
  - The theme picker reads one shared list of choices. The main window and the Settings dialog offer the same themes.
  - The version advances to 0.66.1 for the theme picker.
- The version advances to 0.67.0 for the iTunes Search fallback. `pyproject.toml` and `src/infiniteprep/__init__.py` hold the new value.

### Fixed

- A wrong computer clock no longer removes USB recovery entries.
  - The expiry scan keeps an entry with a start time after the current time. It also keeps an entry older than its expiry limit plus 365 days.
  - The launch scan shows a "USB Recovery Clock Check" notice that lists the kept entries. Settings > Storage still removes those entries by hand.
  - `UsbExpiryScanResult` gains the optional `clock_suspect_entries` field. Three-argument construction keeps working.
  - The version advances from 0.68.1 to 0.69.0 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- The library database no longer depends on the computer clock for record order.
  - A change journal record keeps the time of its first write. A later status update no longer replaces that time.
  - The change journal and the pending job list follow insertion order. A backward clock step no longer reorders either list.
  - Schema migration 3 adds the `updated_at` column to the change journal. The column holds the time of the latest write. Each existing row starts at its own timestamp. `ChangeRecord` gains the optional `updated_at` field.
  - `utc_now_iso` always emits microseconds. The fixed width keeps text comparison equal to time comparison.
  - `Repository.upsert_change_record` converts a caller timestamp with any UTC offset to that stored form. A value without an offset raises `ValueError`.
  - `Repository.set_cached_response` converts `expires_at_iso` the same way. A value without an offset raises `ValueError`.
  - The version advances from 0.69.1 to 0.70.0 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- The version advances from 0.69.0 to 0.69.1 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- Hardened USB recovery journal launch and Storage settings cleanup against corrupt or unreadable journal files while preserving data on disk. Preserved resolved recovery records across expiry and manual cleanup, added bounded durable cleanup history records, confined journal parent directories under the storage root, and normalized manifest and journal timestamps to UTC. The version advances from 0.67.0 to 0.67.1 in `pyproject.toml` and `src/infiniteprep/__init__.py`.
- The theme picker changes the app in a packaged build (issue 247).
  - The packaged app shipped without the `qt-material` package. Every theme choice fell back to the native look. The picker showed a theme that the app never applied.
  - `infiniteprep.spec` now names `qt_material` as a hidden import and points PyInstaller at `packaging/pyinstaller_hooks/`. The new `hook-qt_material.py` keeps the two theme files, the style sheet template, and the icon sources next to the package in every bundle.
  - The Windows, macOS, and Linux bundle allowlists name the theme data, the Qt interface loader binding that the theme package imports, and the MarkupSafe extension that its template engine uses.
  - The Roboto fonts ship beside the theme files in every bundle. The theme package does not ship the license text for the fonts. The bundle carries a pinned copy of the Apache-2.0 text next to the font files.
  - NOTICE names the shipped qt-material and MarkupSafe versions. The notice listed the versions from before the theme package shipped.
  - The packaging hook stops the build when the theme package has no Roboto fonts.

## [0.64.0] (2026-11-15)

### Added

- Every library table column sorts in both directions (issue 248).
  - A header click sorts that column ascending. A second click on the same header reverses the direction. The header shows the sort indicator for the active column and direction.
  - The table opens sorted ascending by filename with a natural order. `Track 2` comes before `Track 10`.
  - Duration sorts by seconds. BPM sorts by number and keeps the decimals. The key column sorts by the active notation. The Camelot notation sorts by the wheel position. The Open Key notation sorts by the number. The standard notation sorts by the pitch class upward from C like the piano keys. The review status column sorts by the workflow order. The remaining columns sort by a natural case-insensitive text order.
  - Rows that tie order by title and then by file path. Two keys at one wheel position keep the minor key first in both directions.
  - The file ID rides on the first cell of each row. A row click opens the file in that row. The sort column, the sort direction, and the selected file survive a search, a status filter, an import, an analysis, and a write.
  - The new `unsorted_rows_placement` setting places the rows without a value for the sorted column. The setting holds `first` or `last`. The General tab of the Settings dialog holds the choice. The rows form one group that moves with the sort direction. A BPM that is not a finite number, such as `NaN`, counts as a missing value.
  - `src/infiniteprep/ui/table_sort.py` holds the typed sort keys and the row order. `tests/unit/test_ui_library_table_sort.py` and `tests/unit/test_ui_unsorted_rows_placement.py` cover the sort of every column, the selection, the refresh, and the setting.
- Library storage supports named playlists, crates, and nested folders (issue 139).
  - Playlists keep track order and support optional repeats. Crates contain unique tracks.
  - Playlists can sort by key or BPM and restore the previous order after a restart.
- The README roadmap lists the 20 stage issues in order, with links and a pointer to the task index (D356, issue 179). The 24-item list goes away. The settings walkthrough names the field on each Settings tab.

- Library schema migrations track the schema version in SQLite `user_version` (D349, issue 175).
  - The new `db/migrations/` package lists numbered steps in order. Migration 1 runs the existing `schema.sql`. A library from an earlier release reads as version 0 and reaches version 1 with no schema change.
  - Every pending step and the version stamp run in one transaction. A failed step rolls back the whole run and raises `MigrationFailedError`.
  - Each migration run first writes one verified backup beside the library as `infiniteprep.sqlite3.pre-migration-backup`. A one-row record table inside the backup holds the source version and per-table row counts. One rename publishes the copy and its record. Restore removes that table from the copy it installs. Verification checks `integrity_check`, the version, and the row counts from a literal query registry. The next start with nothing pending deletes the backup.
  - A library from a newer app raises `NewerLibraryError` before any change. The startup prompt offers Restore when a usable backup exists. Restore keeps the newer library and its WAL and SHM files as `infiniteprep.sqlite3.newer-<UTC timestamp>` and installs the backup. A failed rename moves every file back.
  - `restore_pre_migration_backup()` serves the update rollback in UPD-3 (issue 92). `open_storage` takes an optional `confirm_restore` callback.
  - `tests/unit/test_schema_migrations.py`, `tests/unit/test_newer_library_restore.py`, `tests/unit/test_restore_robustness.py`, and `tests/unit/test_row_count_registry.py` cover a fresh library, an existing version-0 library, a two-step migration, a failed migration with the backup intact, a refused newer library, backup verification, cleanup, and restore.
- Added fake-lossless audio detection under `src/infiniteprep/services/fake_lossless.py` (D332, issue 99). The detector analyzes lossless tracks for spectral cutoff, MP3 lowpass shelf, and AAC SBR gap traces behind a pluggable `FakeLosslessDetector` interface.
- Added user-selectable `fake_lossless_method` in `AppSettings`, `AnalysisTab`, and `SettingsDialog`.
- Wired fake-lossless diagnostics into `WaveformAnalysisPool`, `DetailPanel`, `MainWindow`, and non-blocking transcode preflight notices in `action_handlers.py`.
- Added generated-fixture tests in `tests/unit/test_fake_lossless.py`.
- `docs/decision-register.md` records D402 to D406. The entries close the issue 222 source research. MusicBrainz keeps CC0 core data only. Cover Art Archive supplies artwork. iTunes Search supplies a genre and release-date fallback. Deezer leaves the roadmap.
- The Settings dialog splits into tabs, one module per area (D351, issue 176).
  - The new `ui/settings/` package holds one tab module each for General, Playback, Analysis, Transcode, Identification, Updates, and Storage. A later setting edits one tab module.
  - General holds the UI theme. Analysis holds the batch stop rule. Transcode holds the transcode mode, the preset, and the playlist relative paths. Identification holds the AcoustID key, the Discogs token, and the local LLM inputs. Storage holds both USB export reserve inputs and the data cleanup button.
  - Playback and Updates each show a one-line note until a setting joins the tab.
  - Every setting keeps its stored value and its save behavior. `SettingsDialog` keeps its constructor, its widget attributes, and its module constants.
  - `tests/unit/test_settings_tabs.py` opens each tab, finds every existing setting on its tab, and saves and reloads each setting unchanged.
- `tests/fixtures/synthetic_audio.py` builds seeded audio fixtures at test time (D359). Later tasks build audio test files with this module.
  - The signal builders cover click tracks with tempo changes and downbeat accents, tones, chords, full-band and band-limited noise, and an MP3-like lowpass shelf at 16 kHz. `write_wav_fixture` writes PCM_16 WAV through soundfile.
  - The container builders cover ADTS AAC LC, AAC and ALAC M4A, STEM.MP4, Ogg Vorbis, and Ogg Opus. A STEM.MP4 holds a mixdown, four stems, and a stem manifest. Ogg pages carry real CRCs. Codec payloads are PCG64 filler. Opus packets are valid empty CELT frames.
- `tests/unit/test_synthetic_audio.py` passes each container through its app reader. The ADTS test walks the frame chain because the app has no ADTS reader. `tests/unit/test_synthetic_audio_opus_toc.py` pins the Opus TOC bytes to RFC 6716.
- `packaging/release.py` gains `RELEASE_JOB_PERMISSIONS`, a read-only mapping of the release job permissions (D355).
- `tests/test_release_constants.py` pins the six release package names, the Sigstore bundle names, and the five release job permissions by hand (D355). The file also checks that `RELEASE_JOB_PERMISSIONS` matches the release job permissions in `build-packages.yml`.
- The version advances to 0.60.0 for the Settings tabs, to 0.60.1 for the synthetic audio fixtures, to 0.60.2 for the bundle trim, to 0.60.3 for the source research decisions, to 0.60.4 for the release test constants, to 0.61.0 for fake-lossless audio detection, and to 0.62.0 for the schema migrations. `pyproject.toml` and `src/infiniteprep/__init__.py` hold the new value.

### Changed

- `SECURITY.md` names the supported builds, the scope, the application surfaces, the credential storage, and the report hygiene.
  - The supported builds are the latest `main` branch build and the latest tagged release. Pre-releases and earlier tagged releases receive no support.
  - The scope section replaces the repeated template sentences with prose that covers the same ground.
  - The new application section names audio and tag parsing, tag writing to disk, the network services, the local LLM endpoint, update downloads, and the `fpcalc` bootstrap. Each line names the data the surface handles.
  - The AcoustID key and the Discogs token live in the OS credential store. Reporters must exclude credentials, tokens, and personal data from report attachments.
  - The agent policy security section goes away. The file ends with the disclosure policy.
  - `docs/agent-policy/security.md` points at `SECURITY.md` instead of `SECURITY.md.example`.
  - `docs/template-drift.md` records both divergences from the source template.
  - The version advances to 0.64.0. `pyproject.toml` and `src/infiniteprep/__init__.py` hold the new value.
- `tests/test_release_workflow.py` reads the release job permissions and the package count from `packaging/release.py` (D355). `test_release_gathers_every_package_and_writes_checksums` replaces the six-package test name.
- `tests/test_release_review_fixes.py` reads the package and bundle names from `expected_packages` and `expected_bundles` (D355). `PACKAGES`, `BUNDLES`, and `PACKAGES_AND_BUNDLES` replace the literal six-name lists. Two test names drop the counts six and twelve.

### Fixed

- MP3, FLAC, WAV, and AIFF tag writes verify the audio payload with SHA-256 (issue 131).
  A mismatch discards the staged write, preserves the source, and records a failure.
- The Windows, macOS, and Linux bundles no longer carry pytest or pygments.
  - scipy imports pytest inside `PytestTester`. The pytest hook collected all of pytest and pygments into every bundle. The app skips the scipy test runner and never imports either package.
  - `infiniteprep.spec` now excludes `pytest`, `_pytest`, and `pygments` through `TEST_ONLY_PACKAGES`. Both packages stay dev dependencies for the test suite.
- The 0.59.3 entry names its own version. It previously named 0.59.1.
- The `requirements-lock.txt` header names v0.58.0. That release last changed the pins when it added Pillow. The header previously named v0.57.0.
- Added `tests/test_bundle_excludes_test_tools.py`.

## [0.59.3] (2026-11-14)

### Added

- `docs/decision-register.md` records D394 to D401. The entries turn user stories from another DJ tool into issues 222 to 229. The entries cover source research, protected fields, identification order, duplicate and health checks, watched folders, Energy hotkeys, and preselected proposals.

### Changed

- The landing site shows the InfinitePrep logo and uses the logo palette.
  - `website/logo.png` holds a byte copy of `docs/infiniteprep-logo.png`. The hero note displays it. The header shows the favicon beside the site name.
  - The page colors move from neutral gray to the logo navy, royal blue, and sky blue. Labels, the primary links, and the focus outline use the sky blue accent.
- `test_the_site_folder_holds_the_five_site_files` replaces the four-file name in `tests/test_pages_deployment_workflow.py`. `test_the_page_shows_the_project_logo` checks the logo copy and its image tag.
- The two reusable review workflows move to the releases the pinned commits
  name.
  - The foucault caller moves to `3ec8d0e3`. The new pin names foucault 3.8.1.
    Both caller jobs keep the same inputs and the same permissions.
  - The euler caller moves to `940c53a3`. The new pin names euler 0.9.0.
  - The foucault adapter files are byte copies of the pinned commit.
    `ci/build_retry_case.py` is new. A response that fails validation retries
    with the validator problems in trusted context. `REVIEW_TARGET` stays
    byte-identical.
  - `ci/model_providers.json` drops the unused `kimi-k2.7-code` profile and
    the `large_input_provider` key. `ollama-large` with `minimax-m3.1-flash`
    is the one active profile. Input above its budget still ends at exit code
    3 and a fixed `NEEDS-HUMAN` verdict.
  - `tests/test_review_wiring.py` pins the new commits and the six adapter
    digests. `docs/pr-review.md` records the new releases, the adapter list,
    and the capacity path.
  - `docs/dependabot.md` states the reusable workflow rule without the stale
    euler claim.
- The version advances to 0.59.3. `pyproject.toml` and
  `src/infiniteprep/__init__.py` hold the new value.

### Fixed

- The Windows, macOS, and Linux bundles shipped Pillow and its native libraries. `packaging/check_bundle.py` failed every bundle job.
  - scipy imports pytest. The pytest hook pulls in pygments. The pygments hook collects `pygments.formatters.img`. That formatter imports `PIL` inside a `try`/`except ImportError` block.
  - `infiniteprep.spec` now excludes `PIL` through `BUILD_ONLY_PACKAGES`. Pillow stays a build-only dependency for the icon conversion (D393).
- Added `tests/test_bundle_excludes_pillow.py`. Its spec reader raises `ValueError` for an `excludes` part that is not a list and for any operator other than `+`.

## [0.58.0] (2026-11-13)

### Added

- The InfinitePrep logo is the app icon and the website favicon (D393).
  - Every window and the taskbar entry show the logo. `ui/app_icon.py` loads the 256x256 `ui/app-icon.png` at startup. A missing icon file logs a warning and keeps the platform default icon.
  - The Windows exe, the macOS app, and the Linux AppImage carry the logo. The AppImage icon replaces the D287 placeholder.
  - The Windows setup.exe and Uninstall.exe show the logo. `installer.nsi` requires `/DICON_FILE`.
  - `packaging/make_icon.py` writes the AppImage PNG and the installer ICO. It stops the build unless the environment holds Pillow 13.0.0.
  - `website/favicon.png` holds the 64x64 favicon. `website/index.html` links it.
  - Each bundle allowlist admits `_internal/infiniteprep/ui/app-icon.png`.
  - `pillow==13.0.0` joins the dev dependencies and the lockfile. PyInstaller uses it to convert the PNG icon.
- Added `tests/unit/test_app_icon.py`, `tests/unit/test_make_icon.py`, and `tests/test_app_icon_wiring.py`.
- `test_the_site_folder_holds_the_four_site_files` replaces the three-file name in `tests/test_pages_deployment_workflow.py`.
- `README.md` shows the centered InfinitePrep logo from `docs/infiniteprep-logo.png` above the title.

- `docs/decision-register.md` records D390 to D392, the stuck-file choices of PR 213. D244 points to the new entries. D240 points to D380 for when its report opens. D388 and D389 stay unused.

### Fixed

- A queued Analyze or write batch that hung on one file had no way out after **Cancel and Exit**. The window waited on that file until an outside kill ended the process. Analyze and write batches now raise the same Quit Now or Keep Waiting prompt that imports raise under D244.
  - Analysis allows 5 minutes per file. Writes allow 2 minutes per file. Imports keep 2 minutes.
  - The write prompt states that files written so far stay written and that **Recover Interrupted Writes** offers the interrupted file at the next launch.
- `ui/stuck_file_watcher.py` holds the shared time limit and prompt. `FolderImportController` and `BatchQueueController` both use it. `BatchQueueController` accepts optional `prompt_parent`, `stuck_prompts`, and `exit_process` arguments.
- Added `tests/unit/test_ui_batch_stuck_prompt.py`.
- A push to `main` no longer cancels the running `os-tests.yml` run of the previous `main` commit. Each `main` commit gets its own group and its platform tests always complete. A newer pull request commit still replaces the older run.

### Changed

- A documentation-only pull request skips the security review and the quality review. A `classify` job in `security-review-pr.yml` and `quality-review-pr.yml` runs the classifier from the default branch. The job fetches the pull request head commit as Git objects, and no pull request file reaches a working tree. Each workflow publishes its check with the `skipped` conclusion. A website change, a mixed change, and a failed classification keep both reviews. An unapproved fork pull request gets no fetch.
- The USB drive class rule accepts a USB, SD, or MMC bus with or without the removable flag (D372, #207). Portable SSDs, USB hard disks, and flash sticks that report as fixed disks now pass on Windows, Linux, and macOS. The system and boot refusal, the MBR rules, and the FAT32 rule stay.
- A drive without the removable flag carries a fixed-disk note. The confirm dialog lists it under **Notes**. The drive picker adds it to the drive row. `PreflightResult` gains a `notes` field with `Note` and `NoteCode`.
- The Windows inspector sets the removable flag from the volume `DriveType` only. A USB disk with a Fixed volume reports as not removable.
- Breaking: `RefusalCode.NOT_REMOVABLE` becomes `RefusalCode.UNSUPPORTED_BUS`. `is_removable_class` becomes `is_supported_bus_class`. D207 permits breaking changes before 1.0.0.

## [0.57.3] (2026-11-12)

### Added

- `docs/decision-register.md` records D372, the USB drive class rule of issue 207, and D373 to D387, the batch queue decisions of issue 109. D239 and D241 now read as superseded. The register omits D388 and D389. Both recorded the PR count and the branch of PR 202.

- USB export confirm dialog in `ui/usb_preflight_dialog.py` (USB-4, #156). The dialog names the target drive, the DJ profiles, the planned changes, and the space figures. A refusal disables the confirm button. A safety refusal hides the drive identity and the space figures. Facts without a source show as not verified.
- USB export drive picker in `ui/usb_drive_picker_dialog.py`. The picker lists removable-class drives only. A refused drive shows its first reason. The picker disables a drive with a failed probe and shows fixed text.
- `services/usb/targets.py` selects the inspector and the probe for the current platform and loads the removable-class drives.
- USB export reserve settings `usb_reserve_percent` and `usb_reserve_bytes` with two inputs in the Settings dialog. An invalid stored value loads as zero. `SettingsManager.update` rejects an out-of-range reserve.
- A batch queue runs one library-changing batch at a time (INFRA-1, #109). Identify & Analyze, folder import, and Apply Safe Writes join the queue. Each batch runs on its own worker thread, and later batches wait in order. The queue lives in memory. Waiting batches end with the app.
- The **Batch Queue** dock lists the running, waiting, and finished batches with progress. **Move Up**, **Move Down**, and drag and drop reorder waiting batches. **Cancel Batch** removes a waiting batch. On a running Analyze or write batch it stops after the current file and keeps the finished files. On a running import it rolls back the whole import.
- A finished batch stays in the dock with counts. **View Result** opens the report. **Clear Finished** empties the list.
- **Settings & Providers** adds **Stop an Analyze or write batch at its first failed file**, off by default. Imports ignore the setting. `settings.json` stores it as `stop_batch_on_first_failure`.
- `BatchQueue`, `QueuedBatch`, `BatchKind`, and `BatchState` in `services/job_orchestrator.py`. `JobOrchestrator.process_files_continuing` treats any error type as one failed file. `BatchJobSummary` adds `stopped_early`.
- `WriteService.apply_batch_continuing` continues past a failed write and returns a `WriteBatchResult`. A public `WriteService` method exposes the pending recovery check.
- `ui/batch_queue.py` holds `BatchQueueController` and `BatchWorker`. `ui/batch_queue_dock.py` holds `BatchQueueDock`.
- `MainWindow` accepts an optional `analyze_runner` and adds `queue_batch` and `show_batch_result`. `WritePlanDialog` accepts an optional `submit_plans`. `SettingsDialog` accepts an optional `cleanup_enabled`. `FolderImportController` accepts an optional `show_progress_dialog` and adds a `progress` signal.
- Added `tests/unit/test_batch_queue.py`, `tests/unit/test_batch_queue_analyze.py`, `tests/unit/test_write_batch_continue.py`, `tests/unit/test_batch_failure_setting.py`, `tests/unit/test_ui_batch_queue_dock.py`, `tests/unit/test_ui_batch_queue_window.py`, `tests/unit/test_ui_batch_queue_drag_race.py`, and `tests/unit/test_ui_queued_writes.py`.
- Selected-track spectral analysis runs in a two-worker background pool. The waveform view adds persistent zoom windows and keeps the current overview visible while analysis runs or fails.
- Guard zoom rendering for empty analyses and keep playback errors separate from waveform status messages.
- Reject non-file waveform sources and validate persisted setting types and supported ranges.
- `SettingsManager` writes the settings file through a temporary file and an atomic replace. A failed write keeps the previous file intact.
- Closing the detail panel cancels waveform analysis and keeps its worker pool usable when the panel reopens. The panel and application quit wait at most two seconds for workers.
- The waveform worker extracts the fallback overview before spectral analysis. The overview shows real peaks while analysis runs and after analysis fails.
- `SettingsManager.update` raises `ValueError` for an invalid value and writes nothing.
- The overview extractor decodes regular files only.
- USB export preflight policy in `services/usb/preflight.py` (USB-4, #156). The policy refuses non-removable drives, system and boot volumes, active partitions, read-only media, filesystem errors, unknown layouts, non-FAT32 filesystems, and unknown database formats.
- A file of 4 GiB or more blocks the whole export.
- Conservative space estimate in `services/usb/space.py`. The reserve is the larger of a percentage of the estimate and a fixed byte count. Both start at zero.
- Pre-write recheck in `services/usb/recheck.py`. A changed drive identity, mount path, or writability aborts the export. The recheck reads writability from the fresh mount path on every call.
- Optional preflight facts on the USB inspection models.
- Read-only preflight probes in `services/usb/linux_probe.py`, `macos_probe.py`, and `windows_probe.py`. The probes add system, active, primary, hardware ID, and filesystem health facts. No probe needs raw device access.
- The Linux probe infers filesystem health from the kernel mount state. A read-only mount on writable media refuses. The guidance names a filesystem error and an intentional read-only mount as causes. The macOS probe leaves active, primary, and health facts unverified. The Windows probe reads `HealthStatus`.

### Changed

- Identify & Analyze and Apply Safe Writes no longer block the window. A queued batch continues past a failed file by default.
- The main window shows import progress in the dock. The modal import progress dialog no longer opens there. The stuck-file prompt stays.
- No report opens when a batch ends. The import report and the import failure message open from **View Result**.
- Closing the window while a batch runs or waits offers **Keep Working** or **Cancel and Exit**. The earlier wait-then-close choice is gone. `MainWindow.ask_close_during_import` now returns only `cancel` or `stay`.
- `MainWindow.start_folder_import` queues the import and always returns `True`.
- While a batch runs or waits, Transcode to XDJ, Change Journal & Restore, Bulk Approve, field edits, and Delete InfinitePrep Data stay disabled.
- The `tests-windows` and `tests-macos` jobs move from `sync-check.yml` to the new `os-tests.yml`. That workflow omits the `edited` event. A pull request title or description edit no longer cancels or reruns a platform job.
- A documentation-only or website-only change skips both platform jobs and the advisory AgentLint step in `sync-check.yml`. Every blocking check in `sync-check.yml` still runs.
- The `check-sync` job probes the configured hook launchers on Linux.

### Fixed

- The `resolve-pr` job in `security-review-pr.yml` skips a cancelled `ci` run. A newer commit cancels the `ci` run of the older commit, and that run no longer names a pull request head. Each such run failed the security review workflow.

## [0.54.0] (2026-11-11)

### Added

- The command execution gate returns an allow verdict for inspecting workflows. The verdict covers unittest runs, Ruff checking forms, `rg` searches inside the repository, and allow entries in `hooks/workflow-scripts.txt`. The files follow `abuzucom/agents` at commit `88f58b5`.
- `hooks/workflow-scripts.txt` lists the repository scripts the gate recognizes. Nine InfinitePrep entries cover `scripts/check_action_versions.py`, `scripts/classify_changed_paths.py`, `packaging/release.py`, `packaging/check_bundle.py`, and `ci/call_model.py`.
- `tests/test_workflow_allow_verdict.py`, `tests/test_compaction_disclosure_form.py`, and `tests/test_trusted_gh_connectivity.py` arrive from the template.
- `.github/PULL_REQUEST_TEMPLATE.md` and `.github/ISSUE_TEMPLATE.md` arrive from the template.
- `scripts/classify_changed_paths.py` sorts a commit range into the `docs`, `website`, `light`, or `full` change class. A version-only edit to `pyproject.toml` or `src/infiniteprep/__init__.py` keeps a range out of the `full` class.
- The `release` job in `build-packages.yml` publishes a GitHub build-provenance attestation for each of the six packages and `SHA256SUMS.txt` with `actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6` (v4.2.2, D325).
- The `release` job uploads each package's Sigstore bundle as `<package>.sigstore.json` to the draft release, with its hash recorded in `SHA256SUMS.txt` (D328).
- `packaging/release.py check-assets` learns the Sigstore bundle names and supports `--require-bundles` to verify that the folder holds all six packages and six Sigstore bundles (D328). Without the flag, a bundle counts as an extra file.
- `tests/test_release_bundle_flag.py` checks the explicit bundle requirement and the asset count in the `check-assets` success line.
- The Releases section in `README.md` documents package provenance verification commands with `gh attestation verify`.
- `tests/test_release_attestation.py` checks workflow permissions, actions/attest commit pinning, subject paths, bundle copying, asset verification, and README verification documentation.
- The `release` job verifies each Sigstore bundle reused from a draft release against its package with `gh attestation verify --bundle` before reuse. A failed download, an empty bundle, or a failed verification stops the job.
- `tests/test_release_bundle_reuse.py` checks the strict shell mode and the reuse verification of the bundle-saving step.

### Changed

- `AGENTS.md`, the five documents under `docs/agent-policy/`, and the eight generated copies follow the trimmed template policy text.
- `scripts/check_gate_adoption.py`, `scripts/check_hook_coverage.py`, `scripts/run_tests.py`, `scripts/trusted_gh.py`, and `scripts/trusted_git.py` follow the template.
- Six gate tests follow the template. Each edit received active human sign-off.
- `docs/template-drift.md` records template commit `88f58b5`.
- The copied hooks, scripts, and tests name each numeric literal. The template made that change for the Euler M11 prescan. No behavior changes.
- `ci.yml`, `security-devops.yml`, and `dependency-review.yml` read the change class. A documentation-only or website-only change skips `test-matrix`, the Microsoft Security DevOps scan, and the dependency review. A failed classification runs every one of those checks.
- The `ci` workflow runs on pushes to `main` only. A pull request commit starts one `ci` run instead of two.
- A newer pull request commit cancels the `ci` run of the older commit. Runs on `main` always complete.
- The `Save package attestation bundles` step runs under `set -euo pipefail` and stops when the release for the tag is already published.
- The `release` job in `build-packages.yml` gains `id-token: write`, `attestations: write`, and `artifact-metadata: write` permissions.
- `tests/test_release_workflow.py` permits `actions/attest@` in the release job and checks the expanded permission set.
- `tests/test_release_review_fixes.py` tests check-assets with both packages and Sigstore bundles.

## [0.52.1] (2026-11-10)

### Added

- `docs/decision-register.md` records the project owner's decisions by number, D93 to D371, with the open questions. Task issues cite these numbers.

- `WindowsDriveInspector` in `services/usb/windows.py` performing read-only Windows drive inspection with fixed PowerShell Storage command projections for `Get-Disk`, `Get-Partition`, and `Get-Volume`, executed as fixed argument arrays with `shell=False` and a timeout, and with no drive data in command text (USB-3, #155).
- The Windows inspector reuses the shared `_shared.py` sanitization helpers established by #195.
- Unit tests in `tests/unit/test_usb_windows_inspection.py` covering recorded outputs for a FAT32 stick, an exFAT stick, and a GPT system disk, plus malformed command replies, fixed command arrays, and untrusted metadata sanitization.

### Changed

- Pin the Windows inspector PowerShell interpreter to the SystemRoot-derived System32 path so a hostile PATH entry cannot substitute it and a relocated system root still resolves.
- Drop the `-ExecutionPolicy` flag from the fixed Windows command. The `-Command` input is not gated by the execution policy. The flag provided no protection.
- Bound Windows inspector command output at 8 MiB in bytes before decoding or parsing and cap the final error message length.

## [0.51.4] (2026-11-09)

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

- `bounded_command_runner` in `services/usb/macos.py` closes child pipes through the `_close_pipes` helper to respect the nesting invariant (#154).
- `_terminate_process` in `services/usb/macos.py` targets the process group with terminate and kill signals on POSIX and falls back to process scope when the group is gone. Spawned descendants receive the signals (#154).
- `_build_drive_identity` and `_build_partition` in `services/usb/macos.py` fall through size candidates only on a missing key. A valid zero no longer counts as missing (#154).
- The pipe reader timeout error names the generic command instead of `diskutil` (#154).
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

- A pushed `v*` tag runs build-packages as a release. The tag must match `__version__`, `pyproject.toml`, and the top CHANGELOG entry, and must point to a commit on main. A release job then uploads the six packages and `SHA256SUMS.txt` to a draft release. Only the repository owner publishes it.
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
  - CI downloads NSIS 3.13 and checks its pinned SHA-256 before use (Rule 9 sign-off). The Windows job installs the built setup in silent mode, runs the installed app's smoke check, checks the shortcut and the Apps and Features entry, and uninstalls in silent mode with the data kept.
- Every bundle now carries LICENSE and NOTICE beside the executable.
- A hidden `--forget-credentials` flag removes every key InfinitePrep saved in the OS keyring. The uninstaller's saved-keys box runs it.
- New tests: `test_windows_packaging.py` and `test_forget_credentials.py`.
- Portable mode for packaged builds (D83a1a).
  - A `portable.mode` file beside the executable keeps the database and settings in a `Data` folder there. Caches, recovery backups, and the downloaded fpcalc stay in the per-user folders. Credentials stay in the system keyring.
  - Only a packaged build looks for the file. A run from source always uses the per-user folders.
  - A portable copy starts with its own empty library. It does not read or copy the per-user one.
  - When the app cannot write to `Data`, a prompt offers `Use my user profile` or `Quit`, with a `Remember` checkbox. A remembered choice clears itself once the app can write to `Data` again.
  - The write check uses a uniquely named temporary file that deletes itself. It never touches a file already in `Data`.
- When the library folder or database cannot open at startup, a plain-text message states the cause. InfinitePrep then exits with code 1 instead of stopping with a traceback.
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
  - The tooltip shows the tag text literally. A value that looks like HTML, such as an `img` tag, does not render as markup or load anything.
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
  - An item with a savepoint that cannot roll back raises `BatchIntegrityError`, and the whole batch rolls back. No partial item reaches the commit.
  - A failed batch rollback logs a warning and keeps the original error. SQLite discards the uncommitted batch when the connection closes.
  - A batch refuses to start on a connection with an uncommitted transaction. It never absorbs or discards other work.
  - Each savepoint scope gets its own generated name. No scope can collide with another.
- `ImportService.import_paths` takes optional `progress` and `should_cancel` callbacks. `ImportBatchReport` adds `failed_files`, `scanned_count`, and `outcome`.
- Added `tests/unit/test_import_batch_transaction.py` and `tests/unit/test_ui_folder_import.py`.

### Changed

- The package version moves to 0.34.0.
- Without callbacks, `ImportService.import_paths` also imports in one transaction. A file that raises any error now appears in `failed_files` and no longer stops the call.
- Item 4 of the decision record no longer claims that no runtime dependency carries a copyleft license. PySide6, Qt, and libsndfile carry LGPL licenses.
- A new item 5 in the decision record records that user installs come only from the packaged bundles and not from a pip install.

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
  The base branch checker requires these exact pins for privileged execution.
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
  - FLAC writes keep every other Vorbis field and block. LABEL still also writes ORGANIZATION. A file without a Vorbis comment gets one with a vendor string of `InfinitePrep` and the package version.
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

- Spectral waveform analysis streams bounded audio into low, mid, high, and overall peaks.
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
- Spectral waveform arrays reject inconsistent dimensions or lengths.
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
- ID3v2.3 output keeps a TDRC or TDOR frame with a value that does not start with a year. A timestamp must match the whole v2.4 form, with an optional Z or offset after a time, or the frame stays as it is. TDAT needs a real calendar date, leap years included, and TIME needs a valid time of day. TIME keeps the written hour and minutes.

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
- Write preflight refuses a symbolic link, a file with more than one hard link, or a file owned by another user. Swapping in a written copy would replace the link, split the hard links, or change the file owner. The apply step checks again before the backup.

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
- Synchronized policy from `abuzucom/agents` 2.14.0. Branch preflight now runs `check_branch_name.py --strict-agent-preflight`. A branch that a harness assigns receives no exemption. `docs/agent-policy/clients.md` discloses that a multi-repository Claude Code cloud session loads no repository hooks.

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

- The playlist export leaves out a track with a stored or resolved path that holds a line break, another C0 control character, or DEL. One entry can no longer split into extra playlist lines. The export dialog names each skipped track with its control characters shown as spaces. A stale file ID shows as an unknown track. Added `tests/unit/test_playlist_export_review_fixes.py`.
- The export button appends `.m3u8` to a name with another suffix instead of replacing that suffix. A chosen `set.v2` becomes `set.v2.m3u8`, and `set.M3U8` keeps its name.
- The playlist export opens its destination with `O_NOFOLLOW` where the OS defines it. A symlink at the destination raises an error and leaves the link target unchanged. A new playlist file gets mode `0o600`, readable and writable by its owner only. An overwritten playlist gets the same mode on POSIX. Added `tests/unit/test_playlist_overwrite_mode.py`.
- The playlist export dialogs render as plain text through `show_plain_message`. A destination or file name that holds HTML tags shows literally. `DialogRecorder` records the helper. Added `tests/unit/test_plain_text_dialogs.py`.
- `FpcalcBootstrapService` pins the SHA-256 digest of the official Chromaprint 1.5.1 Windows and macOS archives. Extraction requires a 64-character hexadecimal digest and compares it in constant time.
- `install_fpcalc` refuses Linux and other platforms without a pinned archive. Those platforms install fpcalc from the system package manager.
- `install_fpcalc` always verifies the download against the pinned digest. A caller `expected_sha256` that differs from the pin raises an error ahead of any download.
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
- Added exact copies of the foucault review adapter files.
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
  - Consent-guarded, SHA-256 verified `FpcalcBootstrapService` and `ChromaprintAcoustIDAdapter`.
  - `MusicBrainzAdapter` (`ws/2`) with `1 req/sec` rate limiting and SQLite response caching.
  - Durable `JobOrchestrator` coordinating fingerprinting, catalog enrichment, and local audio analysis.
- **Milestone 4 (DJ Audio Analysis & Rip Diagnostics)**:
  - Built-in BSD-compatible `LocalDspAnalyzer` computing BPM, Krumhansl-Kessler chroma musical key, waveform overview envelope, digital clipping warnings, DC offset warnings, and silence warnings.
  - Optional external `EssentiaCliAnalyzer` subprocess adapter (`shell=False`).
- **Milestone 5 (Reconciliation, Discogs, Optional LLM & Artwork)**:
  - `DiscogsAdapter` with 6-hour TTL cache and vinyl position (`A1`, `B2`) extraction, plus `ACRCloudOptInAdapter` and `BeatportStubAdapter`.
  - `ClosedWorldValidator`, `OllamaOpenAIReasoningAdapter` (`qwen2.5:3b` default), and consent-guarded `OllamaBootstrapService`.
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
