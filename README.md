# Teams Transcriber

A background Windows application that automatically records and transcribes Microsoft Teams meetings, then produces a structured summary with action items, to-dos, and follow-ups using Claude.

**Status:** Phases 1-3 shipped. Phase 4 (packaging) on `feature/phase-4-packaging`. Design specs in `docs/superpowers/specs/`; implementation plans in `docs/superpowers/plans/`.

## Design

See [`docs/superpowers/specs/2026-05-14-teams-transcriber-design.md`](docs/superpowers/specs/2026-05-14-teams-transcriber-design.md) for the full design.

## Highlights

- Auto-detects Teams meetings via window-title polling; starts recording with a cancellable toast.
- Captures system audio (loopback) + mic on separate channels so "you" vs "others" is preserved without diarization.
- Local transcription with `faster-whisper` on GPU.
- Structured summarization via Claude API (summary, key decisions, my to-dos, action items for others, follow-ups, topics).
- Searchable history with full-text search across transcripts.
- Manual recording mode for non-Teams meetings.

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
