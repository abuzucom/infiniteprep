# Decision register

This file records the owner's project decisions by number. Each entry is a
record. An entry grants no authorization. Rule sign-offs still happen at the
act, as `AGENTS.md` requires.

Only the planning session assigns new numbers. Other agents record a choice as
text in the task issue. The planning session then adds the entry here.

Entries marked "superseded" stay for reference. The later entry applies.

## Earlier decisions still referenced

Entries before D93 predate this register. Only the ones still cited appear.

| ID | Decision |
| --- | --- |
| D80 | Every PR carries a How to test section. |
| D82 | Signed installers wait until the signing certificates exist. |
| D85 | Per-format limits apply for the XDJ-RX3, CDJ-3000, Serato DJ Pro, and djay Pro. D333 sets the source rule. |
| D86 | With no target device, the safe format is FLAC up to 48 kHz and 24-bit. |
| D88 | Earlier stem separation notes are background only. D217 applies. |

## D93 to D206: USB and DJ export (2026-11-06)

The 2026-11-06 planning handoff numbered these 1 to 114. Decision N is
D(N+92).

| ID | Decision |
| --- | --- |
| D93 | Show all available drive identifiers, including volume serial. |
| D94 | Block export for unknown filesystem or partition layouts. |
| D95 | Store update backups on the computer. |
| D96 | Block export when estimated space is insufficient. |
| D97 | Make extra free-space headroom configurable. |
| D98 | Offer fixed-byte and percentage reserves. |
| D99 | Set the initial reserve to zero. |
| D100 | Show omissions in an in-app per-track report. |
| D101 | Use exported, exported with omissions, skipped, and failed statuses. |
| D102 | Include omitted source values in reports. |
| D103 | Include automatic phrase analysis. |
| D104 | Analyze phrase boundaries, labels, and fill-in markers. |
| D105 | Offer review and automatic-save modes. Default to review. |
| D106 | Support phrase edits and previewed bulk decisions. |
| D107 | Reuse the cue-edit lifecycle for phrase edits. |
| D108 | Preserve imported Hot Cue Auto Load and allow per-track edits. |
| D109 | Save Hot Cue Auto Load edits for later exports. |
| D110 | Allow assignment of verified rekordbox memory-cue colors. |
| D111 | Support per-cue and bulk color assignment. |
| D112 | Save memory-cue color edits for later exports. |
| D113 | Preserve and edit imported My Tags. |
| D114 | Save My Tags vocabulary and track assignments. |
| D115 | Map My Tags to verified equivalent destination fields. |
| D116 | Use a separate completion manifest and verified database fields. |
| D117 | Record in-progress and completed manifest states. |
| D118 | Use UTC timestamps. |
| D119 | Cover My Tags vocabulary and assignments in the digest. |
| D120 | Use SHA-256 over canonicalized tags and assignments. |
| D121 | Record analysis versions for generated exported data. |
| D122 | Require parser and target application or device checks where available. |
| D123 | Allow release with hardware validation marked pending. |
| D124 | Use one reviewed model with per-destination adapters. |
| D125 | Compare a dedicated helper with standard-library XML generation. |
| D126 | Use pinned independent parser readers in CI. |
| D127 | Mark a target unsupported if its application rejects a parser-accepted export. |
| D128 | Offer single-target and multi-target USB exports. |
| D129 | Show one overall track status with target-specific details. |
| D130 | Confirm after showing target identity, profiles, changes, and space estimate. |
| D131 | Refuse system and boot volumes. |
| D132 | Recheck drive identity and safety immediately before writing. Abort on change. |
| D133 | Superseded by D201. |
| D134 | Ask the user to resolve ambiguous track matches. |
| D135 | Match by verified stable ID, then exact normalized path. |
| D136 | Allow broad XDJ and CDJ family profiles. |
| D137 | Stop on read-only media without changing mount state or permissions. |
| D138 | Refuse filesystem errors and show repair guidance. |
| D139 | Remove the in-progress USB manifest during recovery. Keep the journal. |
| D140 | Retain only the latest completed manifest. |
| D141 | Use JSON for the manifest. |
| D142 | Store the manifest in an InfinitePrep metadata folder. |
| D143 | Let the user select the full library, one playlist, or multiple playlists. |
| D144 | Record scope, playlist names, and track count. |
| D145 | Record detailed result counts per target. |
| D146 | Include per-track omissions in the manifest. |
| D147 | Include omitted source values in the manifest. |
| D148 | Identify tracks by relative path, title, and artist. |
| D149 | Superseded by D200. |
| D150 | Run missing phrase analysis during export preflight. |
| D151 | Run phrase analysis and wait before export. |
| D152 | Keep low-confidence phrases tentative and omit them until reviewed. |
| D153 | Allow an optional disclosed network phrase-analysis service. |
| D154 | Decide network payload after provider and privacy review. |
| D155 | Use persistent network opt-in after disclosure. |
| D156 | Offer confirmed local analysis if the network provider fails. |
| D157 | Use local phrase analysis by default. Enable network analysis only after opt-in. |
| D158 | Implement the preflight and safety stage before device writers. |
| D159 | Set device-adapter PR boundaries after format research. |
| D160 | Support Windows, macOS, and Linux release platforms. |
| D161 | Use fixed operating-system tools for drive inspection. |
| D162 | Let each adapter manage format-defined device paths. |
| D163 | Ask Create New or Update on each export. |
| D164 | Replace a conflicting database only after backup, preview, and confirmation. |
| D165 | Delete computer backups after verified success. |
| D166 | Prompt on reconnect with Resume, Restore, or Leave unchanged. |
| D167 | Map rekordbox-specific data to verified equivalents across formats. |
| D168 | Omit approximate mappings until the user approves them. |
| D169 | Save approved mappings for future exports. |
| D170 | Key saved mappings by exporter version and destination profile. |
| D171 | Preserve and edit imported My Tags categories. |
| D172 | Use On, Off, or Inherit for Hot Cue Auto Load. |
| D173 | Default Hot Cue Auto Load to Inherit. |
| D174 | Allow custom phrase labels. |
| D175 | Allow analyzer-proposed custom labels for review. |
| D176 | After analysis, offer review, omission, or cancellation. |
| D177 | Require benchmark and confidence reporting. Review release without a fixed threshold. |
| D178 | Show boundary and label evidence in phrase review. |
| D179 | Record reader names, versions, and results in the manifest. |
| D180 | Use a conservative upper-bound space estimate. |
| D181 | Use PowerShell Storage commands on Windows. |
| D182 | Superseded by D203. |
| D183 | Use lsblk JSON and df on Linux. |
| D184 | Restrict the first USB release to FAT32. |
| D185 | Require MBR with exactly one partition. |
| D186 | Require that partition to be primary. |
| D187 | Refuse active or bootable partitions. |
| D188 | Accept removable-class drives only. D372 refines the class rule. |
| D189 | Refuse targets with unknown database-format support. |
| D190 | Use only family-common verified capabilities in broad profiles. |
| D191 | Match recovery by verified volume ID. Confirm if needed. |
| D192 | Keep a computer journal and a USB in-progress marker. |
| D193 | Offer manual cleanup and configurable expiry for unresolved recovery data. |
| D194 | Enable expiry by default. |
| D195 | Set the default expiry to 30 days. |
| D196 | Warn before expiry, then delete automatically. |
| D197 | Warn one day before expiry. |
| D198 | If the app was closed at expiry, delete at next launch after notice. |
| D199 | When both reserve settings exist, use the larger reserve. |
| D200 | Keep the completed manifest and add a temporary in-progress record. |
| D201 | Apply reviewed InfinitePrep fields on Update after preview. |
| D202 | Block the entire export if any selected file exceeds the FAT32 4 GiB file limit. |
| D203 | Use fixed read-only diskutil commands with plist parsing on macOS. |
| D204 | Compare helper and standard-library rekordbox XML approaches with a focused prototype. |
| D205 | Cover all approved fields and omission cases in the comparison fixture. |
| D206 | Select the Traktor NML CI reader route. Open. D343 sets research first. |

## D207 to D246: scope, licensing, and D78b (2026-11-06, 2026-11-07)

| ID | Decision |
| --- | --- |
| D207 | Breaking changes are allowed until 1.0.0. The CHANGELOG names each one. Rule 6 in `AGENTS.md` stays as written. |
| D208 | No strong copyleft (GPL, AGPL) in anything shipped or downloaded. LGPL parts stay under compliance. Build-only tools stay. |
| D209 | The copyleft cleanup (C0) came before D78b and D83a. |
| D210 | No CI license gate. Every Rule 9 proposal states the license. The docs claim no enforcement. |
| D211 | qt-material 2.14 approved with Jinja2 3.1.6 and MarkupSafe 3.0.2. A later Dependabot update moved qt-material to 2.17. |
| D212 | D78b scope: playback on notation changes, the import worker, fields and Other tags, the playback prompt, and the FFmpeg logs. |
| D213 | Order: C0, then D78b, then D83a. |
| D214 | The owner closes Dependabot PRs 40 and 44. |
| D215 | Restored from backlog: neural key detection, fake-lossless detection, intensity and arousal, and energy summaries. Each needs permissive code and weights. |
| D216 | Kept in backlog: two-deck preview, metronome audition, and prompt cue templates. |
| D217 | Stem separation comes last. Model, engine, runtime, and backends get decided at stage start. |
| D218 | STEM.MP4 tag writes return to scope. The writer keeps the NI `stem` atom byte for byte and shifts offsets in every track. |
| D219 | Restored: next-track suggestions, cue names and colors, automatic energy proposals, and the lossy-to-lossless warning. iTunes XML import and offline identification stay removed. |
| D220 | The 2026-11-06 handoff decisions 1 to 114 become D93 to D206. |
| D221 | One shared User-Agent: the version plus the repository URL. No email address. |
| D222 | Each restored item joins the stage it extends. |
| D223 | Remove the Essentia adapter. |
| D224 | Check fpcalc licensing from both the upstream build scripts and the configuration text in the pinned binaries. |
| D225 | Scan every pinned wheel on all three OSes. Licenses come from the wheels' own files. |
| D226 | C0 split: Essentia removal first, then the research record. |
| D227 | Research that needs downloads runs in a CI workflow on a branch that never merges. |
| D228 | No pip install reaches a user machine. The installer and updater use the verified release bundle. |
| D229 | D83b provides a Windows online installer and an in-app updater. No CPython runtime, pip, deb, or rpm packages. |
| D230 | The application wheel leaves the release assets. |
| D231 | The bundle check uses a per-OS allowlist plus a denylist of Qt tools and GPL-3-only Qt modules. |
| D232 | CI publishes a build-provenance attestation for each package. The installer and updater verify it before unpacking. |
| D233 | First launch asks whether to check GitHub for updates. The answer persists. Every download still needs approval. |
| D234 | The online installer is Windows only and installs the NSIS layout. |
| D235 | Updates download and verify into staging. A helper swaps files, keeps a rollback copy, never touches user data, and relaunches. A failed swap restores the rollback. |
| D236 | D83b1 is the online installer. D83b2 is the updater. |
| D237 | D78b1 is the import worker. D78b2 holds the small fixes. |
| D238 | Import scans first, then uses one transaction with a savepoint per file. Cancel or a crash rolls back the batch. |
| D239 | Superseded by D379. |
| D240 | Import ends with a report of counts and expandable per-file warnings, with a Copy button. |
| D241 | Superseded by D386. |
| D242 | Other tags show in a read-only key and value table. Binary frames show size and SHA-256. |
| D243 | No playback prompt. Import runs while playback continues. |
| D244 | A hung file read gets a 2-minute limit with Quit Now or Keep Waiting. |
| D245 | Other tags hides keys with a leading underscore. |
| D246 | The detail panel reloads audio only when the file ID changes. |

## D247 to D324: release packages (2026-11-07, 2026-11-08)

| ID | Decision |
| --- | --- |
| D247 | D83a splits per platform: Windows, Linux, macOS, then the release job. |
| D248 | Builds run by manual trigger until the release job adds the `v*` tag trigger. |
| D249 | The build workflow merged first. |
| D250 | Build on all three OSes. |
| D251 | Pinned images `windows-2025`, `macos-14`, and `ubuntu-22.04`. D276 replaced `macos-14`. |
| D252 | The Linux baseline is glibc 2.35. RHEL 9 and Rocky 9 are out of scope. |
| D253 | Bundle Python 3.12. |
| D254 | `actions/upload-artifact` approved, pinned by full SHA. |
| D255 | Build artifacts keep for 3 days. |
| D256 | A hidden `--smoke-check <path>` flag writes the version to a new file and exits 0. |
| D257 | Storage shipped before packaging. |
| D258 | An unwritable `Data` folder gets a dialog with a Remember choice. |
| D259 | A remembered entry clears once `Data` is writable. |
| D260 | fpcalc stays per-user in every storage mode. |
| D261 | The `portable.mode` marker counts only in frozen builds, beside the executable. |
| D262 | A portable copy starts with an empty library. |
| D263 | Storage locations are set once at startup. |
| D264 | Windows installs to `%LocalAppData%\Programs\InfinitePrep`. |
| D265 | An unchecked uninstall box deletes the app data. Music and exports are never touched. |
| D266 | A separate unchecked box deletes the saved API keys. |
| D267 | NSIS 3.13 approved, build only, pinned by SHA-256. |
| D268 | CI runs the installer lifecycle end to end. |
| D269 | An existing install leads to an Uninstall and continue or Cancel prompt. |
| D270 | Package file names carry the version and platform. Packages carry LICENSE and NOTICE. |
| D271 | Rule 3 sign-off for the upload test edit. |
| D272 | Beat This! small on ONNX is the primary beat tracker. The classical tracker gives a second opinion. |
| D273 | Per-beat markers and adaptive grids are planned work in the grid stage. |
| D274 | The beat-grid stack and its packaged dependencies must be BSD-3-compatible with no LGPL. Unclear licenses are rejected. |
| D275 | A set-vector license check is the first step of the grid stage. Without a clear compatible license, grid fitting is written fresh. |
| D276 | The build matrix pins `macos-15`. |
| D277 | Superseded by D284. |
| D278 | Linux work splits into storage, then packaging. |
| D279 | The tar.gz uses the `portable.mode` marker. |
| D280 | The AppImage keeps its data beside the .AppImage file, with the D258 prompt. |
| D281 | The Linux data folder holds the database and settings only. |
| D282 | The folder beside the .AppImage is `InfinitePrep-Data`. |
| D283 | Pin linuxdeploy and its AppImage plugin, with digests computed in CI first. |
| D284 | Drop the linuxdeploy Qt plugin. |
| D285 | Pin AppImage type2-runtime 20251108. |
| D286 | linuxdeploy, its AppImage plugin, and the runtime approved after a license check. NOTICE lists the runtime's static libfuse. |
| D287 | Use a placeholder icon. |
| D288 | Ship two tarballs, plain and portable. |
| D289 | macOS uses per-user storage. A `portable.mode` file next to the app switches to a folder beside it. |
| D290 | macOS work splits into storage, the Settings cleanup action, and the app with its DMG. |
| D291 | The folder beside the .app is `InfinitePrep-Data`. |
| D292 | A translocated copy uses the per-user folders with no prompt. |
| D293 | The Settings cleanup action works on every OS. |
| D294 | The cleanup action offers two unchecked boxes. |
| D295 | The cleanup action deletes, then quits. |
| D296 | The cleanup action asks a second confirm, with No as the default. |
| D297 | The macOS bundle ID is `dev.infiniteprep.app`. |
| D298 | The DMG holds the app, an Applications link, and the notices. Ad-hoc signing only. |
| D299 | The release job splits into bundle checks, the tag release, and attestations. |
| D300 | The bundle check uses a pattern allowlist per OS plus a fixed denylist. |
| D301 | The bundle checks run in build-packages only. |
| D302 | Remove Qt Virtual Keyboard and GNU Readline from the bundles. |
| D303 | Trim unused Linux Qt plugins. NOTICE lists the runner libraries that remain. |
| D304 | Prune orphaned Linux libraries. The Linux job starts the app on X11 under Xvfb. |
| D305 | cyclonedx-py builds the Python SBOM entries. An in-house script adds native and system parts from the NOTICE table. |
| D306 | The SBOM work follows the attestation work. |
| D307 | One combined CycloneDX file tags each component with its platforms. |
| D308 | The SBOM ships as a release asset in SHA256SUMS, with an SBOM attestation against every package. |
| D309 | NOTICE corrections for Qt, ICU, D-Bus, FreeType, libkeyutils, zstd, and bzip2. |
| D310 | CI fetches the ICU 73.2 license at a pinned SHA-256 and ships it as `ICU-LICENSE.txt`. |
| D311 | NOTICE carries a three-year written offer for LGPL-2.1 sources and pinned source links for Qt and PySide6. |
| D312 | Trim proven-orphan files before the bundle check. |
| D313 | Drop the proven orphans. Windows also drops QML and Quick. The smoke check fails without a multimedia backend. |
| D314 | CI creates a draft release. The owner publishes it. |
| D315 | Only the release job holds `contents: write`. |
| D316 | A rerun reuses the draft. A matching SHA-256 skips an asset. A mismatch stops the job. |
| D317 | A `v*` tag must equal the three version files and point to a commit on main. |
| D318 | Release notes hold the CHANGELOG section, a download block, then the generated PR list. |
| D319 | The release job checks the six exact package names. |
| D320 | Each draft asset downloads into a fresh `mktemp -d` folder. |
| D321 | `file_sha256` uses `hashlib.file_digest`. |
| D322 | The `v0.48.0` tags in the README and `release.py` stay as generic examples. |
| D323 | No retry loops around `gh` calls. A rerun of the release job is the retry. |
| D324 | The `notes` command does not create its output folder. |

## D325 to D348: planning calls (2026-11-08)

A dependency choice here does not replace Rule 9 confirmation at the act.

| ID | Decision |
| --- | --- |
| D325 | `actions/attest` v4.2.2 (MIT) at `1e69f48acb82d1966a394da916b4c1698aa569d6` for package and SBOM attestations. |
| D326 | `cyclonedx-bom` 8.0.1 (Apache-2.0), build only, in its own hash-pinned environment, with chardet on 7.x. |
| D327 | `sigstore` 4.5.1 (Apache-2.0) and its dependency tree ship in every bundle for update verification. |
| D328 | Attestation bundles ship as `<package>.sigstore.json` release assets. |
| D329 | The Sigstore trust root refreshes through TUF from `tuf-repo-cdn.sigstore.dev`. The update consent names that host. |
| D330 | Updates swap through the staged app in a hidden `--apply-update` helper mode. |
| D331 | The Windows online installer is a verified one-file bootstrap that runs the verified NSIS installer. |
| D332 | Fake-lossless detection ships first as spectral cutoff plus encoder traces. A trained model follows as a user-selectable second method. |
| D333 | Device capabilities count as verified only from vendor documentation, cited by URL and access date. |
| D334 | MP3 output uses the bundled LAME through soundfile, with 320 CBR and V0 VBR presets. |
| D335 | The app log lives in the per-user cache folder, 5 files of 1 MB, with an Open log folder button and a debug switch. |
| D336 | An agent does the beat-grid stack license check from the license files inside each download. |
| D337 | `python-stretch` 0.3.1 (MIT, Signalsmith Stretch) for pitch shift. |
| D338 | One Energy field per track, 1 to 10. A DSP score proposes. The DJ accepts, edits, or types a value. Only accepted or typed values reach tags. |
| D339 | Next-track suggestions filter by key and tempo, then rank by a DJ-picked energy direction. |
| D340 | One cue color model, the verified rekordbox palette, for hot and memory cues. Other exporters map to their nearest color and report approximations. |
| D341 | Research comes before the phrase analyzer choice. |
| D342 | The phrase research also surveys network phrase services. |
| D343 | For D206, research comes first: `abuzucom/traktor-bridge` and other permissive NML parsers. |
| D344 | The rekordbox XML exporter runs its prototype comparison and reader survey inside its own issue. |
| D345 | One shared research issue covers Serato import and export. |
| D346 | The USB database research is ready for an agent. |
| D347 | A separate research issue surveys beat-grid benchmark datasets. |
| D348 | One shared model survey covers neural keys, the fake-lossless model, and intensity and arousal. |

## D349 to D359: plan improvements (2026-11-08)

| ID | Decision |
| --- | --- |
| D349 | Numbered schema migrations on `user_version`, a backup before each, refusal of newer libraries, and backup restore on update rollback. |
| D350 | The owner merges one PR at a time. An agent sets the version bump when bringing its branch up to date, and re-bumps after an earlier merge. |
| D351 | Shared groundwork: Settings tabs, one shared edit lifecycle, and reuse of the existing proposal and review path. |
| D352 | A task index issue lists every task by status. D362 adds this register. |
| D353 | Research issues report in an issue comment and open no PR. |
| D354 | Each build reports bundle sizes and fails past a per-OS limit, set at the measured size plus 50 percent. |
| D355 | One Rule 3 rewrite makes the release tests read constants, with no weakened assertion. |
| D356 | The README roadmap points to the stage issues. |
| D357 | No weekly question batch and no cap on active issues. |
| D358 | Hardware checks stay pending under D123, with no protocol issue. |
| D359 | Shared audio fixtures. A threat model comes first for the update swap and device writes. |

## D360 to D371: repository check (2026-11-10)

D364 to D371 restate the policies in the owner's USB-6 research brief on
issue 158. That brief numbers them D1 to D12. The register numbers apply.

| ID | Decision |
| --- | --- |
| D360 | PR 193 stays one PR. It covers WAVE-1, WAVE-2, and WAVE-3. |
| D361 | OneLibrary (`exportLibrary.db`) is in scope beside legacy DeviceSQL (`export.pdb`) before the first USB release. |
| D362 | This register lives in the repository. The planning session keeps it current. |
| D363 | Priority labels are the owner's own notes. They do not change the stage order. |
| D364 | Brief D1: both USB database formats need independent software validation before the first release. Hardware validation may stay pending. |
| D365 | Brief D3: verified device profiles select the database output. No device is assumed to support both formats. |
| D366 | Brief D4: the device scope stays the XDJ-RX3 and CDJ-3000 families. |
| D367 | Brief D6: structural checks and independent readers are required. Reader acceptance does not establish hardware compatibility. |
| D368 | Brief D7: unsupported metadata is omitted and each omission is reported. D168 still governs approximate mappings. |
| D369 | Brief D9: the research compares write transaction strategies and keeps the journaling, recovery, and device safety requirements. |
| D370 | Brief D11: the research delivers separate DeviceSQL and OneLibrary reports in one handoff. |
| D371 | Brief D12: the device writer split comes from research evidence. The owner approves it before any implementation issue exists. |

## D372 to D387: drive class rule and batch queue (2026-11-11, 2026-11-12)

D372 comes from issue 207. D373 to D387 come from the owner decisions in the
comment on issue 109, in the same order. The last two answers in that comment
name the PR count and the branch of PR 202. They carry no register number.

| ID | Decision |
| --- | --- |
| D372 | A drive passes the class rule on a USB, SD, or MMC bus alone. A drive without the removable flag shows a "reports as a fixed disk" note in the confirm dialog and the drive picker. This refines D188. |
| D373 | The Scope paragraph of issue 109 is the batch queue design source. |
| D374 | The queue governs Identify & Analyze, folder import, and Apply Safe Writes. Transcode to XDJ stays outside the queue. |
| D375 | The queue starts one worker thread per batch. Import keeps `ImportWorker`. Analyze and writes each gain a worker. |
| D376 | The queue lives in memory only. Waiting batches vanish at exit. No schema change. |
| D377 | The queue view is a dock panel at the bottom of the main window. |
| D378 | Move Up and Move Down buttons plus drag and drop reorder waiting batches. The running batch never moves. |
| D379 | The dock shows progress for every batch type. The modal import progress dialog goes away. This replaces D239. The D244 stuck-file prompt stays. |
| D380 | A finished batch stays in the dock with counts and a status. View Result opens its report. Import reuses the D240 report dialog. No report opens automatically. Clear Finished empties the list. |
| D381 | Cancel removes a waiting batch. Cancel on a running import keeps D238. Cancel on a running Analyze or write batch finishes the current file, stops, and keeps finished files. |
| D382 | Settings & Providers gains "Stop a batch at its first failed file", off by default. |
| D383 | A new `WriteService` method continues past a failed write. `apply_batch` keeps its contract. |
| D384 | The queued Analyze path treats any exception type as a per-file failure. `process_files` keeps its contract. |
| D385 | A queued write batch applies the previewed plans. The per-file preflight still runs. |
| D386 | Close during a running or waiting batch offers Keep Working, or Cancel and Exit. This replaces D241. |
| D387 | While any batch runs or waits, transcode, journal restore, data deletion, review edits, and Bulk Approve stay disabled. Browsing, playback, filtering, playlist export, and queue controls stay available. |
## Open questions

| Source | Question |
| --- | --- |
| D206 | The Traktor NML CI reader route. Research first (D343). |
| Brief D2 | The USB database writer strategy: standalone, reused permissive code, or a pinned dependency. |
| Brief D5 | The mandatory feature coverage per format and device profile. |
| Brief D8 | Create and Update release sequencing. |
| Brief D10 | The USB test fixture policy. |