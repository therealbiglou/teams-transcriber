# Teams Transcriber

A background Windows application that automatically records and transcribes Microsoft Teams meetings, then produces a structured summary with action items, to-dos, and follow-ups using Claude.

**Status:** Pre-implementation. Design spec in `docs/superpowers/specs/`. Implementation plans land in `docs/superpowers/plans/`.

## Design

See [`docs/superpowers/specs/2026-05-14-teams-transcriber-design.md`](docs/superpowers/specs/2026-05-14-teams-transcriber-design.md) for the full design.

## Highlights

- Auto-detects Teams meetings via window-title polling; starts recording with a cancellable toast.
- Captures system audio (loopback) + mic on separate channels so "you" vs "others" is preserved without diarization.
- Local transcription with `faster-whisper` on GPU.
- Structured summarization via Claude API (summary, key decisions, my to-dos, action items for others, follow-ups, topics).
- Searchable history with full-text search across transcripts.
- Manual recording mode for non-Teams meetings.
