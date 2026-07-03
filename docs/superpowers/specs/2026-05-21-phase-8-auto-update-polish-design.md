# Phase 8 — Auto-Update + UI Polish

**Date:** 2026-05-21
**Status:** Approved (Brian, 2026-05-21)
**Branch:** `feature/phase-8-auto-update-polish` (off Phase 7)

## Goals

One new substantial feature plus six small polish items, all driven by Brian's testing feedback.

### New: GitHub-based auto-updater

The repo will become **public** (Brian chose option A) so the updater can hit GitHub's anonymous Releases API. Flow:

1. **On app start** (after the runtime check), a background thread calls `GET https://api.github.com/repos/therealbiglou/teams-transcriber/releases?per_page=1`. Parses the latest release's tag (e.g., `v0.5.1-rc1`) and asset URL.
2. **If newer than installed version**, publish `UpdateAvailable(version, download_url)` on the bus. App handler shows a toast: "Update v0.5.1 available — Install".
3. **User clicks Install**, a small modal dialog appears with a progress bar. Downloads the installer asset (~96 MB) to `%LOCALAPPDATA%\TeamsTranscriber\update\TeamsTranscriberSetup-x.y.z.exe`. Verifies it via the GitHub API's reported size.
4. **After download**, dialog transitions to "Update downloaded. Restart now to install?" with **Restart now** / **Later** buttons.
5. **Restart now:** spawn the installer via `subprocess.Popen([installer, "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"])`, then `sys.exit(0)`. Inno Setup closes the running TeamsTranscriber.exe, installs over it, and re-launches.
6. **Later:** keep app running. The downloaded installer stays in the update dir; user can install manually whenever.

**Settings → About tab:**
- Current version (from `teams_transcriber.__version__`).
- Last update check timestamp (saved in settings).
- "Check for updates now" button — runs the check + result toast immediately.
- "Auto-check on startup" checkbox (default ON). Saved as `general.auto_check_updates`.

**Pre-release handling:** the updater pulls the **most recent** release including pre-releases (e.g., `-rc1` builds), since that's what Brian is testing against. Stable-only is out of scope for v1.

### Polish items

1. **Tray icon shows distinct PROCESSING state.** `render_state_icon(TrayState.PROCESSING)` currently produces an icon visually identical to IDLE. Change PROCESSING to amber/yellow so it's visibly different from green (idle), red (recording), and the existing ERROR state.

2. **Stale "Processing" / "Summarizing" chip on past meetings.** Some recordings ended up with status `SUMMARIZING` despite their summary row being saved (likely because the summarizer's `_persist` crashed between `sum_repo.upsert` and `update_status(DONE)`). Two-pronged fix:
   - **Reorder `_persist`** to call `update_status(DONE)` immediately after `sum_repo.upsert` (before `set_display_title` and the todo writes), so an exception in the later steps doesn't leave the recording in a stuck status.
   - **Extend `_recover_stuck_recordings`** on app start: if a recording's status is `SUMMARIZING` AND a `summaries` row exists for it, transition it to `DONE` (the summary clearly succeeded). If status is `TRANSCRIBING` AND transcript segments exist, transition to `SUMMARIZING` (the transcript clearly succeeded; the post-processing will pick up the SUMMARIZING-to-DONE flow from there, or it'll be marked SUMMARY_FAILED on the next recovery).

3. **Start manual recording from the main app UI.** Add a primary "Record" button to the content header, next to the search bar. Toggles to "Stop" while recording. Hidden/replaced by the active-recording banner once a recording starts (the banner already has Open workspace / Stop functionality once it's processing).

4. **Transcript view → button + new window.** Remove the inline collapsible transcript card from `SummaryPane`. Replace with a single "View transcript" button that opens a new `TranscriptWindow` (frameless, themed, contains the `LiveTranscriptView` widget loaded with the recording's segments). The Workspace window remains for live recordings and notes editing; the new TranscriptWindow is a read-only transcript-only view.

5. **Selectable text in summary view.** Every `QLabel` in `SummaryPane` gets `setTextInteractionFlags(TextSelectableByMouse | TextSelectableByKeyboard)` so users can select / copy any part of the summary.

6. **Delete button on failed recordings.** Currently the SummaryPane's delete button only appears when a `Summary` row exists. The Failed card (introduced in v0.4.4) doesn't have one — Brian wants to clean up failed test recordings. Add a Delete button to the Failed card with the same confirmation flow as the existing one.

## Non-goals (deferred)

- Auto-update for stable-only releases (defer until there's a stable channel to subscribe to).
- Backgrounded incremental download with resume on failure (v1 is whole-installer download).
- Update notes / changelog display in the dialog (link to the release page instead).
- Re-prompting on declined updates (one toast per session).

## Architecture

### New module: `src/teams_transcriber/update_checker.py`

```python
# Sketch — full code in the plan

REPO_OWNER = "therealbiglou"
REPO_NAME = "teams-transcriber"
RELEASES_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases?per_page=1"


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    tag: str          # e.g. "v0.5.1-rc1"
    version: tuple[int, ...]   # e.g. (0, 5, 1)
    is_prerelease: bool
    installer_url: str
    installer_size: int    # bytes
    html_url: str          # link to the release page on github.com


class UpdateCheckError(RuntimeError):
    pass


def fetch_latest_release() -> ReleaseInfo:
    """Hit the GitHub Releases API. Raises UpdateCheckError on any failure."""

def parse_version(tag: str) -> tuple[int, ...]:
    """v0.5.1-rc1 -> (0, 5, 1). Pre-release suffix ignored for comparison."""

def is_update_available(installed_version: str, latest: ReleaseInfo) -> bool:
    """Compare parsed installed vs latest version tuples."""

def download_installer(
    release: ReleaseInfo,
    target_path: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None:
    """Stream the installer to `target_path`. Validates size on completion."""
```

### New events

```python
@dataclass(slots=True, frozen=True)
class UpdateAvailable(Event):
    version: str
    download_url: str
    release_url: str

@dataclass(slots=True, frozen=True)
class UpdateCheckCompleted(Event):
    """Used to refresh the Settings → About 'last checked' display."""
    latest_version: str | None  # None if no newer version available
    checked_at: str  # ISO 8601
```

### New UI: `TranscriptWindow`

`src/teams_transcriber/ui/transcript_window.py` — frameless themed dialog like the Workspace, but with just the `LiveTranscriptView` widget loaded via `load_segments`. Title shows the recording's display title. Close button.

### Modified UI

- `main_window.py` content header: gains a "Record" button alongside the search bar.
- `summary_pane.py`: every `QLabel` gets selectable text. Inline transcript card replaced with a "View transcript" button. Failed card gets a "Delete" button.
- `settings_dialog.py`: new "About" tab as the last tab.
- `tray.py`: rename `notes_action` and friends are already done; add proper PROCESSING icon. `render_state_icon` (in `icons.py`) gets a new amber variant.
- `app.py`: wires UpdateAvailable handler, record-button signal, transcript-window opener.

### Pipeline / Storage

- `summarizer.py`: reorder `_persist` so `update_status(DONE)` happens immediately after `sum_repo.upsert` to prevent the "Summarizing" stuck state.
- `pipeline.py`: `_recover_stuck_recordings` extended to detect "summary exists but status stuck" and fix it.

## Data flow — update check

```
App start
   │
   ├── _bootstrap_gpu_runtime() ─ (unchanged)
   ├── Wizard ─ (unchanged)
   ├── self.pipeline.serve() ─ (unchanged)
   │
   ├── if settings.auto_check_updates:
   │       threading.Thread(target=_check_for_updates, daemon=True).start()
   │
   ▼
_check_for_updates():
   try:
       latest = fetch_latest_release()
       if is_update_available(__version__, latest):
           bus.publish(UpdateAvailable(version=latest.tag, ...))
       bus.publish(UpdateCheckCompleted(...))
   except UpdateCheckError:
       logger.warning(...)
       # Silent — don't pester the user about network failures.
```

## Data flow — install update

```
User clicks "Install" in the toast
   │
   ▼
UpdateDialog opens (modal)
   │
   ├── threading.Thread → download_installer(release, target, progress)
   │       │
   │       └── progress_callback fires per chunk, updates QProgressBar
   │
   ├── On done: dialog state → "Restart now to install?"
   │
   └── User clicks "Restart now":
           subprocess.Popen([installer, "/SILENT", "/CLOSEAPPLICATIONS",
                            "/RESTARTAPPLICATIONS"])
           sys.exit(0)
```

## Error handling

| Failure mode | Behavior |
|---|---|
| Network error during check | Logged at WARNING. No toast. User can retry from Settings → About → Check now. |
| API rate limit (403) | Logged. No toast. (Anonymous GitHub API allows 60 req/hr — well above our usage.) |
| Download fails mid-stream | Dialog shows error + Retry button. Partial file deleted. |
| Installer launch fails | Dialog shows error + a "Show installer in Explorer" option (user can launch manually). |
| Repo still private when first deployed | API returns 404. Treated as "no update available" silently. Won't break the app. |
| Summarizer crashes between upsert and update_status | After this commit: can't happen — update_status is called first. Existing stuck rows fixed by recovery on next start. |

## Testing

### Unit tests

- `update_checker.parse_version` — known inputs → known tuples (handle `v` prefix, pre-release suffix, missing components).
- `update_checker.is_update_available` — installed vs. latest comparison with mocked ReleaseInfo.
- `update_checker.fetch_latest_release` — mocked `urllib.request.urlopen` returning a sample JSON payload.
- `update_checker.download_installer` — mocked download with progress callback verified.
- `Summarizer._persist` — verify status is set to DONE before `set_display_title` (mocked repo records call order).
- `Pipeline._recover_stuck_recordings` — recording stuck at SUMMARIZING with summary row → recovers to DONE.
- `MainWindow` record button — emits signal; toggles label between "Record" and "Stop".
- `TranscriptWindow` — loads segments on construction.
- `SummaryPane` failed card — Delete button emits `delete_requested`.
- `SummaryPane` — all visible QLabels have selectable text interaction flags.
- `tray.render_state_icon` — PROCESSING returns a different image bytes than IDLE.

### Integration / manual

- End-to-end "update available" path with the GitHub API mocked to return a higher version.
- Real-world test: install v0.5.0, push v0.5.1, verify the toast appears on next launch.

## Risks

| Risk | Mitigation |
|---|---|
| Repo not yet public when v0.5.0 ships → update checks silently fail | Make the repo public BEFORE the v0.5.0 release. Test with `curl -s https://api.github.com/repos/therealbiglou/teams-transcriber/releases?per_page=1` returning a 200 with body. |
| Installer can't replace running .exe | Inno Setup's `/CLOSEAPPLICATIONS` flag closes the running app first. Standard pattern. |
| User has the wizard open, then update prompts | Update check fires AFTER the wizard finishes (the auto-check thread starts at the end of `__init__`, but wizard execution blocks `__init__`). |
| Future GitHub API schema changes | Pin to one specific response shape we know works; treat any parse failure as "no update" + log. |
| Code-signing missing → SmartScreen warning on update install | Same as existing installs; user clicks "More info → Run anyway." Out of scope to fix. |
| Brian on metered connection paying per MB | Auto-check just hits the JSON API (~1 KB), not the download. Toast only appears for newer versions. Download only on explicit click. |
