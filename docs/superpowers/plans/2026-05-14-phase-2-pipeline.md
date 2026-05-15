# Phase 2 — Core Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the complete headless pipeline — detect Teams meetings, record dual-channel audio, transcribe with `faster-whisper`, and summarize with Claude. End of phase: `python -m teams_transcriber` runs the app end-to-end without any UI.

**Architecture:** Six loosely-coupled components communicate through a process-local `EventBus`: a `MeetingWatcher` polls Teams windows and emits start/stop events; a `Recorder` listens for start events and writes dual-channel Opus files via PyAV; a `Transcriber` consumes finished recordings (via `faster-whisper`) and writes segments to storage; a `Summarizer` consumes finished transcripts and calls the Anthropic API for a structured JSON summary. A `Pipeline` orchestrator wires them together; a small CLI is the entry point.

**Tech Stack additions (Phase 2):** `pywin32` + `psutil` (Teams detection), `soundcard` (WASAPI capture), `av` (PyAV — FFmpeg bindings for Opus encoding), `faster-whisper` (transcription), `anthropic` (Claude SDK), `numpy` (audio buffers). Continuing to use `uv`, `pytest`, `ruff`, `mypy` strict from Phase 1.

**Spec reference:** [`docs/superpowers/specs/2026-05-14-teams-transcriber-design.md`](../specs/2026-05-14-teams-transcriber-design.md) §§4 (architecture), 5 (pipeline), 6 (AI processing), 11 (phasing).

---

## Design choices that diverge from the literal spec

These are intentional Phase 2 decisions; each is justified inline at point of use.

1. **EventBus is a plain Python pub/sub class, not Qt signals.** Spec §4 calls for Qt signals/slots. Headless Phase 2 has no `QApplication`, and Qt signals require `QObject` parents and an event loop. A small thread-safe pub/sub keeps Phase 2 free of UI dependencies; Phase 3 can either bridge into Qt signals or replace the bus. Footprint is tiny — swappable later.

2. **Live dual-channel transcription is deferred to a Phase 2.5 follow-up.** Spec §5.3 calls for live per-channel Whisper instances; this Phase 2 ships **post-recording** transcription only (transcribe-after-finalize). The VRAM-pressure fallback the spec mentions becomes the *only* mode for now. Architecturally everything is set up so live mode can be added later without changing consumers — the `Transcriber.transcribe(recording_id)` API is the same shape.

3. **Tests use mocks for `WhisperModel` and `anthropic.Anthropic`.** A real Whisper invocation needs ~1.5 GB of model weights and a GPU; a real Anthropic call costs money and isn't reproducible. Unit tests mock both; a written manual-verification checklist (Task 12) covers the real-pipeline path. The audio capture is also tested via a `FakeAudioSource` rather than driving real Windows audio.

4. **Settings live in `config.py` with a small TypedDict-defined defaults dict.** The Claude API key is loaded from `keyring` if available, falling back to the `ANTHROPIC_API_KEY` env var (so tests and CI can drive the pipeline without touching the Windows credential store).

---

## File structure (Phase 2 produces)

```
src/teams_transcriber/
├── __init__.py                            unchanged (version)
├── __main__.py                            NEW — `python -m teams_transcriber` → cli.main()
├── paths.py                               unchanged
├── config.py                              NEW — settings.json + keyring API key
├── events.py                              NEW — EventBus + Event dataclasses
├── meeting_watcher.py                     NEW — Teams detection (state machine + Win32)
├── audio/
│   ├── __init__.py                        NEW
│   ├── devices.py                         NEW — soundcard device enumeration
│   ├── source.py                          NEW — AudioSource protocol + RealAudioSource
│   └── opus_writer.py                     NEW — PyAV-based 2-channel Opus encoder
├── recorder.py                            NEW — orchestrates capture + opus_writer + DB
├── transcriber.py                         NEW — faster-whisper integration (post-mode)
├── summarizer.py                          NEW — Anthropic SDK + structured output + retry
├── pipeline.py                            NEW — wires EventBus + components
├── cli.py                                 NEW — argparse-based CLI commands
└── storage/                               unchanged (Phase 1)

tests/
├── conftest.py                            modified — add fakes/fixtures helpers
├── test_config.py                         NEW
├── test_events.py                         NEW
├── test_meeting_watcher.py                NEW
├── audio/
│   ├── __init__.py                        NEW
│   ├── test_opus_writer.py                NEW
│   ├── test_devices.py                    NEW (smoke only)
│   └── test_source.py                     NEW
├── test_recorder.py                       NEW
├── test_transcriber.py                    NEW (WhisperModel mocked)
├── test_summarizer.py                     NEW (anthropic SDK mocked)
├── test_pipeline.py                       NEW (integration with fakes)
├── test_cli.py                            NEW (argparse smoke)
└── storage/                               unchanged
```

---

## Task 1: Phase 2 bootstrap — add dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock` (auto-generated)

This task only adds the runtime dependencies and verifies they import cleanly. No new application code.

- [ ] **Step 1: Update `pyproject.toml` dependencies**

Edit `pyproject.toml`. Replace the `dependencies = []` line with:

```toml
dependencies = [
    "pywin32>=306",
    "psutil>=5.9",
    "soundcard>=0.4.3",
    "av>=12.0",
    "faster-whisper>=1.0",
    "anthropic>=0.40",
    "numpy>=1.26",
    "keyring>=24",
]
```

And add to the `[project.optional-dependencies]` dev list:
```toml
dev = [
    "pytest>=8.0",
    "pytest-cov>=4.1",
    "ruff>=0.5",
    "mypy>=1.10",
    "types-pywin32",
    "types-psutil",
]
```

- [ ] **Step 2: Sync dependencies**

Run:
```powershell
uv sync --extra dev
```

Expected: lock file updates and ~50 new packages install. `av` is the largest (~50 MB — bundles FFmpeg). `faster-whisper` pulls in `ctranslate2`, `tokenizers`, and a few NVIDIA wheels (cuDNN, cuBLAS).

- [ ] **Step 3: Smoke-import each new dependency**

Run:
```powershell
uv run python -c "import pywin32_system32; import psutil; import soundcard; import av; import faster_whisper; import anthropic; import numpy; import keyring; print('OK')"
```

Expected: prints `OK`. If `pywin32_system32` import fails (it's a separate module installed by pywin32), use `import win32api` instead — that's the more reliable smoke-test.

- [ ] **Step 4: Run existing tests to confirm Phase 1 didn't regress**

```powershell
uv run pytest -v
```
Expected: 65 passed.

- [ ] **Step 5: Lint + types**

```powershell
uv run ruff check src tests
uv run mypy
```

Both clean. If mypy complains about missing stubs for `soundcard`, `av`, `faster_whisper`, `anthropic`, add a per-module override to `pyproject.toml` under `[tool.mypy]`:

```toml
[[tool.mypy.overrides]]
module = ["soundcard.*", "av.*", "faster_whisper.*", "anthropic.*"]
ignore_missing_imports = true
```

Then re-run `uv run mypy` and confirm clean.

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml uv.lock
git commit -m "chore(deps): add Phase 2 runtime dependencies"
```

---

## Task 2: Config loader

**Files:**
- Create: `src/teams_transcriber/config.py`
- Test: `tests/test_config.py`

Loads `settings.json` from `AppPaths.config_dir`, with sensible defaults if missing. Exposes a `Settings` object with typed access. The Claude API key uses `keyring` if available, falling back to `ANTHROPIC_API_KEY`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_config.py`:
```python
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from teams_transcriber.config import (
    DEFAULT_SETTINGS,
    Settings,
    load_settings,
    save_settings,
)
from teams_transcriber.paths import AppPaths


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    p = AppPaths(root=tmp_path / "TT")
    p.ensure_dirs()
    return p


def test_load_returns_defaults_when_file_missing(paths: AppPaths) -> None:
    s = load_settings(paths)
    assert s.ai_model == DEFAULT_SETTINGS["ai"]["model"]
    assert s.detection_poll_interval_ms == DEFAULT_SETTINGS["detection"]["poll_interval_ms"]
    assert s.audio_retention_days == DEFAULT_SETTINGS["audio"]["retention_days"]


def test_save_then_load_round_trips(paths: AppPaths) -> None:
    s = load_settings(paths)
    s.ai_model = "claude-opus-4-7"
    s.audio_retention_days = 60
    save_settings(paths, s)
    again = load_settings(paths)
    assert again.ai_model == "claude-opus-4-7"
    assert again.audio_retention_days == 60


def test_partial_settings_file_merges_with_defaults(paths: AppPaths) -> None:
    """A settings file missing some keys still loads — missing keys come from defaults."""
    settings_path = paths.config_dir / "settings.json"
    settings_path.write_text(json.dumps({"ai": {"model": "claude-haiku-4-5"}}))
    s = load_settings(paths)
    assert s.ai_model == "claude-haiku-4-5"
    # Other defaults still present:
    assert s.detection_poll_interval_ms == DEFAULT_SETTINGS["detection"]["poll_interval_ms"]


def test_malformed_json_falls_back_to_defaults(paths: AppPaths) -> None:
    (paths.config_dir / "settings.json").write_text("not valid json {")
    s = load_settings(paths)
    assert s.ai_model == DEFAULT_SETTINGS["ai"]["model"]


def test_api_key_from_env(paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-env")
    s = load_settings(paths)
    assert s.anthropic_api_key() == "sk-test-env"


def test_api_key_from_keyring(paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import keyring
    from keyring.backend import KeyringBackend

    class FakeKeyring(KeyringBackend):
        priority = 1  # type: ignore[assignment]

        def __init__(self) -> None:
            self._store: dict[tuple[str, str], str] = {}

        def get_password(self, service: str, username: str) -> str | None:
            return self._store.get((service, username))

        def set_password(self, service: str, username: str, password: str) -> None:
            self._store[(service, username)] = password

        def delete_password(self, service: str, username: str) -> None:
            self._store.pop((service, username), None)

    fk = FakeKeyring()
    fk.set_password("teams-transcriber", "anthropic_api_key", "sk-test-ring")
    keyring.set_keyring(fk)
    try:
        s = load_settings(paths)
        assert s.anthropic_api_key() == "sk-test-ring"
    finally:
        keyring.set_keyring(keyring.backends.fail.Keyring())  # type: ignore[attr-defined]


def test_api_key_returns_none_when_unset(paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import keyring
    keyring.set_keyring(keyring.backends.fail.Keyring())  # type: ignore[attr-defined]
    s = load_settings(paths)
    assert s.anthropic_api_key() is None
```

- [ ] **Step 2: Run to confirm failure**

```powershell
uv run pytest tests/test_config.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `config.py`**

Create `src/teams_transcriber/config.py`:
```python
"""Application settings loaded from disk, with defaults baked in."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import keyring

from teams_transcriber.paths import AppPaths

KEYRING_SERVICE = "teams-transcriber"
KEYRING_USER_ANTHROPIC = "anthropic_api_key"


# Default settings — load_settings merges any user-provided file on top of this.
DEFAULT_SETTINGS: dict[str, Any] = {
    "general": {
        "auto_launch": True,
        "pause_on_startup": False,
    },
    "audio": {
        "mic_device": None,
        "loopback_device": None,
        "retention_days": 30,
        "bitrate_kbps": 24,
    },
    "detection": {
        "poll_interval_ms": 2000,
        "debounce_polls": 2,
        "title_patterns": [
            "Meeting in progress | Microsoft Teams",
            "Meeting | Microsoft Teams",
            "| Microsoft Teams Call",
        ],
    },
    "transcription": {
        "model": "large-v3-turbo",
        "compute_type": "int8_float16",
        "language": "en",
        "live_dual_channel": False,  # Phase 2 ships post-mode only; live is Phase 2.5.
    },
    "ai": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "custom_prompt_addendum": "",
        "max_retries": 3,
    },
    "hotkeys": {
        "toggle_manual_recording": "ctrl+alt+r",
        "toggle_pause_detection": "ctrl+alt+p",
    },
}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Return `base` with values from `overlay` recursively applied. Mutates a copy."""
    result = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


@dataclass(slots=True)
class Settings:
    """Typed view over the settings dict. New fields go here as the app grows."""

    _raw: dict[str, Any] = field(default_factory=lambda: _deep_merge(DEFAULT_SETTINGS, {}))

    # --- general
    @property
    def auto_launch(self) -> bool:
        return bool(self._raw["general"]["auto_launch"])

    @auto_launch.setter
    def auto_launch(self, value: bool) -> None:
        self._raw["general"]["auto_launch"] = bool(value)

    # --- audio
    @property
    def mic_device(self) -> str | None:
        return self._raw["audio"].get("mic_device")

    @property
    def loopback_device(self) -> str | None:
        return self._raw["audio"].get("loopback_device")

    @property
    def audio_retention_days(self) -> int:
        return int(self._raw["audio"]["retention_days"])

    @audio_retention_days.setter
    def audio_retention_days(self, value: int) -> None:
        self._raw["audio"]["retention_days"] = int(value)

    @property
    def audio_bitrate_kbps(self) -> int:
        return int(self._raw["audio"]["bitrate_kbps"])

    # --- detection
    @property
    def detection_poll_interval_ms(self) -> int:
        return int(self._raw["detection"]["poll_interval_ms"])

    @property
    def detection_debounce_polls(self) -> int:
        return int(self._raw["detection"]["debounce_polls"])

    @property
    def detection_title_patterns(self) -> list[str]:
        return list(self._raw["detection"]["title_patterns"])

    # --- transcription
    @property
    def transcription_model(self) -> str:
        return str(self._raw["transcription"]["model"])

    @property
    def transcription_compute_type(self) -> str:
        return str(self._raw["transcription"]["compute_type"])

    @property
    def transcription_language(self) -> str:
        return str(self._raw["transcription"]["language"])

    @property
    def transcription_live_dual_channel(self) -> bool:
        return bool(self._raw["transcription"]["live_dual_channel"])

    # --- ai
    @property
    def ai_model(self) -> str:
        return str(self._raw["ai"]["model"])

    @ai_model.setter
    def ai_model(self, value: str) -> None:
        self._raw["ai"]["model"] = value

    @property
    def ai_custom_prompt_addendum(self) -> str:
        return str(self._raw["ai"]["custom_prompt_addendum"])

    @property
    def ai_max_retries(self) -> int:
        return int(self._raw["ai"]["max_retries"])

    def anthropic_api_key(self) -> str | None:
        """Resolve the Anthropic API key. Env var wins over keyring (useful for CI/tests)."""
        env = os.environ.get("ANTHROPIC_API_KEY")
        if env:
            return env
        try:
            return keyring.get_password(KEYRING_SERVICE, KEYRING_USER_ANTHROPIC)
        except keyring.errors.KeyringError:
            return None

    def to_dict(self) -> dict[str, Any]:
        return _deep_merge(self._raw, {})


def load_settings(paths: AppPaths) -> Settings:
    """Load settings.json from disk; fall back to defaults if missing or malformed."""
    settings_path = paths.config_dir / "settings.json"
    raw = _deep_merge(DEFAULT_SETTINGS, {})
    if settings_path.exists():
        try:
            user = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                raw = _deep_merge(raw, user)
        except (json.JSONDecodeError, OSError):
            # Malformed file — log and fall back to defaults.
            pass
    return Settings(_raw=raw)


def save_settings(paths: AppPaths, settings: Settings) -> None:
    """Persist current settings to settings.json. Creates config_dir if needed."""
    paths.ensure_dirs()
    settings_path = paths.config_dir / "settings.json"
    settings_path.write_text(
        json.dumps(settings.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```powershell
uv run pytest tests/test_config.py -v
```
Expected: all 7 tests PASS.

- [ ] **Step 5: Lint + types**

```powershell
uv run ruff check src tests
uv run mypy
```
Both clean.

- [ ] **Step 6: Commit**

```powershell
git add src/teams_transcriber/config.py tests/test_config.py
git commit -m "feat(config): add Settings with JSON file + keyring API key resolution"
```

---

## Task 3: EventBus and events

**Files:**
- Create: `src/teams_transcriber/events.py`
- Test: `tests/test_events.py`

A small thread-safe pub/sub bus. Components publish typed `Event` dataclasses; subscribers register handlers by event type. The bus is not Qt-based — see "Design choices" above.

- [ ] **Step 1: Write failing tests**

Create `tests/test_events.py`:
```python
from __future__ import annotations

import threading

from teams_transcriber.events import (
    EventBus,
    MeetingDetected,
    MeetingEnded,
    RecordingFinalized,
    RecordingStarted,
    SummaryReady,
    TranscriptionComplete,
)


def test_subscribe_and_publish_calls_handler() -> None:
    bus = EventBus()
    received: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, received.append)
    evt = MeetingDetected(window_title="Meeting | Microsoft Teams")
    bus.publish(evt)
    assert received == [evt]


def test_publish_with_no_subscribers_is_noop() -> None:
    bus = EventBus()
    bus.publish(MeetingEnded())  # must not raise


def test_multiple_handlers_each_receive_event() -> None:
    bus = EventBus()
    a: list[MeetingDetected] = []
    b: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, a.append)
    bus.subscribe(MeetingDetected, b.append)
    evt = MeetingDetected(window_title="X")
    bus.publish(evt)
    assert a == [evt]
    assert b == [evt]


def test_handler_exception_does_not_block_other_handlers() -> None:
    bus = EventBus()

    def boom(_e: MeetingDetected) -> None:
        raise RuntimeError("intentional")

    received: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, boom)
    bus.subscribe(MeetingDetected, received.append)
    bus.publish(MeetingDetected(window_title="X"))
    assert len(received) == 1  # second handler still ran


def test_unsubscribe_removes_handler() -> None:
    bus = EventBus()
    received: list[MeetingDetected] = []
    handler = received.append
    bus.subscribe(MeetingDetected, handler)
    bus.unsubscribe(MeetingDetected, handler)
    bus.publish(MeetingDetected(window_title="X"))
    assert received == []


def test_different_event_types_are_isolated() -> None:
    bus = EventBus()
    md: list[MeetingDetected] = []
    me: list[MeetingEnded] = []
    bus.subscribe(MeetingDetected, md.append)
    bus.subscribe(MeetingEnded, me.append)
    bus.publish(MeetingDetected(window_title="X"))
    assert len(md) == 1
    assert me == []


def test_publish_is_thread_safe() -> None:
    bus = EventBus()
    counter = 0
    lock = threading.Lock()

    def handler(_e: MeetingDetected) -> None:
        nonlocal counter
        with lock:
            counter += 1

    bus.subscribe(MeetingDetected, handler)

    def worker() -> None:
        for _ in range(100):
            bus.publish(MeetingDetected(window_title="X"))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert counter == 400


def test_event_dataclasses_carry_expected_fields() -> None:
    """Smoke check that the event types have the fields callers will use."""
    MeetingDetected(window_title="X")
    MeetingEnded()
    RecordingStarted(recording_id=1, audio_path="C:/a.opus")
    RecordingFinalized(recording_id=1, duration_ms=12345)
    TranscriptionComplete(recording_id=1, segment_count=10)
    SummaryReady(recording_id=1)
```

- [ ] **Step 2: Run to confirm failure**

```powershell
uv run pytest tests/test_events.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `events.py`**

Create `src/teams_transcriber/events.py`:
```python
"""Process-local event bus and event dataclasses.

Phase 2 deliberately uses a plain Python pub/sub (not Qt signals) so the headless
pipeline does not depend on PySide6 or a QApplication. Phase 3 can either bridge
EventBus → Qt signals or replace the bus.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

logger = logging.getLogger(__name__)

# --- Event dataclasses ----------------------------------------------------


@dataclass(slots=True, frozen=True)
class Event:
    """Marker base — every event subclass is itself an event type."""


@dataclass(slots=True, frozen=True)
class MeetingDetected(Event):
    window_title: str


@dataclass(slots=True, frozen=True)
class MeetingEnded(Event):
    pass


@dataclass(slots=True, frozen=True)
class RecordingStarted(Event):
    recording_id: int
    audio_path: str


@dataclass(slots=True, frozen=True)
class RecordingFinalized(Event):
    recording_id: int
    duration_ms: int


@dataclass(slots=True, frozen=True)
class TranscriptionComplete(Event):
    recording_id: int
    segment_count: int


@dataclass(slots=True, frozen=True)
class SummaryReady(Event):
    recording_id: int


# --- EventBus -------------------------------------------------------------

E = TypeVar("E", bound=Event)
Handler = Callable[[E], None]


class EventBus:
    """Synchronous, thread-safe pub/sub keyed by event class.

    Handlers run on whatever thread calls publish(). Exceptions in one handler
    do not abort other handlers — they're logged and swallowed.
    """

    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[Handler]] = {}
        self._lock = threading.RLock()

    def subscribe(self, event_type: type[E], handler: Handler[E]) -> None:
        with self._lock:
            self._handlers.setdefault(event_type, []).append(handler)  # type: ignore[arg-type]

    def unsubscribe(self, event_type: type[E], handler: Handler[E]) -> None:
        with self._lock:
            handlers = self._handlers.get(event_type)
            if handlers is None:
                return
            try:
                handlers.remove(handler)  # type: ignore[arg-type]
            except ValueError:
                pass

    def publish(self, event: Event) -> None:
        with self._lock:
            handlers = list(self._handlers.get(type(event), ()))
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception("event handler %r raised on %r", handler, event)
```

- [ ] **Step 4: Run tests to verify passing**

```powershell
uv run pytest tests/test_events.py -v
```
Expected: 8 tests PASS.

- [ ] **Step 5: Lint + types**

```powershell
uv run ruff check src tests
uv run mypy
```
Both clean.

- [ ] **Step 6: Commit**

```powershell
git add src/teams_transcriber/events.py tests/test_events.py
git commit -m "feat(events): add EventBus + event dataclasses"
```

---

## Task 4: MeetingWatcher state machine (pure logic)

**Files:**
- Create: `src/teams_transcriber/meeting_watcher.py` (state machine only — Win32 polling lands in Task 5)
- Test: `tests/test_meeting_watcher.py`

The state machine is independent of how windows are enumerated. The `MeetingWatcher` takes a callable `current_windows() -> list[WindowInfo]` and ticks one cycle per `step()` call. Test by driving `step()` directly.

- [ ] **Step 1: Write failing tests**

Create `tests/test_meeting_watcher.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

from teams_transcriber.events import EventBus, MeetingDetected, MeetingEnded
from teams_transcriber.meeting_watcher import (
    MeetingWatcher,
    WatcherState,
    WindowInfo,
)


@dataclass
class FakeWindows:
    """Drives current_windows() output by appending lists to .scripted."""

    scripted: list[list[WindowInfo]]

    def __call__(self) -> list[WindowInfo]:
        return self.scripted.pop(0) if self.scripted else []


def _teams_meeting(title: str = "Meeting in progress | Microsoft Teams") -> WindowInfo:
    return WindowInfo(pid=123, process_name="ms-teams.exe", title=title)


def _no_match() -> WindowInfo:
    return WindowInfo(pid=99, process_name="explorer.exe", title="File Explorer")


def test_emits_meeting_detected_after_debounce() -> None:
    bus = EventBus()
    received: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, received.append)

    fw = FakeWindows(scripted=[
        [_no_match()],               # tick 1: IDLE → IDLE
        [_teams_meeting()],          # tick 2: IDLE → CANDIDATE (not yet emitted)
        [_teams_meeting()],          # tick 3: CANDIDATE → IN_MEETING (emit)
        [_teams_meeting()],          # tick 4: IN_MEETING → IN_MEETING
    ])
    w = MeetingWatcher(
        bus=bus,
        current_windows=fw,
        title_patterns=["Meeting in progress | Microsoft Teams"],
        debounce_polls=2,
    )
    w.step()
    w.step()
    assert received == []
    w.step()
    assert len(received) == 1
    assert received[0].window_title == "Meeting in progress | Microsoft Teams"


def test_emits_meeting_ended_after_window_disappears() -> None:
    bus = EventBus()
    ended: list[MeetingEnded] = []
    bus.subscribe(MeetingEnded, ended.append)

    fw = FakeWindows(scripted=[
        [_teams_meeting()],          # CANDIDATE
        [_teams_meeting()],          # IN_MEETING (emits start)
        [_no_match()],               # LEAVING (not yet emit end)
        [_no_match()],               # IDLE (emit end)
    ])
    w = MeetingWatcher(
        bus=bus,
        current_windows=fw,
        title_patterns=["Meeting in progress | Microsoft Teams"],
        debounce_polls=2,
    )
    w.step(); w.step()
    assert ended == []
    w.step()
    assert ended == []
    w.step()
    assert len(ended) == 1


def test_flicker_does_not_emit() -> None:
    """One-tick title flicker (CANDIDATE then back to nothing) doesn't fire."""
    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)

    fw = FakeWindows(scripted=[
        [_no_match()],
        [_teams_meeting()],     # IDLE → CANDIDATE
        [_no_match()],          # CANDIDATE → IDLE (no emit)
        [_no_match()],
    ])
    w = MeetingWatcher(
        bus=bus,
        current_windows=fw,
        title_patterns=["Meeting in progress | Microsoft Teams"],
        debounce_polls=2,
    )
    for _ in range(4):
        w.step()
    assert detected == []


def test_substring_matching() -> None:
    """Configured patterns match as case-insensitive substrings of window title."""
    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)

    fw = FakeWindows(scripted=[
        [WindowInfo(pid=1, process_name="ms-teams.exe",
                    title="Brian's daily call | Microsoft Teams Call")],
    ] * 3)
    w = MeetingWatcher(
        bus=bus,
        current_windows=fw,
        title_patterns=["| Microsoft Teams Call"],
        debounce_polls=2,
    )
    for _ in range(3):
        w.step()
    assert len(detected) == 1


def test_non_teams_process_is_ignored() -> None:
    """A window with a matching title but the wrong process must not trigger."""
    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)

    fw = FakeWindows(scripted=[
        [WindowInfo(pid=1, process_name="notepad.exe",
                    title="Meeting in progress | Microsoft Teams - notes.txt")],
    ] * 3)
    w = MeetingWatcher(
        bus=bus,
        current_windows=fw,
        title_patterns=["Meeting in progress | Microsoft Teams"],
        debounce_polls=2,
    )
    for _ in range(3):
        w.step()
    assert detected == []


def test_pause_blocks_emission() -> None:
    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)

    fw = FakeWindows(scripted=[[_teams_meeting()]] * 3)
    w = MeetingWatcher(
        bus=bus,
        current_windows=fw,
        title_patterns=["Meeting in progress | Microsoft Teams"],
        debounce_polls=2,
    )
    w.set_paused(True)
    for _ in range(3):
        w.step()
    assert detected == []
    assert w.state == WatcherState.IDLE


def test_resume_after_pause_can_detect() -> None:
    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)

    fw = FakeWindows(scripted=[[_teams_meeting()]] * 6)
    w = MeetingWatcher(
        bus=bus,
        current_windows=fw,
        title_patterns=["Meeting in progress | Microsoft Teams"],
        debounce_polls=2,
    )
    w.set_paused(True)
    for _ in range(3):
        w.step()
    w.set_paused(False)
    for _ in range(3):
        w.step()
    assert len(detected) == 1
```

- [ ] **Step 2: Run to confirm failure**

```powershell
uv run pytest tests/test_meeting_watcher.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement state machine in `meeting_watcher.py`**

Create `src/teams_transcriber/meeting_watcher.py`:
```python
"""Polls Teams windows and emits MeetingDetected / MeetingEnded events.

This module is split in two:
  * The state-machine + filter logic (here) is pure Python and fully tested.
  * The Win32 window-enumeration (`_enumerate_windows`) is the only OS-bound piece.

The polling loop runs in `MeetingWatcher.run_forever`, which calls `step()` every
`poll_interval_ms`. Tests drive `step()` directly with a scripted `current_windows`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto

from teams_transcriber.events import EventBus, MeetingDetected, MeetingEnded

logger = logging.getLogger(__name__)

TEAMS_PROCESS_NAMES: frozenset[str] = frozenset({"ms-teams.exe"})


@dataclass(slots=True, frozen=True)
class WindowInfo:
    pid: int
    process_name: str  # lowercased exe name, e.g. "ms-teams.exe"
    title: str


class WatcherState(Enum):
    IDLE = auto()
    CANDIDATE = auto()
    IN_MEETING = auto()
    LEAVING = auto()


class MeetingWatcher:
    """Stateful poller. Drive via `step()` (tests) or `run_forever()` (production)."""

    def __init__(
        self,
        bus: EventBus,
        current_windows: Callable[[], list[WindowInfo]],
        title_patterns: list[str],
        debounce_polls: int = 2,
        poll_interval_ms: int = 2000,
    ) -> None:
        self._bus = bus
        self._current_windows = current_windows
        self._title_patterns = [p.lower() for p in title_patterns]
        self._debounce = max(1, debounce_polls)
        self._poll_interval_s = poll_interval_ms / 1000.0
        self.state = WatcherState.IDLE
        self._consecutive_match = 0
        self._consecutive_miss = 0
        self._paused = False
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = paused
            if paused:
                # Reset machine so resume starts fresh.
                self.state = WatcherState.IDLE
                self._consecutive_match = 0
                self._consecutive_miss = 0

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def step(self) -> None:
        """One poll cycle. Public for testability."""
        if self.is_paused():
            return

        try:
            windows = self._current_windows()
        except Exception:
            logger.exception("current_windows() raised; treating as no-match")
            windows = []

        match = self._find_meeting_window(windows)

        if match is not None:
            self._consecutive_match += 1
            self._consecutive_miss = 0
        else:
            self._consecutive_miss += 1
            self._consecutive_match = 0

        self._advance(match)

    def run_forever(self) -> None:
        """Production loop. Returns only after stop() is called."""
        while not self._stop.is_set():
            start = time.monotonic()
            self.step()
            elapsed = time.monotonic() - start
            self._stop.wait(timeout=max(0.0, self._poll_interval_s - elapsed))

    def stop(self) -> None:
        self._stop.set()

    # --- internals ---------------------------------------------------------

    def _find_meeting_window(self, windows: list[WindowInfo]) -> WindowInfo | None:
        for w in windows:
            if w.process_name.lower() not in TEAMS_PROCESS_NAMES:
                continue
            title_lower = w.title.lower()
            if any(p in title_lower for p in self._title_patterns):
                return w
        return None

    def _advance(self, match: WindowInfo | None) -> None:
        if self.state == WatcherState.IDLE:
            if self._consecutive_match >= 1:
                self.state = WatcherState.CANDIDATE
                if self._consecutive_match >= self._debounce and match is not None:
                    self._enter_meeting(match)
        elif self.state == WatcherState.CANDIDATE:
            if self._consecutive_match >= self._debounce and match is not None:
                self._enter_meeting(match)
            elif self._consecutive_miss >= 1:
                self.state = WatcherState.IDLE
        elif self.state == WatcherState.IN_MEETING:
            if self._consecutive_miss >= 1:
                self.state = WatcherState.LEAVING
                if self._consecutive_miss >= self._debounce:
                    self._leave_meeting()
        elif self.state == WatcherState.LEAVING:
            if self._consecutive_miss >= self._debounce:
                self._leave_meeting()
            elif self._consecutive_match >= 1:
                self.state = WatcherState.IN_MEETING

    def _enter_meeting(self, w: WindowInfo) -> None:
        self.state = WatcherState.IN_MEETING
        self._bus.publish(MeetingDetected(window_title=w.title))

    def _leave_meeting(self) -> None:
        self.state = WatcherState.IDLE
        self._consecutive_match = 0
        self._consecutive_miss = 0
        self._bus.publish(MeetingEnded())
```

- [ ] **Step 4: Run tests, fix until green**

```powershell
uv run pytest tests/test_meeting_watcher.py -v
```
Expected: 7 tests PASS.

- [ ] **Step 5: Lint + types**

```powershell
uv run ruff check src tests
uv run mypy
```

- [ ] **Step 6: Commit**

```powershell
git add src/teams_transcriber/meeting_watcher.py tests/test_meeting_watcher.py
git commit -m "feat(meeting_watcher): add state machine + EventBus integration"
```

---

## Task 5: MeetingWatcher Win32 integration

**Files:**
- Modify: `src/teams_transcriber/meeting_watcher.py` (append `enumerate_windows()`)
- Modify: `tests/test_meeting_watcher.py` (append a smoke test)

Real window enumeration via pywin32 + psutil. The unit-tested state machine is unchanged; this just provides a concrete `current_windows` callable.

- [ ] **Step 1: Append the smoke test**

Append to `tests/test_meeting_watcher.py`:
```python


def test_enumerate_windows_returns_list_on_windows() -> None:
    """Smoke: real enumeration returns *some* windows on a real OS.

    We don't assert specific contents — the test just confirms the call doesn't
    raise and returns the right shape. CI on non-Windows will skip this test.
    """
    import sys

    if not sys.platform.startswith("win"):
        import pytest
        pytest.skip("Win32 enumeration is Windows-only")

    from teams_transcriber.meeting_watcher import enumerate_windows

    windows = enumerate_windows()
    assert isinstance(windows, list)
    if windows:
        w = windows[0]
        assert isinstance(w.pid, int)
        assert isinstance(w.process_name, str)
        assert isinstance(w.title, str)
```

- [ ] **Step 2: Run to confirm failure (or skip on non-Windows)**

```powershell
uv run pytest tests/test_meeting_watcher.py::test_enumerate_windows_returns_list_on_windows -v
```
Expected: FAIL — `ImportError: cannot import name 'enumerate_windows'`.

- [ ] **Step 3: Implement `enumerate_windows` in `meeting_watcher.py`**

Append to `src/teams_transcriber/meeting_watcher.py`:
```python


def enumerate_windows() -> list[WindowInfo]:
    """Return all visible top-level windows on the current Windows desktop.

    Returns an empty list (and logs a warning) on non-Windows or if pywin32/psutil
    aren't available, so the rest of the pipeline can still be wired up in tests.
    """
    try:
        import psutil
        import win32gui
        import win32process
    except ImportError:
        logger.warning("pywin32/psutil not available — enumerate_windows() returns []")
        return []

    results: list[WindowInfo] = []
    process_name_cache: dict[int, str] = {}

    def _process_name_for(pid: int) -> str:
        if pid in process_name_cache:
            return process_name_cache[pid]
        try:
            name = psutil.Process(pid).name().lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            name = ""
        process_name_cache[pid] = name
        return name

    def _callback(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return True
        results.append(WindowInfo(pid=pid, process_name=_process_name_for(pid), title=title))
        return True

    win32gui.EnumWindows(_callback, None)
    return results
```

- [ ] **Step 4: Run the smoke test**

```powershell
uv run pytest tests/test_meeting_watcher.py::test_enumerate_windows_returns_list_on_windows -v
```
Expected: PASS (on Windows).

- [ ] **Step 5: Full suite + lint + types**

```powershell
uv run pytest -v
uv run ruff check src tests
uv run mypy
```

- [ ] **Step 6: Commit**

```powershell
git add src/teams_transcriber/meeting_watcher.py tests/test_meeting_watcher.py
git commit -m "feat(meeting_watcher): add Win32 enumerate_windows()"
```

---

## Task 6: Audio devices helper

**Files:**
- Create: `src/teams_transcriber/audio/__init__.py`
- Create: `src/teams_transcriber/audio/devices.py`
- Test: `tests/audio/__init__.py`
- Test: `tests/audio/test_devices.py`

Thin convenience wrappers over `soundcard` for default mic + default-speaker loopback. Mostly so other modules don't have to know about `soundcard`'s API.

- [ ] **Step 1: Create empty `__init__.py` files**

`src/teams_transcriber/audio/__init__.py`:
```python
"""Audio capture and encoding."""
```

`tests/audio/__init__.py`:
```python
```

- [ ] **Step 2: Write failing tests**

Create `tests/audio/test_devices.py`:
```python
from __future__ import annotations

import sys

import pytest

from teams_transcriber.audio.devices import (
    AudioDevice,
    default_loopback,
    default_microphone,
    list_microphones,
    list_speakers,
)


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows audio devices only")
def test_default_microphone_returns_something() -> None:
    mic = default_microphone()
    assert mic is None or isinstance(mic, AudioDevice)
    if mic:
        assert mic.name


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows audio devices only")
def test_default_loopback_returns_something() -> None:
    lb = default_loopback()
    assert lb is None or isinstance(lb, AudioDevice)
    if lb:
        assert lb.is_loopback is True


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows audio devices only")
def test_list_returns_iterable() -> None:
    assert isinstance(list_microphones(), list)
    assert isinstance(list_speakers(), list)


def test_audio_device_dataclass_shape() -> None:
    d = AudioDevice(id="abc", name="My Mic", is_loopback=False)
    assert d.id == "abc"
    assert d.is_loopback is False
```

- [ ] **Step 3: Run to confirm failure**

```powershell
uv run pytest tests/audio/test_devices.py -v
```

- [ ] **Step 4: Implement `audio/devices.py`**

Create `src/teams_transcriber/audio/devices.py`:
```python
"""Light wrapper over `soundcard` for default mic + speaker loopback enumeration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class AudioDevice:
    """An audio input device. Loopbacks are inputs that capture system output."""

    id: str
    name: str
    is_loopback: bool


def _safe_import_soundcard() -> object | None:
    try:
        import soundcard
    except (OSError, ImportError):
        # On non-Windows or where COM is unavailable, soundcard's import raises.
        return None
    return soundcard


def default_microphone() -> AudioDevice | None:
    sc = _safe_import_soundcard()
    if sc is None:
        return None
    try:
        m = sc.default_microphone()  # type: ignore[attr-defined]
    except RuntimeError:
        return None
    return AudioDevice(id=m.id, name=m.name, is_loopback=False)


def default_loopback() -> AudioDevice | None:
    """Return a loopback device for the default speaker (captures system audio)."""
    sc = _safe_import_soundcard()
    if sc is None:
        return None
    try:
        speaker = sc.default_speaker()  # type: ignore[attr-defined]
        # soundcard exposes a loopback-input for each speaker; ask for it explicitly.
        loop = sc.get_microphone(speaker.id, include_loopback=True)  # type: ignore[attr-defined]
    except RuntimeError:
        return None
    return AudioDevice(id=loop.id, name=loop.name, is_loopback=True)


def list_microphones() -> list[AudioDevice]:
    sc = _safe_import_soundcard()
    if sc is None:
        return []
    return [
        AudioDevice(id=m.id, name=m.name, is_loopback=False)
        for m in sc.all_microphones(include_loopback=False)  # type: ignore[attr-defined]
    ]


def list_speakers() -> list[AudioDevice]:
    sc = _safe_import_soundcard()
    if sc is None:
        return []
    # Speakers exposed as loopback inputs.
    return [
        AudioDevice(id=m.id, name=m.name, is_loopback=True)
        for m in sc.all_microphones(include_loopback=True)  # type: ignore[attr-defined]
        if getattr(m, "isloopback", False)
    ]
```

- [ ] **Step 5: Run tests**

```powershell
uv run pytest tests/audio/test_devices.py -v
```
Expected: 4 tests pass (3 are skipped on non-Windows).

- [ ] **Step 6: Lint + types**

```powershell
uv run ruff check src tests
uv run mypy
```

- [ ] **Step 7: Commit**

```powershell
git add src/teams_transcriber/audio tests/audio
git commit -m "feat(audio): add device enumeration helpers"
```

---

## Task 7: Opus writer (PyAV)

**Files:**
- Create: `src/teams_transcriber/audio/opus_writer.py`
- Test: `tests/audio/test_opus_writer.py`

Encodes streaming PCM (2 channels: mic + loopback) into an Ogg-wrapped Opus file. `write_chunk(pcm: np.ndarray)` accepts (frames, 2) `float32` arrays and is safe to call from the capture thread. `close()` flushes the encoder and seals the file.

- [ ] **Step 1: Write failing tests**

Create `tests/audio/test_opus_writer.py`:
```python
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from teams_transcriber.audio.opus_writer import OpusWriter, SAMPLE_RATE


def _make_test_pcm(seconds: float, freq_hz: float, channels: int = 2) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    t = np.linspace(0.0, seconds, n, endpoint=False, dtype=np.float32)
    mono = (0.25 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)
    if channels == 1:
        return mono.reshape(-1, 1)
    # Different sine on the right channel so we can tell channels apart later if needed.
    right = (0.25 * np.sin(2 * np.pi * (freq_hz * 1.5) * t)).astype(np.float32)
    return np.stack([mono, right], axis=1)


def test_write_then_close_produces_readable_opus(tmp_path: Path) -> None:
    path = tmp_path / "out.opus"
    writer = OpusWriter(path, channels=2, bitrate_kbps=24)
    pcm = _make_test_pcm(seconds=1.0, freq_hz=440.0, channels=2)
    writer.write_chunk(pcm)
    writer.close()

    assert path.exists()
    assert path.stat().st_size > 0

    # Read back via PyAV to confirm the file is valid Opus and has the right params.
    import av  # type: ignore[import-not-found]
    container = av.open(str(path))
    try:
        assert len(container.streams.audio) == 1
        stream = container.streams.audio[0]
        assert stream.codec_context.name == "opus"
        assert stream.codec_context.channels == 2
        # Decode all frames and confirm we got at least ~1 second of audio.
        total_samples = 0
        for frame in container.decode(audio=0):
            total_samples += frame.samples
        assert total_samples >= SAMPLE_RATE * 0.9  # tolerate small framing loss
    finally:
        container.close()


def test_multiple_chunks_concatenate(tmp_path: Path) -> None:
    path = tmp_path / "multi.opus"
    writer = OpusWriter(path, channels=2, bitrate_kbps=24)
    for _ in range(5):
        writer.write_chunk(_make_test_pcm(seconds=0.4, freq_hz=440.0, channels=2))
    writer.close()

    import av  # type: ignore[import-not-found]
    container = av.open(str(path))
    try:
        total = sum(f.samples for f in container.decode(audio=0))
        assert total >= int(SAMPLE_RATE * 2.0 * 0.9)  # 5 * 0.4s = 2s, allow 10% loss
    finally:
        container.close()


def test_close_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "ok.opus"
    writer = OpusWriter(path, channels=2, bitrate_kbps=24)
    writer.write_chunk(_make_test_pcm(seconds=0.2, freq_hz=440.0))
    writer.close()
    writer.close()  # must not raise


def test_write_after_close_raises(tmp_path: Path) -> None:
    path = tmp_path / "ok.opus"
    writer = OpusWriter(path, channels=2, bitrate_kbps=24)
    writer.close()
    with pytest.raises(RuntimeError, match="closed"):
        writer.write_chunk(_make_test_pcm(seconds=0.1, freq_hz=440.0))


def test_rejects_wrong_channel_count(tmp_path: Path) -> None:
    path = tmp_path / "ok.opus"
    writer = OpusWriter(path, channels=2, bitrate_kbps=24)
    mono = _make_test_pcm(seconds=0.1, freq_hz=440.0, channels=1)
    with pytest.raises(ValueError, match="channels"):
        writer.write_chunk(mono)
    writer.close()
```

- [ ] **Step 2: Run to confirm failure**

```powershell
uv run pytest tests/audio/test_opus_writer.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `audio/opus_writer.py`**

Create `src/teams_transcriber/audio/opus_writer.py`:
```python
"""Streaming PCM → Ogg/Opus encoder.

Wraps PyAV. The capture thread calls `write_chunk(pcm)` with `(frames, channels)`
float32 arrays at 16 kHz; we resample-up to 48 kHz (Opus's preferred rate) inside
PyAV. `close()` flushes any pending encoder output and finalizes the container.

The writer is intended for use by a single producing thread. Concurrent writers
on the same instance are not supported (and not needed).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE: int = 16_000  # Whisper's native rate; Opus accepts and we don't resample later.
ENCODE_RATE: int = 48_000  # Opus's native rate; PyAV resamples internally.


class OpusWriter:
    def __init__(self, path: Path, *, channels: int = 2, bitrate_kbps: int = 24) -> None:
        import av  # noqa: PLC0415 — imported lazily so tests can patch

        self._path = path
        self._channels = channels
        self._closed = False

        path.parent.mkdir(parents=True, exist_ok=True)

        self._container = av.open(str(path), mode="w", format="ogg")
        self._stream = self._container.add_stream("libopus", rate=ENCODE_RATE)
        self._stream.layout = "stereo" if channels == 2 else "mono"
        # Per-channel bitrate × channels.
        self._stream.bit_rate = int(bitrate_kbps * 1000 * channels)

        # Lazy resampler reference (created on first write).
        self._resampler = None
        self._samples_written = 0

    def write_chunk(self, pcm: np.ndarray) -> None:
        """Encode a (frames, channels) float32 PCM block at SAMPLE_RATE."""
        if self._closed:
            raise RuntimeError("OpusWriter is closed")
        if pcm.ndim != 2 or pcm.shape[1] != self._channels:
            raise ValueError(
                f"expected (frames, {self._channels}) array, got {pcm.shape}"
            )
        if pcm.dtype != np.float32:
            pcm = pcm.astype(np.float32, copy=False)

        import av  # noqa: PLC0415

        # PyAV wants planar float; we have interleaved. Transpose to (channels, frames)
        # then reshape to (1, channels*frames) for the planar-float input format.
        # Simpler: build an AudioFrame from the interleaved data directly.
        layout = "stereo" if self._channels == 2 else "mono"
        frame = av.AudioFrame.from_ndarray(
            pcm.T.copy(order="C"),  # PyAV wants shape (channels, samples)
            format="fltp",            # float planar
            layout=layout,
        )
        frame.sample_rate = SAMPLE_RATE

        if self._resampler is None:
            self._resampler = av.AudioResampler(
                format="fltp", layout=layout, rate=ENCODE_RATE,
            )

        for resampled in self._resampler.resample(frame):
            for packet in self._stream.encode(resampled):
                self._container.mux(packet)
                self._samples_written += resampled.samples

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Flush any frames buffered in the resampler.
        if self._resampler is not None:
            try:
                for resampled in self._resampler.resample(None):
                    for packet in self._stream.encode(resampled):
                        self._container.mux(packet)
            except Exception:
                logger.exception("resampler flush failed")
        # Flush the encoder.
        try:
            for packet in self._stream.encode(None):
                self._container.mux(packet)
        except Exception:
            logger.exception("encoder flush failed")
        try:
            self._container.close()
        except Exception:
            logger.exception("container close failed")
```

- [ ] **Step 4: Run tests**

```powershell
uv run pytest tests/audio/test_opus_writer.py -v
```
Expected: all 5 PASS. If the AudioFrame.from_ndarray call fails with a layout error, double-check the PyAV version (require `av >= 12.0` — earlier versions had a different layout API).

- [ ] **Step 5: Lint + types**

Both clean.

- [ ] **Step 6: Commit**

```powershell
git add src/teams_transcriber/audio/opus_writer.py tests/audio/test_opus_writer.py
git commit -m "feat(audio): add OpusWriter (PyAV-based 2-channel encoder)"
```

---

## Task 8: Recorder

**Files:**
- Create: `src/teams_transcriber/audio/source.py` (`AudioSource` protocol + `FakeAudioSource`)
- Create: `src/teams_transcriber/recorder.py`
- Test: `tests/test_recorder.py`

The `Recorder` orchestrates capture + write + DB. Capture is abstracted behind an `AudioSource` protocol so tests can drive it with a `FakeAudioSource` instead of touching real Windows audio.

- [ ] **Step 1: Write failing test (using FakeAudioSource)**

Create `tests/test_recorder.py`:
```python
from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from teams_transcriber.audio.source import FakeAudioSource
from teams_transcriber.config import Settings
from teams_transcriber.events import (
    EventBus,
    RecordingFinalized,
    RecordingStarted,
)
from teams_transcriber.paths import AppPaths
from teams_transcriber.recorder import Recorder
from teams_transcriber.storage import (
    Recording,
    RecordingRepo,
    RecordingStatus,
    build_database,
)


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    p = AppPaths(root=tmp_path / "TT")
    p.ensure_dirs()
    return p


@pytest.fixture
def db_and_repo(paths: AppPaths):
    db = build_database(paths.db_path)
    db.initialize()
    repo = RecordingRepo(db)
    yield db, repo
    db.close()


def _make_source(seconds: float) -> FakeAudioSource:
    n = int(seconds * 16_000)
    t = np.linspace(0, seconds, n, endpoint=False, dtype=np.float32)
    mic = 0.25 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
    loop = 0.25 * np.sin(2 * np.pi * 880 * t).astype(np.float32)
    return FakeAudioSource(mic_samples=mic, loopback_samples=loop)


def test_recorder_creates_recording_and_finalizes(paths, db_and_repo) -> None:
    db, repo = db_and_repo
    bus = EventBus()
    settings = Settings()

    started: list[RecordingStarted] = []
    finalized: list[RecordingFinalized] = []
    bus.subscribe(RecordingStarted, started.append)
    bus.subscribe(RecordingFinalized, finalized.append)

    source = _make_source(seconds=1.5)
    recorder = Recorder(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source=source,
    )

    rec_id = recorder.start(source_type="manual", detected_title=None)
    # Let the capture thread run.
    source.run_until_exhausted()
    recorder.stop()

    assert len(started) == 1
    assert started[0].recording_id == rec_id
    assert len(finalized) == 1
    assert finalized[0].recording_id == rec_id
    assert finalized[0].duration_ms >= 1000

    row = repo.get(rec_id)
    assert row is not None
    assert row.status == RecordingStatus.TRANSCRIBING
    assert row.duration_ms == finalized[0].duration_ms
    assert row.audio_path is not None
    assert Path(row.audio_path).exists()


def test_recorder_records_to_distinct_paths(paths, db_and_repo) -> None:
    db, _repo = db_and_repo
    bus = EventBus()
    settings = Settings()
    src1 = _make_source(seconds=0.5)
    src2 = _make_source(seconds=0.5)
    r1 = Recorder(bus=bus, db=db, paths=paths, settings=settings, audio_source=src1)
    r1.start(source_type="manual", detected_title=None)
    src1.run_until_exhausted(); r1.stop()
    r2 = Recorder(bus=bus, db=db, paths=paths, settings=settings, audio_source=src2)
    r2.start(source_type="manual", detected_title=None)
    src2.run_until_exhausted(); r2.stop()
    repo = RecordingRepo(db)
    recs = repo.list_recent()
    assert len({r.audio_path for r in recs}) == 2


def test_recorder_cancel_deletes_file_and_row(paths, db_and_repo) -> None:
    db, repo = db_and_repo
    bus = EventBus()
    settings = Settings()
    source = _make_source(seconds=1.0)
    recorder = Recorder(bus=bus, db=db, paths=paths, settings=settings, audio_source=source)
    rec_id = recorder.start(source_type="teams", detected_title="X")
    source.run_until_samples(int(0.3 * 16_000))
    recorder.cancel()

    assert repo.get(rec_id) is None
    # No audio file should remain.
    audio_files = list(paths.audio_dir.glob("*.opus"))
    assert audio_files == []
```

- [ ] **Step 2: Run to confirm failure**

```powershell
uv run pytest tests/test_recorder.py -v
```

- [ ] **Step 3: Implement `audio/source.py`**

Create `src/teams_transcriber/audio/source.py`:
```python
"""Audio source abstraction so tests can drive the Recorder without real devices."""

from __future__ import annotations

import threading
from typing import Protocol

import numpy as np

from teams_transcriber.audio.opus_writer import SAMPLE_RATE


class AudioSource(Protocol):
    """Yields (frames, 2) float32 PCM at SAMPLE_RATE.

    `read_chunk(num_frames)` returns up to `num_frames` rows. Returning a 0-row
    array signals end-of-stream (recorder stops).
    """

    def read_chunk(self, num_frames: int) -> np.ndarray: ...
    def close(self) -> None: ...


class FakeAudioSource:
    """A scripted source that yields pre-supplied mic + loopback mono streams.

    Both arrays must be the same length. Each `read_chunk` call advances a cursor;
    `run_until_exhausted` waits (in a test thread) until everything has been read.
    """

    def __init__(self, mic_samples: np.ndarray, loopback_samples: np.ndarray) -> None:
        assert mic_samples.ndim == 1
        assert loopback_samples.shape == mic_samples.shape
        self._mic = mic_samples.astype(np.float32, copy=False)
        self._loop = loopback_samples.astype(np.float32, copy=False)
        self._cursor = 0
        self._lock = threading.Lock()
        self._exhausted = threading.Event()

    def read_chunk(self, num_frames: int) -> np.ndarray:
        with self._lock:
            start = self._cursor
            end = min(start + num_frames, len(self._mic))
            self._cursor = end
            if start >= end:
                self._exhausted.set()
                return np.empty((0, 2), dtype=np.float32)
            mic_slice = self._mic[start:end]
            loop_slice = self._loop[start:end]
        return np.stack([mic_slice, loop_slice], axis=1)

    def close(self) -> None:
        with self._lock:
            self._cursor = len(self._mic)
            self._exhausted.set()

    # --- test helpers ------------------------------------------------------

    def run_until_exhausted(self, timeout: float = 5.0) -> None:
        self._exhausted.wait(timeout=timeout)

    def run_until_samples(self, n: int, timeout: float = 5.0) -> None:
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._cursor >= n:
                    return
            time.sleep(0.01)
```

- [ ] **Step 4: Implement `recorder.py`**

Create `src/teams_transcriber/recorder.py`:
```python
"""Captures audio from an AudioSource into a 2-channel Opus file, with DB state.

Phase 2 ships post-mode transcription, so the recorder finalizes the file and
sets the recording row to `transcribing`; the Transcriber picks it up from there.

This module abstracts the audio source so tests can use FakeAudioSource. The
default real implementation lives in audio/source_real.py (added in Phase 2.5
when we wire the actual `soundcard.Recorder`).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from teams_transcriber.audio.opus_writer import SAMPLE_RATE, OpusWriter
from teams_transcriber.audio.source import AudioSource
from teams_transcriber.config import Settings
from teams_transcriber.events import (
    EventBus,
    RecordingFinalized,
    RecordingStarted,
)
from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import (
    Database,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
)

logger = logging.getLogger(__name__)

CHUNK_FRAMES: int = SAMPLE_RATE  # 1-second chunks


def _slug(text: str | None) -> str:
    if not text:
        return "manual"
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (cleaned or "manual")[:40]


class Recorder:
    def __init__(
        self,
        *,
        bus: EventBus,
        db: Database,
        paths: AppPaths,
        settings: Settings,
        audio_source: AudioSource,
    ) -> None:
        self._bus = bus
        self._db = db
        self._paths = paths
        self._settings = settings
        self._source = audio_source
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._recording_id: int | None = None
        self._audio_path: Path | None = None
        self._writer: OpusWriter | None = None
        self._started_at: datetime | None = None

    def start(self, *, source_type: str, detected_title: str | None) -> int:
        if self._thread is not None:
            raise RuntimeError("Recorder already running; call stop() first")
        self._started_at = datetime.now(UTC)
        repo = RecordingRepo(self._db)

        slug = _slug(detected_title)
        fname = f"{self._started_at.strftime('%Y-%m-%d_%H%M%S')}_{slug}.opus"
        self._audio_path = self._paths.audio_dir / fname
        self._audio_path.parent.mkdir(parents=True, exist_ok=True)

        rec = repo.create(Recording(
            id=None,
            started_at=self._started_at.isoformat(),
            ended_at=None,
            source=RecordingSource(source_type),
            detected_title=detected_title,
            display_title=None,
            audio_path=str(self._audio_path),
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        ))
        assert rec.id is not None
        self._recording_id = rec.id

        self._writer = OpusWriter(
            self._audio_path,
            channels=2,
            bitrate_kbps=self._settings.audio_bitrate_kbps,
        )

        self._bus.publish(RecordingStarted(
            recording_id=rec.id, audio_path=str(self._audio_path),
        ))

        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="recorder")
        self._thread.start()
        return rec.id

    def stop(self) -> None:
        """Stop recording, finalize the file, transition status to TRANSCRIBING."""
        self._end(cancel=False)

    def cancel(self) -> None:
        """Abort: delete file, remove DB row."""
        self._end(cancel=True)

    # --- internals ---------------------------------------------------------

    def _run(self) -> None:
        assert self._writer is not None
        try:
            while not self._stop.is_set():
                chunk = self._source.read_chunk(CHUNK_FRAMES)
                if chunk.shape[0] == 0:
                    break
                self._writer.write_chunk(chunk)
        except Exception:
            logger.exception("recorder loop failed")
            repo = RecordingRepo(self._db)
            if self._recording_id is not None:
                repo.update_status(
                    self._recording_id, RecordingStatus.RECORDING_FAILED,
                    error_message="recording loop exception",
                )

    def _end(self, *, cancel: bool) -> None:
        if self._thread is None:
            return
        self._stop.set()
        # Wake the source if it's blocking; FakeAudioSource doesn't block but real ones may.
        try:
            self._source.close()
        except Exception:
            logger.exception("source.close() raised")
        self._thread.join(timeout=5.0)
        self._thread = None

        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                logger.exception("writer.close() raised")
            self._writer = None

        repo = RecordingRepo(self._db)
        if self._recording_id is None:
            return

        if cancel:
            if self._audio_path is not None and self._audio_path.exists():
                try:
                    self._audio_path.unlink()
                except OSError:
                    logger.exception("could not delete %s", self._audio_path)
            repo.delete(self._recording_id)
        else:
            ended_at = datetime.now(UTC)
            assert self._started_at is not None
            duration_ms = int((ended_at - self._started_at).total_seconds() * 1000)
            repo.finalize(
                self._recording_id,
                ended_at=ended_at.isoformat(),
                duration_ms=duration_ms,
            )
            repo.update_status(self._recording_id, RecordingStatus.TRANSCRIBING)
            self._bus.publish(RecordingFinalized(
                recording_id=self._recording_id, duration_ms=duration_ms,
            ))

        self._recording_id = None
        self._audio_path = None
        self._started_at = None
```

- [ ] **Step 5: Run tests**

```powershell
uv run pytest tests/test_recorder.py -v
```

- [ ] **Step 6: Full suite + lint + types**

```powershell
uv run pytest -v
uv run ruff check src tests
uv run mypy
```

- [ ] **Step 7: Commit**

```powershell
git add src/teams_transcriber/audio/source.py src/teams_transcriber/recorder.py tests/test_recorder.py
git commit -m "feat(recorder): add Recorder with AudioSource abstraction"
```

---

## Task 9: Transcriber (post-recording)

**Files:**
- Create: `src/teams_transcriber/transcriber.py`
- Test: `tests/test_transcriber.py`

Wraps `faster-whisper.WhisperModel`. Reads the audio file finalized by Recorder, transcribes it, and writes segments to storage. `WhisperModel` is mocked in tests — a real-audio verification is on the manual checklist.

- [ ] **Step 1: Write failing tests with mocked WhisperModel**

Create `tests/test_transcriber.py`:
```python
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from teams_transcriber.config import Settings
from teams_transcriber.events import EventBus, TranscriptionComplete
from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import (
    Channel,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    TranscriptRepo,
    build_database,
)
from teams_transcriber.transcriber import Transcriber


# --- A stand-in for faster_whisper.WhisperModel.transcribe ------------

@dataclass
class _FakeWord:
    word: str
    start: float
    end: float


@dataclass
class _FakeSegment:
    start: float
    end: float
    text: str
    words: list[_FakeWord]


@dataclass
class _FakeInfo:
    language: str = "en"
    duration: float = 5.0


class FakeWhisperModel:
    """Returns canned segments based on the audio file name."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def transcribe(self, path: str, *args: Any, **kwargs: Any
                   ) -> tuple[Iterator[_FakeSegment], _FakeInfo]:
        segs = [
            _FakeSegment(0.0, 1.5, "Hello there", words=[]),
            _FakeSegment(1.5, 3.0, "How are you", words=[]),
            _FakeSegment(3.0, 4.2, "I am well thanks", words=[]),
        ]
        return iter(segs), _FakeInfo()


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    p = AppPaths(root=tmp_path / "TT")
    p.ensure_dirs()
    return p


@pytest.fixture
def db_with_recording(paths: AppPaths):
    db = build_database(paths.db_path)
    db.initialize()
    audio = paths.audio_dir / "fake.opus"
    audio.write_bytes(b"not-real-opus-but-fine-for-mocked-test")
    rec = RecordingRepo(db).create(Recording(
        id=None,
        started_at="2026-05-14T10:00:00+00:00",
        ended_at="2026-05-14T10:00:05+00:00",
        source=RecordingSource.MANUAL,
        detected_title=None,
        display_title=None,
        audio_path=str(audio),
        audio_deleted_at=None,
        duration_ms=5000,
        status=RecordingStatus.TRANSCRIBING,
        error_message=None,
    ))
    yield db, rec.id
    db.close()


def test_transcribe_writes_segments_and_emits_event(db_with_recording, paths) -> None:
    db, rec_id = db_with_recording
    bus = EventBus()
    settings = Settings()
    received: list[TranscriptionComplete] = []
    bus.subscribe(TranscriptionComplete, received.append)

    transcriber = Transcriber(
        bus=bus, db=db, settings=settings,
        model_factory=lambda *_a, **_kw: FakeWhisperModel(),
    )
    transcriber.transcribe(rec_id)

    segments = TranscriptRepo(db).list_for_recording(rec_id)
    assert len(segments) == 3
    assert segments[0].text == "Hello there"
    assert segments[0].start_ms == 0
    assert segments[0].end_ms == 1500
    assert all(s.channel == Channel.OTHERS for s in segments)  # whole-file → "others"

    assert len(received) == 1
    assert received[0].segment_count == 3

    repo = RecordingRepo(db)
    rec = repo.get(rec_id)
    assert rec is not None
    assert rec.status == RecordingStatus.SUMMARIZING


def test_transcribe_marks_failed_when_audio_missing(db_with_recording, paths) -> None:
    db, rec_id = db_with_recording
    # Delete the audio file under the Transcriber's feet.
    rec = RecordingRepo(db).get(rec_id)
    assert rec and rec.audio_path
    Path(rec.audio_path).unlink()

    bus = EventBus()
    transcriber = Transcriber(
        bus=bus, db=db, settings=Settings(),
        model_factory=lambda *_a, **_kw: FakeWhisperModel(),
    )
    transcriber.transcribe(rec_id)

    repo = RecordingRepo(db)
    rec = repo.get(rec_id)
    assert rec is not None
    assert rec.status == RecordingStatus.TRANSCRIPTION_FAILED
    assert rec.error_message is not None


def test_transcribe_marks_failed_on_model_exception(db_with_recording) -> None:
    db, rec_id = db_with_recording

    class BoomModel:
        def __init__(self, *a: Any, **kw: Any) -> None: ...
        def transcribe(self, *a: Any, **kw: Any) -> Any:
            raise RuntimeError("model exploded")

    bus = EventBus()
    transcriber = Transcriber(
        bus=bus, db=db, settings=Settings(),
        model_factory=lambda *_a, **_kw: BoomModel(),
    )
    transcriber.transcribe(rec_id)

    repo = RecordingRepo(db)
    rec = repo.get(rec_id)
    assert rec is not None
    assert rec.status == RecordingStatus.TRANSCRIPTION_FAILED
    assert "model exploded" in (rec.error_message or "")
```

- [ ] **Step 2: Run to confirm failure**

```powershell
uv run pytest tests/test_transcriber.py -v
```

- [ ] **Step 3: Implement `transcriber.py`**

Create `src/teams_transcriber/transcriber.py`:
```python
"""Post-recording transcription using faster-whisper.

This is the simplest possible shape: take a finalized recording, transcribe the whole
file in one pass, write segments. Live per-channel transcription is a Phase 2.5 follow-up.

Segments are emitted with `channel='others'` because we transcribe the mixed file;
proper per-channel labeling will land alongside live mode.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from teams_transcriber.config import Settings
from teams_transcriber.events import EventBus, TranscriptionComplete
from teams_transcriber.storage import (
    Channel,
    Database,
    RecordingRepo,
    RecordingStatus,
    TranscriptRepo,
    TranscriptSegment,
)

logger = logging.getLogger(__name__)

ModelFactory = Callable[..., Any]  # returns a WhisperModel-like object


def _default_model_factory(model_name: str, *, compute_type: str) -> Any:
    from faster_whisper import WhisperModel
    return WhisperModel(model_name, compute_type=compute_type)


class Transcriber:
    def __init__(
        self,
        *,
        bus: EventBus,
        db: Database,
        settings: Settings,
        model_factory: ModelFactory = _default_model_factory,
    ) -> None:
        self._bus = bus
        self._db = db
        self._settings = settings
        self._model_factory = model_factory
        self._model: Any = None

    def transcribe(self, recording_id: int) -> None:
        """Synchronous: transcribe and write segments, then update status + emit event."""
        rec_repo = RecordingRepo(self._db)
        rec = rec_repo.get(recording_id)
        if rec is None:
            logger.error("transcribe(%d): no such recording", recording_id)
            return
        if rec.audio_path is None or not Path(rec.audio_path).exists():
            msg = f"audio file missing: {rec.audio_path}"
            logger.error(msg)
            rec_repo.update_status(
                recording_id, RecordingStatus.TRANSCRIPTION_FAILED, error_message=msg,
            )
            return

        try:
            if self._model is None:
                self._model = self._model_factory(
                    self._settings.transcription_model,
                    compute_type=self._settings.transcription_compute_type,
                )
            segments_iter, _info = self._model.transcribe(
                rec.audio_path,
                language=self._settings.transcription_language,
                vad_filter=True,
            )
            ts_repo = TranscriptRepo(self._db)
            count = 0
            batch: list[TranscriptSegment] = []
            for seg in segments_iter:
                batch.append(TranscriptSegment(
                    id=None,
                    recording_id=recording_id,
                    start_ms=int(seg.start * 1000),
                    end_ms=int(seg.end * 1000),
                    channel=Channel.OTHERS,
                    text=seg.text.strip(),
                ))
                count += 1
                if len(batch) >= 32:
                    ts_repo.append_many(batch)
                    batch.clear()
            if batch:
                ts_repo.append_many(batch)

            rec_repo.update_status(recording_id, RecordingStatus.SUMMARIZING)
            self._bus.publish(TranscriptionComplete(
                recording_id=recording_id, segment_count=count,
            ))
        except Exception as exc:
            logger.exception("transcription failed for recording %d", recording_id)
            rec_repo.update_status(
                recording_id,
                RecordingStatus.TRANSCRIPTION_FAILED,
                error_message=str(exc),
            )
```

- [ ] **Step 4: Run tests**

```powershell
uv run pytest tests/test_transcriber.py -v
```

- [ ] **Step 5: Lint + types + commit**

```powershell
uv run ruff check src tests
uv run mypy
git add src/teams_transcriber/transcriber.py tests/test_transcriber.py
git commit -m "feat(transcriber): add post-mode faster-whisper integration"
```

---

## Task 10: Summarizer

**Files:**
- Create: `src/teams_transcriber/summarizer.py`
- Test: `tests/test_summarizer.py`

Calls Claude with tool-use to force a structured JSON response, persists a `Summary` row, sets the recording's `display_title`, and transitions status to `DONE`. Retries on failure with exponential backoff.

- [ ] **Step 1: Write failing tests**

Create `tests/test_summarizer.py`:
```python
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from teams_transcriber.config import Settings
from teams_transcriber.events import EventBus, SummaryReady
from teams_transcriber.storage import (
    Channel,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    SummaryRepo,
    TranscriptRepo,
    TranscriptSegment,
    build_database,
)
from teams_transcriber.summarizer import Summarizer, SUMMARY_TOOL_NAME


# --- Anthropic SDK fakes -------------------------------------------------

@dataclass
class _FakeToolUseBlock:
    type: str
    name: str
    input: dict[str, Any]


@dataclass
class _FakeResponse:
    content: list[_FakeToolUseBlock]
    stop_reason: str = "tool_use"


class FakeAnthropic:
    """Returns canned tool-use responses. Tracks calls for assertion."""

    def __init__(self, scripted: list[Any]) -> None:
        self._scripted = list(scripted)
        self.calls: list[dict[str, Any]] = []

    class _Messages:
        def __init__(self, parent: "FakeAnthropic") -> None:
            self._parent = parent

        def create(self, **kwargs: Any) -> Any:
            self._parent.calls.append(kwargs)
            response = self._parent._scripted.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    @property
    def messages(self) -> "_Messages":  # noqa: D401
        return self._Messages(self)


def _canned_ok(title: str = "Q2 sync") -> _FakeResponse:
    payload = {
        "title": title,
        "one_line": "Aligned on billing rewrite for July.",
        "summary": "We discussed the billing rewrite.",
        "key_decisions": ["Ship in July"],
        "my_todos": [{"task": "Write API stub", "context": None, "due": None}],
        "action_items_others": [{"who": "Sarah", "task": "Migration doc", "due": None}],
        "follow_ups": ["Revisit pricing"],
        "topics": ["billing"],
    }
    return _FakeResponse(content=[
        _FakeToolUseBlock(type="tool_use", name=SUMMARY_TOOL_NAME, input=payload),
    ])


@pytest.fixture
def setup_recording(tmp_path):
    from teams_transcriber.paths import AppPaths
    paths = AppPaths(root=tmp_path / "TT")
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-14T10:00:00+00:00",
        ended_at="2026-05-14T10:05:00+00:00",
        source=RecordingSource.TEAMS,
        detected_title="X", display_title=None,
        audio_path=None, audio_deleted_at=None,
        duration_ms=300_000, status=RecordingStatus.SUMMARIZING,
        error_message=None,
    ))
    assert rec.id is not None
    TranscriptRepo(db).append_many([
        TranscriptSegment(None, rec.id, 0, 2000, Channel.OTHERS, "Welcome everyone"),
        TranscriptSegment(None, rec.id, 2000, 4500, Channel.ME, "Hi I'll own the stub"),
    ])
    yield db, rec.id
    db.close()


def test_summarize_writes_summary_and_sets_title(setup_recording) -> None:
    db, rec_id = setup_recording
    bus = EventBus()
    received: list[SummaryReady] = []
    bus.subscribe(SummaryReady, received.append)
    settings = Settings()

    client = FakeAnthropic(scripted=[_canned_ok(title="Q2 roadmap sync")])
    s = Summarizer(bus=bus, db=db, settings=settings, client_factory=lambda _key: client)
    s.summarize(rec_id, api_key="sk-test")

    assert len(received) == 1
    assert received[0].recording_id == rec_id

    rec = RecordingRepo(db).get(rec_id)
    assert rec is not None
    assert rec.display_title == "Q2 roadmap sync"
    assert rec.status == RecordingStatus.DONE

    summary = SummaryRepo(db).get(rec_id)
    assert summary is not None
    assert summary.title == "Q2 roadmap sync"
    assert summary.my_todos[0].task == "Write API stub"


def test_summarize_retries_on_transient_error(setup_recording) -> None:
    db, rec_id = setup_recording
    bus = EventBus()
    settings = Settings()

    scripted = [RuntimeError("503 service unavailable"), _canned_ok()]
    client = FakeAnthropic(scripted=scripted)
    s = Summarizer(
        bus=bus, db=db, settings=settings,
        client_factory=lambda _key: client,
        sleep=lambda _s: None,
    )
    s.summarize(rec_id, api_key="sk-test")

    rec = RecordingRepo(db).get(rec_id)
    assert rec is not None
    assert rec.status == RecordingStatus.DONE
    # Made two calls: one failed, one succeeded.
    assert len(client.calls) == 2


def test_summarize_marks_failed_after_max_retries(setup_recording) -> None:
    db, rec_id = setup_recording
    bus = EventBus()
    settings = Settings()

    client = FakeAnthropic(scripted=[RuntimeError("boom")] * 5)
    s = Summarizer(
        bus=bus, db=db, settings=settings,
        client_factory=lambda _key: client,
        sleep=lambda _s: None,
    )
    s.summarize(rec_id, api_key="sk-test")

    rec = RecordingRepo(db).get(rec_id)
    assert rec is not None
    assert rec.status == RecordingStatus.SUMMARY_FAILED
    assert "boom" in (rec.error_message or "")


def test_summarize_skips_when_no_api_key(setup_recording) -> None:
    db, rec_id = setup_recording
    bus = EventBus()
    client = FakeAnthropic(scripted=[_canned_ok()])
    s = Summarizer(
        bus=bus, db=db, settings=Settings(),
        client_factory=lambda _key: client,
    )
    s.summarize(rec_id, api_key=None)

    rec = RecordingRepo(db).get(rec_id)
    assert rec is not None
    assert rec.status == RecordingStatus.SUMMARY_FAILED
    assert "api key" in (rec.error_message or "").lower()


def test_summarize_marks_failed_when_tool_input_is_malformed(setup_recording) -> None:
    db, rec_id = setup_recording
    bad_block = _FakeToolUseBlock(
        type="tool_use", name=SUMMARY_TOOL_NAME,
        input={"title": "x"},  # missing required fields
    )
    bad_response = _FakeResponse(content=[bad_block])
    client = FakeAnthropic(scripted=[bad_response, bad_response, bad_response])
    s = Summarizer(
        bus=EventBus(), db=db, settings=Settings(),
        client_factory=lambda _k: client,
        sleep=lambda _s: None,
    )
    s.summarize(rec_id, api_key="sk-test")
    rec = RecordingRepo(db).get(rec_id)
    assert rec is not None
    assert rec.status == RecordingStatus.SUMMARY_FAILED
```

- [ ] **Step 2: Run to confirm failure**

```powershell
uv run pytest tests/test_summarizer.py -v
```

- [ ] **Step 3: Implement `summarizer.py`**

Create `src/teams_transcriber/summarizer.py`:
```python
"""Anthropic Claude summarization with retry + structured output."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from teams_transcriber.config import Settings
from teams_transcriber.events import EventBus, SummaryReady
from teams_transcriber.storage import (
    ActionItemOther,
    Database,
    RecordingRepo,
    RecordingStatus,
    Summary,
    SummaryRepo,
    TodoItem,
    TodoStateRepo,
    TranscriptRepo,
)

logger = logging.getLogger(__name__)

SUMMARY_TOOL_NAME = "save_meeting_summary"

SYSTEM_PROMPT = """\
You summarize meeting transcripts produced by a Teams Transcriber app.

The transcript has two channels: "me" (the user) and "others" (the remote participants).
Use that distinction to attribute commitments accurately:
- `my_todos` = things the user committed to doing themselves.
- `action_items_others` = things other participants committed to doing.

Always call the save_meeting_summary tool with the full structured summary. Do not
respond with plain text. Be concise. Keep the one_line under 120 characters.
"""

TOOL_SCHEMA: dict[str, Any] = {
    "name": SUMMARY_TOOL_NAME,
    "description": "Save a structured summary of the meeting.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "one_line": {"type": "string"},
            "summary": {"type": "string"},
            "key_decisions": {"type": "array", "items": {"type": "string"}},
            "my_todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string"},
                        "context": {"type": ["string", "null"]},
                        "due": {"type": ["string", "null"]},
                    },
                    "required": ["task"],
                },
            },
            "action_items_others": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "who": {"type": "string"},
                        "task": {"type": "string"},
                        "due": {"type": ["string", "null"]},
                    },
                    "required": ["who", "task"],
                },
            },
            "follow_ups": {"type": "array", "items": {"type": "string"}},
            "topics": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "title", "one_line", "summary",
            "key_decisions", "my_todos", "action_items_others",
            "follow_ups", "topics",
        ],
    },
}


ClientFactory = Callable[[str], Any]


def _default_client_factory(api_key: str) -> Any:
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


class Summarizer:
    def __init__(
        self,
        *,
        bus: EventBus,
        db: Database,
        settings: Settings,
        client_factory: ClientFactory = _default_client_factory,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._bus = bus
        self._db = db
        self._settings = settings
        self._client_factory = client_factory
        self._sleep = sleep

    def summarize(self, recording_id: int, *, api_key: str | None) -> None:
        rec_repo = RecordingRepo(self._db)
        rec = rec_repo.get(recording_id)
        if rec is None:
            logger.error("summarize(%d): no such recording", recording_id)
            return
        if not api_key:
            rec_repo.update_status(
                recording_id, RecordingStatus.SUMMARY_FAILED,
                error_message="Anthropic API key is not configured",
            )
            return

        transcript = self._build_transcript_text(recording_id)
        if not transcript.strip():
            rec_repo.update_status(
                recording_id, RecordingStatus.SUMMARY_FAILED,
                error_message="transcript is empty",
            )
            return

        client = self._client_factory(api_key)
        result = self._call_with_retry(client, transcript, max_attempts=self._settings.ai_max_retries)
        if isinstance(result, Exception):
            rec_repo.update_status(
                recording_id, RecordingStatus.SUMMARY_FAILED, error_message=str(result),
            )
            return

        self._persist(recording_id, result)
        self._bus.publish(SummaryReady(recording_id=recording_id))

    # --- internals -------------------------------------------------------

    def _build_transcript_text(self, recording_id: int) -> str:
        segments = TranscriptRepo(self._db).list_for_recording(recording_id)
        lines = []
        for s in segments:
            ts = f"[{s.start_ms // 1000:>4}s]"
            who = "ME" if s.channel.value == "me" else "OTHER"
            lines.append(f"{ts} {who}: {s.text}")
        return "\n".join(lines)

    def _call_with_retry(
        self, client: Any, transcript: str, max_attempts: int,
    ) -> dict[str, Any] | Exception:
        """Returns the parsed tool payload on success, or the last Exception on failure."""
        addendum = self._settings.ai_custom_prompt_addendum
        sys_prompt = SYSTEM_PROMPT + ("\n\n" + addendum if addendum else "")
        last_err: Exception = RuntimeError("no attempts ran")
        for attempt in range(max_attempts):
            try:
                response = client.messages.create(
                    model=self._settings.ai_model,
                    max_tokens=4096,
                    system=sys_prompt,
                    tools=[TOOL_SCHEMA],
                    tool_choice={"type": "tool", "name": SUMMARY_TOOL_NAME},
                    messages=[{
                        "role": "user",
                        "content": f"Summarize this meeting:\n\n{transcript}",
                    }],
                )
                payload = self._extract_tool_input(response)
                if payload is None:
                    last_err = ValueError("response did not contain expected tool_use block")
                    if attempt < max_attempts - 1:
                        self._sleep(1.5 ** attempt)
                    continue
                return payload
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "summarize attempt %d/%d failed: %r", attempt + 1, max_attempts, exc,
                )
                if attempt < max_attempts - 1:
                    self._sleep(1.5 ** attempt)
        return last_err

    def _extract_tool_input(self, response: Any) -> dict[str, Any] | None:
        blocks = getattr(response, "content", []) or []
        for b in blocks:
            if getattr(b, "type", None) == "tool_use" and getattr(b, "name", None) == SUMMARY_TOOL_NAME:
                payload = getattr(b, "input", None)
                if not isinstance(payload, dict):
                    return None
                required = TOOL_SCHEMA["input_schema"]["required"]
                if not all(k in payload for k in required):
                    return None
                return payload
        return None

    def _persist(self, recording_id: int, payload: dict[str, Any]) -> None:
        rec_repo = RecordingRepo(self._db)
        sum_repo = SummaryRepo(self._db)
        todo_repo = TodoStateRepo(self._db)
        summary = Summary(
            recording_id=recording_id,
            title=str(payload["title"]),
            one_line=str(payload["one_line"]),
            summary=str(payload["summary"]),
            key_decisions=list(payload["key_decisions"]),
            my_todos=[
                TodoItem(
                    task=str(d["task"]),
                    context=d.get("context"),
                    due=d.get("due"),
                )
                for d in payload["my_todos"]
            ],
            action_items_others=[
                ActionItemOther(
                    who=str(d["who"]),
                    task=str(d["task"]),
                    due=d.get("due"),
                )
                for d in payload["action_items_others"]
            ],
            follow_ups=list(payload["follow_ups"]),
            topics=list(payload["topics"]),
            generated_at=datetime.now(UTC).isoformat(),
            model_used=self._settings.ai_model,
        )
        sum_repo.upsert(summary)
        rec_repo.set_display_title(recording_id, summary.title or "Untitled meeting")
        rec_repo.update_status(recording_id, RecordingStatus.DONE)
        # Seed todo_state rows for each my_todo so the UI can toggle them.
        for i, td in enumerate(summary.my_todos):
            todo_repo.upsert(recording_id, todo_index=i, task_text=td.task, done=False)
```

- [ ] **Step 4: Run tests**

```powershell
uv run pytest tests/test_summarizer.py -v
```

- [ ] **Step 5: Lint + types + commit**

```powershell
uv run ruff check src tests
uv run mypy
git add src/teams_transcriber/summarizer.py tests/test_summarizer.py
git commit -m "feat(summarizer): add Claude API integration with retry + structured output"
```

---

## Task 11: Pipeline orchestrator and CLI

**Files:**
- Create: `src/teams_transcriber/pipeline.py`
- Create: `src/teams_transcriber/cli.py`
- Create: `src/teams_transcriber/__main__.py`
- Test: `tests/test_pipeline.py`
- Test: `tests/test_cli.py`

The `Pipeline` wires `MeetingWatcher → Recorder → Transcriber → Summarizer` through the EventBus. The CLI exposes commands: `serve` (run the full background loop), `record-manual` (start a manual recording), `retry-summary <id>`.

- [ ] **Step 1: Implement `pipeline.py`**

Create `src/teams_transcriber/pipeline.py`:
```python
"""Wires EventBus + components into a runnable headless pipeline."""

from __future__ import annotations

import logging
import threading

from teams_transcriber.audio.source import AudioSource
from teams_transcriber.config import Settings
from teams_transcriber.events import (
    EventBus,
    MeetingDetected,
    MeetingEnded,
    RecordingFinalized,
    TranscriptionComplete,
)
from teams_transcriber.meeting_watcher import MeetingWatcher
from teams_transcriber.paths import AppPaths
from teams_transcriber.recorder import Recorder
from teams_transcriber.storage import Database
from teams_transcriber.summarizer import Summarizer
from teams_transcriber.transcriber import Transcriber

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self,
        *,
        bus: EventBus,
        db: Database,
        paths: AppPaths,
        settings: Settings,
        audio_source_factory,  # type: ignore[no-untyped-def]
        meeting_watcher: MeetingWatcher | None = None,
        transcriber: Transcriber | None = None,
        summarizer: Summarizer | None = None,
    ) -> None:
        self._bus = bus
        self._db = db
        self._paths = paths
        self._settings = settings
        self._audio_source_factory = audio_source_factory
        self._recorder: Recorder | None = None
        self._transcriber = transcriber or Transcriber(bus=bus, db=db, settings=settings)
        self._summarizer = summarizer or Summarizer(bus=bus, db=db, settings=settings)
        self._meeting_watcher = meeting_watcher  # may be None for manual-only mode
        self._watcher_thread: threading.Thread | None = None
        self._wire()

    # --- public lifecycle ----------------------------------------------

    def start_manual(self, *, detected_title: str | None = None) -> int:
        return self._start_recorder(source_type="manual", detected_title=detected_title)

    def stop_manual(self) -> None:
        if self._recorder is not None:
            self._recorder.stop()
            self._recorder = None

    def serve(self) -> None:
        if self._meeting_watcher is None:
            raise RuntimeError("Pipeline configured without a MeetingWatcher")
        self._watcher_thread = threading.Thread(
            target=self._meeting_watcher.run_forever, daemon=True, name="watcher",
        )
        self._watcher_thread.start()

    def shutdown(self) -> None:
        if self._meeting_watcher is not None:
            self._meeting_watcher.stop()
        if self._watcher_thread is not None:
            self._watcher_thread.join(timeout=3.0)
        if self._recorder is not None:
            self._recorder.stop()

    # --- wiring --------------------------------------------------------

    def _wire(self) -> None:
        self._bus.subscribe(MeetingDetected, self._on_meeting_detected)
        self._bus.subscribe(MeetingEnded, self._on_meeting_ended)
        self._bus.subscribe(RecordingFinalized, self._on_recording_finalized)
        self._bus.subscribe(TranscriptionComplete, self._on_transcription_complete)

    def _on_meeting_detected(self, evt: MeetingDetected) -> None:
        try:
            self._start_recorder(source_type="teams", detected_title=evt.window_title)
        except Exception:
            logger.exception("failed to start recorder for %r", evt.window_title)

    def _on_meeting_ended(self, _evt: MeetingEnded) -> None:
        if self._recorder is not None:
            self._recorder.stop()
            self._recorder = None

    def _on_recording_finalized(self, evt: RecordingFinalized) -> None:
        # Synchronous: transcribe in-thread. Long-meeting workloads could move to a queue.
        try:
            self._transcriber.transcribe(evt.recording_id)
        except Exception:
            logger.exception("transcription crashed for %d", evt.recording_id)

    def _on_transcription_complete(self, evt: TranscriptionComplete) -> None:
        try:
            self._summarizer.summarize(
                evt.recording_id, api_key=self._settings.anthropic_api_key(),
            )
        except Exception:
            logger.exception("summarization crashed for %d", evt.recording_id)

    def _start_recorder(self, *, source_type: str, detected_title: str | None) -> int:
        if self._recorder is not None:
            logger.warning("recorder already running; ignoring duplicate start")
            return -1
        source = self._audio_source_factory()
        self._recorder = Recorder(
            bus=self._bus, db=self._db, paths=self._paths,
            settings=self._settings, audio_source=source,
        )
        return self._recorder.start(source_type=source_type, detected_title=detected_title)
```

- [ ] **Step 2: Implement `cli.py`**

Create `src/teams_transcriber/cli.py`:
```python
"""argparse-based CLI for the headless pipeline."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from teams_transcriber.audio.devices import default_loopback, default_microphone
from teams_transcriber.config import load_settings
from teams_transcriber.events import EventBus
from teams_transcriber.meeting_watcher import MeetingWatcher, enumerate_windows
from teams_transcriber.paths import AppPaths
from teams_transcriber.pipeline import Pipeline
from teams_transcriber.storage import RecordingRepo, build_database
from teams_transcriber.summarizer import Summarizer
from teams_transcriber.transcriber import Transcriber

logger = logging.getLogger(__name__)


def _build_pipeline(paths: AppPaths, *, with_watcher: bool) -> Pipeline:
    settings = load_settings(paths)
    db = build_database(paths.db_path)
    db.initialize()
    bus = EventBus()

    # Phase 2.5 will provide a real AudioSource factory. For now we fail loud
    # if anyone tries to actually record without one — see test_pipeline.py
    # which constructs Pipeline with a stub factory.
    def _no_audio_factory() -> object:
        raise NotImplementedError(
            "Real audio capture is wired up in Phase 2.5 — see audio/source_real.py"
        )

    watcher = None
    if with_watcher:
        watcher = MeetingWatcher(
            bus=bus,
            current_windows=enumerate_windows,
            title_patterns=settings.detection_title_patterns,
            debounce_polls=settings.detection_debounce_polls,
            poll_interval_ms=settings.detection_poll_interval_ms,
        )

    return Pipeline(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source_factory=_no_audio_factory,
        meeting_watcher=watcher,
        transcriber=Transcriber(bus=bus, db=db, settings=settings),
        summarizer=Summarizer(bus=bus, db=db, settings=settings),
    )


def _cmd_serve(args: argparse.Namespace) -> int:
    paths = AppPaths()
    paths.ensure_dirs()
    pipeline = _build_pipeline(paths, with_watcher=True)
    pipeline.serve()

    stopping = False

    def _handle_signal(_sig: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, _handle_signal)
    print("Watching for Teams meetings. Ctrl-C to stop.", file=sys.stderr)
    while not stopping:
        time.sleep(0.5)
    pipeline.shutdown()
    return 0


def _cmd_retry_summary(args: argparse.Namespace) -> int:
    paths = AppPaths()
    pipeline = _build_pipeline(paths, with_watcher=False)
    api_key = load_settings(paths).anthropic_api_key()
    pipeline._summarizer.summarize(args.recording_id, api_key=api_key)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    paths = AppPaths()
    db = build_database(paths.db_path)
    db.initialize()
    try:
        recs = RecordingRepo(db).list_recent(limit=args.limit)
        for r in recs:
            print(f"#{r.id:>4}  {r.started_at}  [{r.status.value:>14}]  "
                  f"{r.display_title or r.detected_title or '(untitled)'}")
    finally:
        db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    parser = argparse.ArgumentParser(prog="teams-transcriber")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="Run the background pipeline.")
    p_serve.set_defaults(func=_cmd_serve)

    p_retry = sub.add_parser("retry-summary", help="Retry a failed summary by recording id.")
    p_retry.add_argument("recording_id", type=int)
    p_retry.set_defaults(func=_cmd_retry_summary)

    p_list = sub.add_parser("list", help="List recent recordings.")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(func=_cmd_list)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Implement `__main__.py`**

Create `src/teams_transcriber/__main__.py`:
```python
from teams_transcriber.cli import main

raise SystemExit(main())
```

- [ ] **Step 4: Write pipeline integration test**

Create `tests/test_pipeline.py`:
```python
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from teams_transcriber.audio.source import FakeAudioSource
from teams_transcriber.config import Settings
from teams_transcriber.events import EventBus, MeetingDetected, MeetingEnded, SummaryReady
from teams_transcriber.paths import AppPaths
from teams_transcriber.pipeline import Pipeline
from teams_transcriber.storage import (
    RecordingRepo,
    RecordingStatus,
    SummaryRepo,
    TranscriptRepo,
    build_database,
)


def _make_source(seconds: float) -> FakeAudioSource:
    n = int(seconds * 16_000)
    t = np.linspace(0, seconds, n, endpoint=False, dtype=np.float32)
    mic = 0.25 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
    loop = 0.25 * np.sin(2 * np.pi * 880 * t).astype(np.float32)
    return FakeAudioSource(mic_samples=mic, loopback_samples=loop)


def test_end_to_end_with_fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = AppPaths(root=tmp_path / "TT"); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    bus = EventBus()
    settings = Settings()

    # Build a Pipeline without a real MeetingWatcher; we fire events manually.
    sources = [_make_source(seconds=0.5)]

    # Fake Whisper:
    from teams_transcriber.transcriber import Transcriber
    class _FakeSeg:
        def __init__(self, s, e, t): self.start, self.end, self.text = s, e, t
    class _FakeWhisper:
        def __init__(self, *_a, **_kw): pass
        def transcribe(self, *_a, **_kw):
            return iter([_FakeSeg(0.0, 0.5, "Hello from pipeline test")]), object()
    transcriber = Transcriber(
        bus=bus, db=db, settings=settings, model_factory=lambda *_a, **_kw: _FakeWhisper(),
    )

    # Fake Anthropic:
    from dataclasses import dataclass
    from teams_transcriber.summarizer import SUMMARY_TOOL_NAME, Summarizer
    @dataclass
    class _TB: type: str; name: str; input: dict
    @dataclass
    class _R: content: list
    class _FakeClient:
        class _M:
            def create(self, **_kw):
                return _R(content=[_TB(
                    "tool_use", SUMMARY_TOOL_NAME,
                    {
                        "title": "Pipeline test", "one_line": "ok", "summary": "ok",
                        "key_decisions": [], "my_todos": [],
                        "action_items_others": [], "follow_ups": [], "topics": [],
                    },
                )])
        @property
        def messages(self): return self._M()

    summarizer = Summarizer(
        bus=bus, db=db, settings=settings,
        client_factory=lambda _k: _FakeClient(),
    )

    pipeline = Pipeline(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source_factory=lambda: sources.pop(0),
        transcriber=transcriber,
        summarizer=summarizer,
    )

    summaries_ready: list[SummaryReady] = []
    bus.subscribe(SummaryReady, summaries_ready.append)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    # Trigger end-to-end: meeting detected → recorder runs → stop → transcribe → summarize.
    bus.publish(MeetingDetected(window_title="Pipeline test | Microsoft Teams"))
    sources[0:0]  # noqa — keep mypy happy; sources list was consumed by factory
    # Wait for the recorder thread to drain the (very small) fake source.
    time.sleep(0.5)
    bus.publish(MeetingEnded())

    # Wait briefly for synchronous transcribe + summarize to finish.
    # They are dispatched on the publishing thread, so by the time MeetingEnded
    # returns they should be done.
    assert len(summaries_ready) == 1

    recs = RecordingRepo(db).list_recent()
    assert len(recs) == 1
    assert recs[0].status == RecordingStatus.DONE
    summary = SummaryRepo(db).get(recs[0].id)
    assert summary is not None
    assert summary.title == "Pipeline test"

    segments = TranscriptRepo(db).list_for_recording(recs[0].id)
    assert any("Hello from pipeline test" in s.text for s in segments)

    db.close()
```

- [ ] **Step 5: Smoke test the CLI**

Create `tests/test_cli.py`:
```python
from __future__ import annotations

from pathlib import Path

import pytest

from teams_transcriber.cli import main


def test_cli_help_runs(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as ei:
        main(["--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "teams-transcriber" in out


def test_cli_list_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                       capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    rc = main(["list"])
    assert rc == 0
```

- [ ] **Step 6: Run all the new tests**

```powershell
uv run pytest tests/test_pipeline.py tests/test_cli.py -v
```

- [ ] **Step 7: Full suite + lint + types**

```powershell
uv run pytest -v
uv run ruff check src tests
uv run mypy
```

- [ ] **Step 8: Commit**

```powershell
git add src/teams_transcriber/pipeline.py src/teams_transcriber/cli.py src/teams_transcriber/__main__.py tests/test_pipeline.py tests/test_cli.py
git commit -m "feat(pipeline): wire components + add CLI"
```

---

## Task 12: Manual verification checklist (no code)

**Files:**
- Create: `docs/superpowers/checklists/2026-05-14-phase-2-manual-verification.md`

The mocked tests prove the wiring is correct but don't exercise real Whisper, real Claude, or real audio. This checklist captures what must be manually verified before declaring Phase 2 complete.

- [ ] **Step 1: Create the checklist**

Create `docs/superpowers/checklists/2026-05-14-phase-2-manual-verification.md`:
```markdown
# Phase 2 Manual Verification Checklist

Run after Phase 2 lands on main. Each item is a real-world sanity check that
the mocked unit tests cannot catch.

## Environment setup

- [ ] `uv sync --extra dev` completes without errors.
- [ ] `uv run python -c "from faster_whisper import WhisperModel"` runs without errors.
- [ ] CUDA is available: `uv run python -c "import torch; print(torch.cuda.is_available())"` prints `True`.
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
```

- [ ] **Step 2: Commit**

```powershell
git add docs/superpowers/checklists/2026-05-14-phase-2-manual-verification.md
git commit -m "docs: add Phase 2 manual verification checklist"
```

---

## Self-review (executed by the agent before handing off)

### Spec coverage (Phase 2 scope)

| Spec section | Implemented by |
|---|---|
| §4 architecture (EventBus + threaded components) | Task 3 (EventBus, deliberately non-Qt), Task 11 (Pipeline) |
| §5.1 meeting detection (window-title polling, debounce, patterns) | Tasks 4, 5 |
| §5.2 audio capture (2-channel Opus, 16kHz mono per ch) | Tasks 6, 7, 8 |
| §5.3 transcription (post-mode only — live deferred to Phase 2.5) | Task 9 |
| §5.4 stop & finalize (status transitions, status=transcribing on stop) | Task 8 (stop()) |
| §5.5 manual recording | Task 11 (Pipeline.start_manual) |
| §6 AI processing (structured output, retry, model selection, prompt addendum) | Task 10 |
| §7.5 settings dialog data shape (settings.json schema) | Task 2 |
| §7.6 global hotkeys | Phase 3 (UI scope) |
| §8.4 auto-launch | Phase 3 |
| Configuration via `keyring` for API key | Task 2 |

Deferrals explicitly documented in the plan: live dual-channel transcription (Phase 2.5), real `AudioSource` factory wiring (Phase 2.5), system tray and toasts (Phase 3), settings dialog UI (Phase 3), packaging (Phase 4).

### Placeholder scan

- No "TBD" / "TODO" markers.
- All steps contain runnable code or commands with expected output.
- The `_no_audio_factory` in `cli.py` raises `NotImplementedError` with a clear pointer to Phase 2.5 — this is a deliberate stub, not a placeholder.

### Type and name consistency

- `Channel` enum from Phase 1 used consistently in tests and `Transcriber`.
- `RecordingSource` strings (`"teams"` / `"manual"`) match between `Recorder` and `Pipeline`.
- `Summary.title` field (added during Phase 1 review) used in `Summarizer._persist`.
- `EventBus.publish/subscribe` signatures stable across all subscribers.
- `Settings` properties referenced consistently across `Transcriber`, `Summarizer`, `Recorder`.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-14-phase-2-pipeline.md`.

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
