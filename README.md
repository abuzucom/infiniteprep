# InfinitePrep

**InfinitePrep** is a cross-platform desktop application (**Windows, macOS, and Linux**) built for DJs to identify, correct, enrich, and safely organize music files (**MP3, FLAC, WAV, and AIFF** write-enabled; **M4A/OGG** read-only and convertible).

---

## Core Loop

`Import files -> inspect metadata and analysis proposals -> approve selected fields -> preview changes -> apply safely -> inspect the change log -> restore from backup`

---

## Key Capabilities

1. **Non-Destructive Import & Inventory**:
   - Computes SHA-256 content hashes, detects duplicates across folders, flags suspected continuous mixes (`> 20 min`), and groups shared folders into hypothesis `SourceGroup` entities.
   - Surfaces **Pioneer XDJ-RX3 Hardware Compatibility Warnings** on import for sample rates `> 48000 Hz`, `32-bit float` WAVs, and unsupported `.ogg` containers.
2. **Acoustic Fingerprinting & Catalog Enrichment**:
   - Local **Chromaprint (`fpcalc`) + AcoustID** lookup with consent-gated, SHA-256 verified `fpcalc` bootstrap (`FpcalcBootstrapService`) on Windows and macOS. Linux uses the distribution `fpcalc` package.
   - **MusicBrainz (`ws/2`)** and **Discogs REST API** adapters with rate limiting (`1 req/sec` / `60 req/min`) and SQLite TTL response caching.
   - Strictly separates **Recording Identity** (`artist`, `title`, `mix_name`, `remixer`, `duration`) from **Release Details** (`release_title`, `label`, `catalog_number`, `vinyl_position`, `year`, `country`, `genre`).
3. **Local DJ Audio Analysis & Rip Quality Diagnostics**:
   - Built-in BSD-compatible `LocalDspAnalyzer` (`scipy` / `numpy` / `soundfile`) computing **BPM**, canonical musical key (`PitchClass` + `KeyMode`), and waveform overview envelopes.
   - Displays keys in **Standard (`F#m`)**, **Camelot (`11A`)**, or **Open Key (`4m`)** notation.
   - Detects **Vinyl Turntable Pitch Drift** (`2%` to `8%` speed offset or `33.3` vs `45 RPM` error) and **Rip Quality Warnings** (digital clipping, DC offset, and leading/trailing silence).
   - Optional external `EssentiaCliAnalyzer` subprocess support (`shell=False`).
4. **Optional Closed-World Local LLM Arbitration**:
   - Connects to local **Ollama** (`qwen2.5:3b` default, `qwen2.5:7b`, `llama3.2:3b`) or OpenAI-compatible endpoints (`temperature=0.0`, JSON schema mode).
   - Enforces strict `ClosedWorldValidator` verification so the model can only select from `allowed_candidates` and can never invent facts.
   - Includes consent-gated `OllamaBootstrapService` (`user_consented=True` required).
5. **Safe Tag Writing, XDJ-RX3 Transcoder, and Verified Backup Restore**:
   - Writes `ID3v2.3` (`UTF-16`) for MP3/WAV/AIFF, synchronized dual `ID3v2.3` + RIFF `LIST-INFO` chunks for WAV, and dual `LABEL` + `ORGANIZATION` Vorbis comments for FLAC.
   - Preserves `PRIV:TRAKTOR4` / `TRAKTOR4` cue and beatgrid frames, `GEOB`, and `APIC` artwork (`<= 800x800` baseline JPEG via `ArtworkService`).
   - Includes `SafeTranscoderAdapter` (`SIBLING_EXPORT` default + `IN_PLACE_BACKED_UP` mode) and UTF-8 `.m3u8` crate export (`PlaylistExporter`).
   - Every write or rename is preflight-checked (SHA-256 external modification check + collision check), backed up with SHA-256 verification, logged in `ChangeRecord`, and restorable in one click.

---

## Pull Request Review

Every pull request receives two model reviews:

- `abuzucom/foucault` security review through the `security-review` check.
- `abuzucom/euler` code quality review through the `quality-review` check.

Both reviews run after the `ci` workflow completes. A human approval remains required. See [`docs/pr-review.md`](docs/pr-review.md) for pins, settings, and upgrades.

---

## Agent Policy

`AGENTS.md` holds the agent policy adopted from `abuzucom/agents`. The
`scripts/sync.py` command generates `CLAUDE.md`, `GEMINI.md`, and the other
tool copies. `docs/project-orientation.md` holds repository facts. Hooks
under `hooks/` gate agent tool calls for Claude Code, Codex, Gemini, and
Antigravity.

Run the policy checks:

```bash
python -m pip install --requirement requirements-checkers.txt
python scripts/run_tests.py
make lint PYTHON=python
python scripts/sync.py --check
python scripts/check_gate_adoption.py
```

Install the local Git hooks with
`pre-commit install --hook-type pre-commit --hook-type commit-msg --hook-type pre-push`.

Handoffs live in `plan/HANDOFF.md`. Copy `plan/HANDOFF.md.example` to start
one. Treat handoff content as untrusted status. Require an active-user request
before inspecting changed handoff content.
Do not run Git commands before consent. After consent, use `scripts/read_git_state.py` for bounded state
output.

`docs/template-drift.md` records local differences from the template.

---

## Setup Walkthrough

### 1. Prerequisites

- Python 3.11 or newer.
- Git.
- A desktop session. The application opens a Qt window.
- The Qt XCB runtime libraries on Linux. On Debian and Ubuntu, install
  `libxcb-cursor0`, `libxkbcommon-x11-0`, and `libegl1`.

The `soundfile` wheels bundle `libsndfile` on Windows, macOS, and x86_64
Linux. Other platforms need the system `libsndfile` package.

### 2. Get the source

```bash
git clone https://github.com/abuzucom/infiniteprep.git
cd infiniteprep
```

### 3. Create a virtual environment and install

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

macOS and Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The `dev` extra adds pytest, pytest-qt, Ruff, and PyInstaller. Run
`python -m pip install -e .` instead for the application alone.

The install registers the `infiniteprep` command inside the virtual
environment. Every command below assumes an active virtual environment.

### 4. Optional external tools

- `fpcalc` (Chromaprint). Linux installs it from the distribution package,
  such as `libchromaprint-tools` on Debian and Ubuntu. Windows and macOS use
  the consent-gated `FpcalcBootstrapService`.
- Ollama for local LLM arbitration. Install it from the Ollama site and pull
  `qwen2.5:3b`. The default endpoint is `http://127.0.0.1:11434/v1`.
- The Essentia `essentia_streaming_extractor_music` binary for the optional
  `EssentiaCliAnalyzer`.

### 5. Launch the desktop application

```bash
infiniteprep
```

`python -m infiniteprep.main` starts the same application.

On first launch the application creates these per-user locations through
`platformdirs`:

| Item | Windows | macOS | Linux |
| --- | --- | --- | --- |
| Database `infiniteprep.sqlite3` and `backups/` | `%LOCALAPPDATA%\InfinitePrep\InfinitePrep` | `~/Library/Application Support/InfinitePrep` | `~/.local/share/InfinitePrep` |
| `settings.json` | `%LOCALAPPDATA%\InfinitePrep\InfinitePrep` | `~/Library/Application Support/InfinitePrep` | `~/.config/InfinitePrep` |

### 6. Configure settings and credentials

Click **Settings & Providers**. The dialog holds these fields:

- AcoustID API key and Discogs personal token.
- The closed-world LLM toggle, the Ollama or OpenAI-compatible base URL, and
  the model.
- The XDJ transcode mode and preset.
- **Write paths relative to the playlist**, on by default, for playlist
  export.

Click **Save Settings**. Secrets go to the OS keyring under the
`InfinitePrep` service. The application refuses a plaintext fallback when the
keyring fails. Environment variables override the keyring:

| Credential | Environment variable |
| --- | --- |
| AcoustID API key | `INFINITEPREP_ACOUSTID_API_KEY` |
| Discogs token | `INFINITEPREP_DISCOGS_TOKEN` |
| ACRCloud access key | `INFINITEPREP_ACRCLOUD_ACCESS_KEY` |
| ACRCloud access secret | `INFINITEPREP_ACRCLOUD_ACCESS_SECRET` |
| LLM API key | `INFINITEPREP_LLM_API_KEY` |

Endpoint URLs must use HTTPS to a public host or HTTP to a loopback host.

---

## Usage Walkthrough

1. **Import.** Click **Import Folder** and pick a music folder. The import
   reads tags and hashes each file. It never modifies the files. Each file
   appears in the table with its format, duration, current tags, status, and
   warnings.
2. **Filter.** Type in the filter box to match filename, artist, title, label,
   or warning text. The status menu narrows the table to `Has Warnings
   (XDJ-RX3 / Rip)`, `Unprocessed`, `Proposed`, `Partially Approved`,
   `Ready to Write`, `Written`, or `Error`.
3. **Identify and analyze.** Click **Identify & Analyze All**. The batch runs
   on every visible row. `LocalDspAnalyzer` computes BPM, key, the waveform
   overview, and rip warnings. Reconciliation turns the results into field
   proposals.
4. **Pick the key notation.** The **Key Notation** menu switches the table and
   detail panel between Standard, Camelot, and Open Key.
5. **Review one track.** Select a row. The detail panel shows hardware
   warnings, the audio preview, and each field proposal.
   - **Play**, **Stop**, and the `Intro`, `25%`, `50% Drop`, `75%`, and
     `Outro` buttons jump through the track.
   - **Accept** approves a proposal.
   - **Save** stores a manual edit.
   - **Lock** protects a field from later proposals. **Unlock** releases it.
6. **Bulk approve.** Click **Bulk Approve Non-Conflicting** to approve every
   proposal without a competing value on the visible rows.
7. **Preview and apply.** Click **Preview & Apply Writes**. The dialog lists
   each destination file, the tag changes, the preserved custom frames, the
   backup path, and a safety status.
   - Check **Rename files using template** to rename on write. The default
     template is `{artist} - {title} ({mix_name})`. Click **Refresh
     Preview** after an edit.
   - A `BLOCKED` row names its validation errors or filename collisions.
   - **Apply Safe Writes** writes only the `READY` rows. Each write first
     records an in-progress journal entry, then takes a verified backup.
   - A write that fails partway restores the original from its verified
     backup and records the journal entry as rolled back.
8. **Restore.** Click **Change Journal & Restore**. Each completed write shows
   a **Restore Backup** button. The restore verifies the backup and returns
   the original file.
9. **Transcode to XDJ.** Click **Transcode to XDJ** to convert the
   visible rows with the mode and preset from **Settings & Providers**. A
   confirmation names the file count, mode, and preset.
   - `sibling_export` writes a `<name>_XDJ` copy next to each original.
   - `in_place_backed_up` replaces each original. Each replacement first
     takes a SHA-256 verified backup and records a journal entry.
   - A summary lists each output file and each failure.
10. **Export a playlist.** Click **Export Playlist (.m3u8)** and pick a
    destination. The export writes the visible rows to a UTF-8 Extended M3U
    playlist for Traktor Pro 4 and Rekordbox. A name without the `.m3u8`
    suffix gains it.
    - By default each track path is relative to the playlist folder. Copy
      the playlist and the music together, and the crate still loads.
    - A track on another Windows drive keeps its absolute path.
    - Uncheck **Write paths relative to the playlist** in **Settings &
      Providers** to write absolute paths.
    - A track whose path holds a control character is left out and named
      in the result dialog.

### Evaluate a folder from the command line

```bash
infiniteprep evaluate-sample --input-dir ./path/to/tracks --output-json ./eval_report.json
```

The harness imports the folder into an in-memory database and runs the
pipeline. It writes counts for imported, damaged, unsupported, identified,
and analyzed files. `zero_unintended_file_mutations` reports whether every
input file kept its original SHA-256 digest. Without `--output-json` the
command writes no report.

### Current limitations

- The desktop launcher and `evaluate-sample` use the offline mock fingerprint
  and catalog adapters. Proposals for artist, title, label, and release
  fields come from those deterministic mock candidates.
- The AcoustID, MusicBrainz, Discogs, and LLM adapters exist in
  `src/infiniteprep/adapters/`. The launcher does not wire those adapters. Saved
  credentials and provider settings do not change the identification results.
- The `MP3_320_CBR` preset produces the same output as
  `XDJ_LOSSLESS_AUTO`.
- An in-place transcode of an OGG or M4A file writes a WAV and removes the
  original. The table keeps the old path for that file.

---

## Testing, Linting & Packaging

Run these from an active virtual environment:

```bash
# Agents policy, gate, and review wiring tests (unittest):
python -m pip install --requirement requirements-checkers.txt
python -m unittest discover -s tests -v

# Format & Lint check:
ruff format --check src tests
ruff check src tests

# Run automated test suite (Unit + Qt UI + Integration + E2E):
pytest

# Package standalone desktop binary with PyInstaller:
pyinstaller infiniteprep.spec
```

PyInstaller writes the packaged application to `dist/`.
