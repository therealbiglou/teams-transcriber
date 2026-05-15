# Phase 2 — Known Issues (carry forward into Phase 2.5 / Phase 3)

Surfaced by the Phase 2 final code review. None block merging Phase 2 (CLI works,
all tests pass, deferrals are honest). Each must be addressed before the UI in
Phase 3 starts driving the pipeline, or sooner if real audio capture in
Phase 2.5 reveals more.

## 1. Pipeline post-processing blocks the watcher thread

**File:** `src/teams_transcriber/pipeline.py:93-110`

`_on_meeting_ended` calls `recorder.stop()` synchronously. Inside `stop()`,
`_end()` publishes `RecordingFinalized`, which (synchronously) triggers
`Transcriber.transcribe()` and then `Summarizer.summarize()`. All of this runs
on the watcher thread, so concurrent meetings cannot be detected during
post-processing.

**Fix:** introduce a `concurrent.futures.ThreadPoolExecutor(max_workers=1)`
inside `Pipeline`. Have `_on_recording_finalized` `submit(...)` the transcribe
+summarize work to it. `shutdown()` then `executor.shutdown(wait=True)`.

## 2. `Pipeline._recorder` cleared after the chain runs

**File:** `src/teams_transcriber/pipeline.py:94-96`

Related to #1. While the post-processing chain runs, `_recorder` is still set,
so a new `MeetingDetected` is silently dropped with a warning.

**Fix:** atomically swap `self._recorder` to `None` before publishing
`RecordingFinalized`, e.g. `rec, self._recorder = self._recorder, None; rec.stop()`.

## 3. `shutdown()` doesn't wait for the post-processing chain

**File:** `src/teams_transcriber/pipeline.py:71-77`

Ctrl-C during transcribe/summarize leaves the recording stuck in
`transcribing` or `summarizing` status. No resume logic exists at startup.

**Fix:** once #1 introduces an executor, `shutdown()` waits on it. Also at
Pipeline startup: `RecordingRepo.list_by_status(TRANSCRIBING)` and
`list_by_status(SUMMARIZING)` should be resumed or transitioned to
`*_FAILED`. The `list_by_status` query already exists.

## 4. Channel labels are not yet meaningful

**File:** `src/teams_transcriber/transcriber.py:88`

Every segment is `channel=OTHERS`. The summarizer prompt has been updated to
not promise me/others attribution (committed in `4ac9a81`), but proper
per-channel labeling lands with Phase 2.5's live dual-channel transcriber.

**Fix:** Phase 2.5 — split capture into two separate Opus streams (mic +
loopback), transcribe each separately, label segments accordingly.

## 5. Recorder failures during `_run` don't emit an event

**File:** `src/teams_transcriber/recorder.py:138-145`

If `read_chunk` throws after `start()` returns (e.g. the audio device goes
away mid-recording), the row transitions to `RECORDING_FAILED` but no event
is published. The Pipeline still thinks `_recorder` is alive.

**Fix:** add a `RecordingFailed(recording_id, error_message)` event to
`events.py`. Have `Recorder._run`'s exception handler publish it. Have
`Pipeline` subscribe and clear `self._recorder` on receipt.

## 6. `Settings._raw` shares mutable lists with `DEFAULT_SETTINGS`

**File:** `src/teams_transcriber/config.py:62-72`

`_deep_merge` deep-copies dict values but not list values. Mutating
`settings._raw["detection"]["title_patterns"].append(...)` would corrupt the
module-level default. No current code does this, but a Phase 3 settings
dialog easily could.

**Fix:** swap `_deep_merge` to use `copy.deepcopy` on non-dict values, or
just `copy.deepcopy(DEFAULT_SETTINGS)` at the top of `load_settings`.

## Smaller items (Phase 3 prep, not Phase 2.5)

- **Recorder thread leak detection** (`recorder.py:147-156`): warn if
  `_thread.is_alive()` after the 5-second join.
- **Long-meeting transcript chunking** (`summarizer.py`, spec §6.4): not
  implemented; fail-loud check until it lands.
- **Prompt caching** (spec §6.3): use `cache_control: ephemeral` on the system
  prompt and tool schema — saves significant cost at any meaningful volume.
- **CLI accessing `pipeline._summarizer`** (`cli.py:81`): add a public
  `Pipeline.retry_summary(recording_id)` method.
- **Stop-event in CLI serve loop** (`cli.py:71-73`): replace `while not stopping:
  time.sleep(0.5)` with `threading.Event.wait()`.
- **`OpusWriter.write_chunk` re-imports `av`**: hoist to module level.
