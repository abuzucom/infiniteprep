# Technical Decision Record (TDR): InfinitePrep Architecture, Integrations, and Stack

**Status**: Approved for MVP Implementation  
**Date**: 2026-10-28  
**Project License**: BSD 3-Clause (`ABUZUCOM LLC`)  

---

## 1. Purpose

Before implementing live external integrations or selecting core libraries, this Technical Decision Record (TDR) evaluates every candidate provider and analysis engine specified in Section 4 of the specification against:

1. Current official access method.
2. Authentication requirements.
3. Available fields and response structure.
4. Rate limits.
5. Storage, caching, attribution, and redistribution restrictions.
6. Software licensing and redistribution requirements (relative to the repository's BSD 3-Clause license).
7. Commercial-use restrictions.
8. Packaging feasibility across Windows, macOS, and Linux.

---

## 2. Candidate Integration & Analyzer Evaluation

### 2.1 Audio Fingerprinting: Chromaprint (`fpcalc`) + AcoustID

| Criterion | Findings & Verification |
|---|---|
| **Official Access Method** | Local fingerprint calculation via the official `fpcalc` CLI (`chromaprint`), followed by HTTPS `GET`/`POST` to `https://api.acoustid.org/v2/lookup`. |
| **Authentication** | Requires a registered AcoustID client API key (`client` parameter). User-submitted submissions require an additional user API key (out of scope for MVP). |
| **Available Fields & Structure** | JSON response (`status`, `results[]`) returning AcoustID `id`, `score` (0.0–1.0), and linked MusicBrainz `recordings[]` (containing `id` [MBID], `title`, `duration`, `artists[]`, `releasegroups[]`). Note: Does not reliably distinguish vinyl catalog numbers or remix credits on its own without downstream MusicBrainz/Discogs enrichment. |
| **Rate Limits** | Official limit is **3 requests per second** per client IP. |
| **Caching, Storage & Attribution** | Locally caching lookup responses for active user files is permitted. Attribution to AcoustID (`Powered by AcoustID`) is required in application UI/documentation. |
| **Software Licensing** | `libchromaprint` is **MIT / LGPLv2.1+**. Note: Prebuilt `fpcalc` binaries linked against GPL-compiled FFmpeg are GPLv2+; prebuilt binaries linked against LGPL FFmpeg are LGPLv2.1+. Invoking `fpcalc` as an isolated external subprocess (`fpcalc -json -length 120 <file>`) preserves the BSD 3-Clause license of the main Python application. |
| **Commercial Use** | AcoustID web service is free for non-commercial projects; commercial distribution requires a commercial API plan from AcoustID. |
| **Cross-Platform Packaging** | Standalone `fpcalc` binaries are officially published for Windows (`fpcalc.exe`), macOS (`fpcalc`), and Linux (`fpcalc`). Adapter resolves bundled binary first, then `PATH`. |
| **Decision** | **SUPPORTED (Primary Fingerprint Provider)**. |

---

### 2.2 Recording / Release Metadata: MusicBrainz (`ws/2`)

| Criterion | Findings & Verification |
|---|---|
| **Official Access Method** | Official MusicBrainz XML/JSON Web Service v2 (`https://musicbrainz.org/ws/2/`) using `fmt=json`. |
| **Authentication** | Read-only lookups (`recording`, `release`, `release-group` search and fetch) do not require OAuth tokens, but **strictly require** a descriptive HTTP `User-Agent` header (`InfinitePrep/0.1.0 ( https://github.com/... or contact-email )`). Requests without a unique User-Agent are throttled or blocked. |
| **Available Fields & Structure** | Recording MBID, title, disambiguation (often holds mix/version notes), length (ms), artist-credit, ISRC, and linked `releases[]` (release title, date, country, status, `label-info[]` with catalog-number and label name, `media[]` with format [`12" Vinyl`, `CD`], track position, including vinyl numbering like `A1`, `B1`, andrecording-level `relations[]` such as `remixer`). |
| **Rate Limits** | Strictly **1 request per second** per IP address on the public API (bursts immediately trigger `HTTP 503 Service Unavailable`). |
| **Caching, Storage & Attribution** | Local caching is strongly encouraged by MetaBrainz to reduce server load. Attribution to MusicBrainz is good practice and included in provenance records. |
| **Software Licensing** | MusicBrainz core metadata (`MBID`, artist, title, release, label, catalog number, relationships) is published under **CC0 1.0 Universal (Public Domain)**. Supplementary data is CC-BY-NC-SA. |
| **Commercial Use** | CC0 data permits commercial use. High-volume commercial organizations are asked to subscribe to the MetaBrainz Commercial Data Feed / Live Data Feed. |
| **Cross-Platform Packaging** | Pure HTTPS REST integration (`urllib` / `httpx`); 100% cross-platform (Windows, macOS, Linux). |
| **Decision** | **SUPPORTED (Primary Recording & Release Metadata Provider)**. |

---

### 2.3 Physical-Release Metadata: Discogs API v2.0

| Criterion | Findings & Verification |
|---|---|
| **Official Access Method** | Official REST API (`https://api.discogs.com/database/search`, `/releases/{id}`, `/masters/{id}`). |
| **Authentication** | Requires custom `User-Agent` header plus either a user-supplied **Personal Access Token** (`Authorization: Discogs token=...`) or OAuth 1.0a consumer key/secret. Stored securely via OS `keyring`. |
| **Available Fields & Structure** | Comprehensive catalog for physical and digital DJ releases across all eras and genres (including 12" vinyl singles, promos, white labels, CDs, and digital releases, which is critical for the 1990s–2010s dance single benchmark corpus): `id`, `title`, `artists[]`, `labels[]` (`name`, `catno`), `formats[]` (`Vinyl`, `12"`, `CD`, `File`, `Promo`, `White Label`), `genres[]`, `styles[]`, `year`, `country`, `tracklist[]` (`position` e.g. `A1`, `B2`, `1`, `title`, `duration`, `extraartists[]` containing track-specific `Remix` credits). |
| **Rate Limits** | **60 requests per minute** (authenticated) / **25 requests per minute** (unauthenticated), tracked via `X-Discogs-Ratelimit-Remaining` response headers. |
| **Caching, Storage & Attribution** | Discogs API Terms of Use prohibit building a permanent competing mirror database or caching raw API responses indefinitely (standard transient cache TTL $\le 6$ hours). Values explicitly reviewed and written by the user into their own local music files and audit log are permitted; raw provider cache entries must honor a strict TTL (`expires_at`). Attribution (`Source: Discogs` + release ID) is retained in provenance. |
| **Software Licensing & Commercial Terms** | Discogs data is CC0 (public domain XML dumps), while API access is governed by the Discogs API Terms of Use. |
| **Cross-Platform Packaging** | Pure HTTPS REST integration; 100% cross-platform (Windows, macOS, Linux). |
| **Decision** | **SUPPORTED (Primary Physical-Release Metadata Provider)**. |

---

### 2.4 Local BPM / Key Analysis: Essentia vs. BSD-Compatible Local DSP Analyzer

| Criterion | Findings & Verification |
|---|---|
| **Official Access Method** | Local signal processing on decoded PCM audio frames. |
| **Authentication / Network** | None (100% offline local CPU execution). |
| **Available Fields & Structure** | BPM estimate (float), canonical musical key (`key_tonic` in 12 pitch classes, `key_mode` in `major`/`minor`), `analysis_strength` (confidence/clarity metric), optional beat positions, and diagnostic `warnings` (e.g., short file, low rhythmic energy, tempo octave ambiguity). |
| **Software Licensing** | **Crucial Finding**: **Essentia** (by MTG-UPF) is licensed under **AGPLv3** (or requires a paid proprietary license from UPF for closed-source/non-AGPL distribution). Linking Essentia directly into a **BSD 3-Clause** application causes a license conflict unless distributed under AGPLv3 or isolated as an optional user-supplied external CLI tool. Conversely, **NumPy**, **SciPy**, and **SoundFile (`libsndfile`)** are **BSD / LGPL** licensed and fully compatible with this repository's BSD 3-Clause license. |
| **Cross-Platform Packaging** | Essentia does **not** ship official prebuilt `pip` wheels for Windows or Python 3.14. Conversely, `numpy`, `scipy`, and `soundfile` provide prebuilt binary wheels for **Windows (x64), macOS (Universal2/ARM64), and Linux (x86_64/aarch64)** on Python 3.14. |
| **Decision** | **SUPPORTED via Dual-Adapter Architecture**: <br>1. **`LocalDspAnalyzer` (Default Built-in)**: BSD-compatible tempo (onset envelope autocorrelation & comb-filter bank tuned for 85–175 BPM dance music) and chroma-based Krumhansl-Kessler / Sha'ath key-profile analyzer using `numpy`/`scipy`/`soundfile` (with `miniaudio`/FFmpeg fallback for MP3). Works out-of-the-box on Windows, macOS, and Linux.<br>2. **`EssentiaCliAnalyzer` (Optional External Binary)**: Invokes standalone `essentia_streaming_extractor_music` binary via subprocess when installed by the user, preserving BSD license separation. |

---

### 2.5 Optional Secondary Recognition: ACRCloud

| Criterion | Findings & Verification |
|---|---|
| **Official Access Method** | HTTPS `POST` (`multipart/form-data`) to `https://<host>/v1/identify` sending either a compact fingerprint or a short (10–15s) audio sample excerpt. |
| **Authentication** | Requires `access_key`, `access_secret`, and regional `host`, signed via HMAC-SHA1. Credentials stored in OS `keyring`. |
| **Available Fields & Structure** | JSON metadata (`metadata.music[]`) containing `title`, `artists[]`, `album`, `label`, `release_date`, `duration_ms`, `external_ids` (`isrc`, `upc`), and `score` (0–100). |
| **Rate Limits & Cost** | Commercial metered API (per-lookup billing after trial quota). |
| **Privacy & Caching** | Sends audio excerpt or acoustic fingerprint to third-party cloud infrastructure. **Must be strictly opt-in** with an explicit privacy disclosure before activation. |
| **Decision** | **SUPPORTED (Explicit Opt-In Secondary Provider)**. Disabled by default; only runs when credentials are configured and the user explicitly opts in. |

---

### 2.6 Optional DJ-Store Enrichment: Beatport

| Criterion | Findings & Verification |
|---|---|
| **Official Access Method** | Beatport API v4 (`https://api.beatport.com/v4/`). |
| **Authentication & Access Status** | Beatport v4 API requires formal partner approval from Beatport; public self-service API key registration is closed. Unofficial scraping or using internal web client tokens violates Beatport Terms of Use and is explicitly prohibited by Section 4 of the project specification. |
| **Decision** | **BLOCKED / DEFERRED**. We provide a clean `BeatportAdapter` stub (`is_available() -> (False, "Beatport partner API credentials not configured / access pending")`) behind a feature flag so the architecture supports it immediately once official partner credentials are granted. |

---

## 3. Application Stack & Target OS Summary

- **Target Operating Systems**:
  - **Windows** (Windows 10/11 x64 — current active development host)
  - **macOS** (macOS 12+ Universal2 / Apple Silicon & Intel)
  - **Linux** (Ubuntu 22.04+ / Fedora / Debian x86_64)
- **Runtime & Core Libraries**:
  - **Python 3.12–3.14** (verified on host: `Python 3.14.6`)
  - **Desktop UI**: `PySide6` (Qt 6, LGPLv3) with `QtMultimedia` (`QMediaPlayer` + `QAudioOutput`) for native playback and seeking, and `QTableView`/`QAbstractTableModel` for high-density track management.
  - **Database**: `sqlite3` (built-in, WAL mode, JSON columns, strict foreign keys).
  - **Tag Reading/Writing**: `mutagen` (LGPLv2+) supporting non-destructive **MP3** (`ID3v2.3`/`v2.4`), **FLAC** (`VorbisComments`), and **WAV** (`ID3v2` RIFF chunk via `mutagen.wave` + `LIST-INFO` preservation) reading/writing while preserving unknown/custom tags.
  - **Local Audio Analysis**: `numpy`, `scipy`, `soundfile` (supports FLAC/WAV/OGG/MP3 natively via `libsndfile` 1.1+ with MPG123).
  - **Credentials**: `keyring` (OS-native secret storage) with environment/memory fallback for headless CI.
  - **Packaging**: `PyInstaller` folder/bundle mode (preserves dynamic linking for LGPLv3 compliance across Windows `.exe`, macOS `.app`, and Linux binary bundle).

---

## 4. Outstanding Licensing & Access Questions for Commercial Release

1. **AcoustID Commercial Key**: Before commercial sale/distribution of InfinitePrep, obtain a commercial API plan from AcoustID (free API key is sufficient for development and non-commercial pilot).
2. **Essentia AGPLv3 vs. Built-in BSD Analyzer**: Keep `LocalDspAnalyzer` (BSD-compatible) as the bundled default analyzer. If Essentia is desired inside a commercial closed-source bundle, either license Essentia commercially from MTG-UPF or instruct users to install the standalone Essentia extractor binary separately.
3. **Beatport Partner Application**: Submit a formal partner API application to Beatport if official Beatport store BPM/key/genre enrichment is desired post-MVP.
