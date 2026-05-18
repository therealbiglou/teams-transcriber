# Teams Transcriber

A background Windows application that automatically records and transcribes Microsoft Teams meetings, then produces a structured summary with action items, to-dos, and follow-ups using Claude.

**Status:** Phases 1-4 shipped on `main`. Design specs in `docs/superpowers/specs/`; implementation plans in `docs/superpowers/plans/`; per-phase manual verification checklists in `docs/superpowers/checklists/`.

## Design

See [`docs/superpowers/specs/2026-05-14-teams-transcriber-design.md`](docs/superpowers/specs/2026-05-14-teams-transcriber-design.md) for the full design.

## Highlights

- Auto-detects Teams meetings via window-title polling; starts recording with a cancellable toast.
- Captures system audio (loopback) + mic on separate channels so "you" vs "others" is preserved without diarization.
- Local transcription with `faster-whisper` on GPU.
- Structured summarization via Claude API (summary, key decisions, my to-dos, action items for others, follow-ups, topics).
- Searchable history with full-text search across transcripts.
- Manual recording mode for non-Teams meetings.

## Setting up on a new machine

Prerequisites:

- Windows 10/11.
- Python 3.11 (`winget install Python.Python.3.11` or download from python.org).
- [uv](https://github.com/astral-sh/uv): `winget install --id astral-sh.uv -e`.
- NVIDIA GPU + recent driver if you want GPU transcription. CPU works as a
  fallback (slower; switch `compute_type` to `int8` in Settings).
- An Anthropic API key from https://console.anthropic.com/.

**Automated path:** run [`scripts/setup_new_machine.ps1`](scripts/setup_new_machine.ps1).
It installs prerequisites (Python 3.11, uv, Git, GitHub CLI) via winget,
authenticates GitHub, clones the repo (if not already present), sets
repo-local git identity, optionally restores Claude Code memory from a zip
on the Desktop, runs `uv sync`, and launches the first-run wizard.
Safe to re-run; uses per-user installs (no admin/UAC needed).

```powershell
# Grab the script (or copy it across from a working machine), then:
powershell -ExecutionPolicy Bypass -File .\setup_new_machine.ps1
```

**Manual path:**

```powershell
git clone <repo-url> teams-transcriber
cd teams-transcriber
uv sync --all-extras
uv run python -m teams_transcriber
```

The first launch shows the wizard — paste your Anthropic key, leave auto-launch
on, click through. The Whisper model (~3 GB) downloads once into
`%USERPROFILE%\.cache\huggingface\` and is reused across launches.

Recordings, database, and settings live in `%LOCALAPPDATA%\TeamsTranscriber\`
and are **per-machine** — moving between machines means each has its own
history. The API key is stored in Windows Credential Manager (service
`teams-transcriber`, user `anthropic_api_key`), also per-machine.

To build a redistributable installer from source, see "Building the installer"
below.

## Building the installer

Prerequisites (one-time setup):

- [Inno Setup 6](https://jrsoftware.org/isdl.php) — free, MIT-licensed.
- Project venv populated: `uv sync --all-extras`.

Then:

```powershell
uv run python scripts/build_installer.py
```

Output: `dist/TeamsTranscriberSetup-<version>.exe` — a user-mode installer
that installs to `%LOCALAPPDATA%\Programs\TeamsTranscriber` with no UAC
prompt. Bundle is ~2.3 GB raw / smaller compressed (CUDA wheels dominate).

To sign the installer, set `TT_SIGN_CERT_PATH` and `TT_SIGN_CERT_PASSWORD`
in the environment before running the build script. Without a cert,
Windows SmartScreen warns users on first install (one-time dismiss).
