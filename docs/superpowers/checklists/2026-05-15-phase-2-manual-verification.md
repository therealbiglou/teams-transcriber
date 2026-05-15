# Phase 2 Manual Verification Checklist

Run after Phase 2 lands on main. Each item is a real-world sanity check that
the mocked unit tests cannot catch.

## Environment setup

- [ ] `uv sync --extra dev` completes without errors.
- [ ] `uv run python -c "from faster_whisper import WhisperModel"` runs without errors.
- [ ] CUDA is available: `uv run python -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())"` prints a positive integer.
- [ ] `ANTHROPIC_API_KEY` env var is set (or the key is stored in Windows Credential Manager under service `teams-transcriber`, user `anthropic_api_key`).

## CLI smoke

- [ ] `uv run python -m teams_transcriber list` runs and prints "no recordings" (or recent ones).
- [ ] `uv run python -m teams_transcriber --help` prints all three commands.
- [ ] `uv run python -m teams_transcriber serve` starts and logs "Watching for Teams meetings."

## Audio capture (Phase 2.5 dependent — skip if not yet wired)

When real `AudioSource` lands:
- [ ] Start a Teams meeting; CLI logs `MeetingDetected`.
- [ ] An `.opus` file appears in `%LOCALAPPDATA%\TeamsTranscriber\audio\`.
- [ ] Playing the file in VLC reveals both mic and system audio on separate channels.

## Transcription

- [ ] After Teams meeting ends, status transitions: `transcribing` → `summarizing`.
- [ ] `transcript_segments` table populates with reasonable English text.
- [ ] FTS search finds words spoken in the meeting.

## Summarization

- [ ] Status transitions to `done` within ~30s of transcription complete (Sonnet 4.6 latency).
- [ ] `summaries` row contains: title, one_line, summary, at least one decision/todo/follow_up where applicable.
- [ ] `recordings.display_title` matches the AI-generated title.
- [ ] Re-running `retry-summary <id>` on a `summary_failed` recording succeeds (after fixing whatever caused the failure).

## Failure paths

- [ ] Unset `ANTHROPIC_API_KEY` and run a meeting: status ends at `summary_failed` with a clear error message.
- [ ] Disconnect network mid-summary: 3 retries (1.5s, 2.25s, 3.4s backoff) then `summary_failed`.

## Known Phase 2 limitations (deferred to Phase 2.5)

- Real `AudioSource` not yet wired into the CLI. `serve` will detect meetings and log them but recording itself will fail with `NotImplementedError` when a meeting starts. To exercise the full pipeline today, drive it from a test harness with `FakeAudioSource` (see `tests/test_pipeline.py`).
- Live dual-channel transcription not implemented — Phase 2 transcribes the finalized file in one pass.
- All transcript segments are labeled `channel='others'` (no per-channel separation yet).
