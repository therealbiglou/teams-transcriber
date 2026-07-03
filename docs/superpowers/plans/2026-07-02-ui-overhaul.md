# UI Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every window natively movable/resizable with persistent geometry, add real depth cues (window shadows, active/inactive states, modal scrims), make all text wrap/visible/selectable everywhere, eliminate native-styled widgets and GUI-thread freezes, and fix the ten correctness bugs confirmed by the multi-agent code review (Tasks 16–24: saved audio devices ignored, hotkeys off-thread, todo done-state clobbering, Wrike assignee/done-state/close-loop gaps, recovery dead-end, over-broad cache deletion, dead placeholder, banner width).

**Architecture:** All frameless windows share `FramelessWindowMixin`, which gains a transparent "chrome margin" around the `OuterFrame` — that margin hosts a window-level drop shadow AND is the reliable edge-resize hit zone (mouse events there always reach the top-level window, never a child). Title-bar dragging switches to `startSystemMove()` for native Aero Snap. New small modules: `ui/window_state.py` (QSettings geometry/splitter persistence), `ui/scrim.py` (modal dim overlay), `ui/labels.py` (selectable/wrapping/eliding label helpers shared by all views).

**Tech Stack:** Python 3.11, PySide6 (Qt 6), pytest + pytest-qt (offscreen platform, see `tests/conftest.py`), uv.

## Global Constraints

- Package management is **uv only**: `uv run pytest`, `uv run python -m teams_transcriber`. Never pip/venv/poetry.
- **Never use `QMessageBox`** — themed `ConfirmDialog` only (this plan removes the two existing violations).
- **Never use OS toasts** — `show_in_app_toast` only.
- EventBus stays plain-Python pub/sub; UI work happens on the Qt main thread; worker threads hop back via the 3-arg `QTimer.singleShot(0, qobject, callable)` pattern (see `app.py:1017` comment) or cross-thread Signal emission.
- Theme tokens come from `ui/theme.py` `COLORS`/`RADIUS`/`SPACING` — no new hardcoded palette values in widgets.
- Tests run offscreen (`QT_QPA_PLATFORM=offscreen` set in `tests/conftest.py`); UI tests live in `tests/ui/` and get a `qapp` fixture automatically (autouse in `tests/ui/conftest.py`). `qtbot` comes from pytest-qt.
- Commits are conventional-commits style: `feat(ui): ...`, `fix(ui): ...`, `refactor(ui): ...`, `test(ui): ...`.
- Run the full suite (`uv run pytest`) before each commit; all ~172 existing tests plus new ones must pass. Some existing tests assert old behavior this plan changes — each task lists the test files it must update.
- If you launch the real app to verify, scrub the proxy env first (see CLAUDE.md "HTTPS_PROXY" gotcha).

---

### Task 1: Frameless chrome — shadow margin, active/inactive styling, wider resize band

**Files:**
- Modify: `src/teams_transcriber/ui/frameless.py`
- Modify: `src/teams_transcriber/ui/title_bar.py` (add `set_window_active`)
- Test: `tests/ui/test_frameless.py`

**Interfaces:**
- Produces: `FramelessWindowMixin._init_frameless(outer, *, resizable=True, title_bar=None, shell_layout=None)` — new keyword `shell_layout`: the layout that holds `outer` inside the top-level window. When given, the mixin manages a transparent `CHROME_MARGIN` (18 px) border on it, applies a `QGraphicsDropShadowEffect` to `outer`, collapses margins/shadow when maximized, and restyles on activation change. Also `frameless.CHROME_MARGIN: int = 18` and `TitleBar.set_window_active(active: bool) -> None`.
- Consumes: `theme.COLORS`, `theme.RADIUS`.

Backward compatibility: calling `_init_frameless(outer)` without `shell_layout` must keep the old behavior (6 px resize band, no shadow) so existing tests/hosts keep working until Task 3 migrates them.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ui/test_frameless.py`:

```python
from PySide6.QtWidgets import QVBoxLayout


class _ChromeWin(FramelessWindowMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.resize(400, 300)
        shell = QVBoxLayout(self)
        frame = QFrame()
        shell.addWidget(frame)
        self._init_frameless(frame, shell_layout=shell)


def test_shell_layout_gets_chrome_margins(qapp):
    from teams_transcriber.ui.frameless import CHROME_MARGIN
    w = _ChromeWin()
    m = w._shell_layout.contentsMargins()
    assert (m.left(), m.top(), m.right(), m.bottom()) == (CHROME_MARGIN,) * 4


def test_chrome_margins_collapse_when_maximized(qapp):
    w = _ChromeWin()
    w.showMaximized()
    w._apply_chrome()
    m = w._shell_layout.contentsMargins()
    assert (m.left(), m.top(), m.right(), m.bottom()) == (0, 0, 0, 0)
    w.showNormal()
    w._apply_chrome()
    assert w._shell_layout.contentsMargins().left() > 0


def test_outer_frame_has_window_shadow(qapp):
    from PySide6.QtWidgets import QGraphicsDropShadowEffect
    w = _ChromeWin()
    assert isinstance(w._outer.graphicsEffect(), QGraphicsDropShadowEffect)


def test_resize_band_covers_chrome_margin(qapp):
    from teams_transcriber.ui.frameless import CHROME_MARGIN
    w = _ChromeWin()
    w.resize(400, 300)
    # Anywhere in the transparent margin band is a resize edge.
    assert w._edge_at(QPoint(CHROME_MARGIN - 2, 150)) == Qt.Edge.LeftEdge
    assert w._edge_at(QPoint(200, 150)).value == 0


def test_legacy_init_without_shell_layout_keeps_old_band(qapp):
    w = _Win()  # existing helper: no shell_layout
    assert w._edge_at(QPoint(2, 150)) == Qt.Edge.LeftEdge
    assert w._edge_at(QPoint(10, 150)).value == 0
    assert w._outer.graphicsEffect() is None


def test_titlebar_set_window_active_dims_title(qapp):
    from teams_transcriber.ui.title_bar import TitleBar
    tb = TitleBar(title="X", controls=("close",))
    tb.set_window_active(False)
    assert "color" in tb.title_label.styleSheet()
    tb.set_window_active(True)
    assert tb.title_label.styleSheet() == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_frameless.py -v`
Expected: new tests FAIL (`TypeError: unexpected keyword argument 'shell_layout'`, `AttributeError: set_window_active`); the 4 pre-existing tests still PASS.

- [ ] **Step 3: Implement the chrome in `frameless.py`**

Replace the module docstring's host requirements paragraph and rework the mixin. Full new content of the changed parts:

```python
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QCursor, QMouseEvent
from PySide6.QtWidgets import QGraphicsDropShadowEffect

from teams_transcriber.ui.theme import COLORS, RADIUS

CHROME_MARGIN: int = 18   # transparent band around OuterFrame: shadow + resize zone
_RESIZE_MARGIN: int = 6   # legacy band when no shell layout is managed
_EDGE_INSET: int = 4      # resize band also extends this far inside the frame


class FramelessWindowMixin:
    _outer = None            # type: ignore[var-annotated]
    _resizable: bool = True
    _title_bar = None        # type: ignore[var-annotated]
    _shell_layout = None     # type: ignore[var-annotated]
    _shadow = None           # type: ignore[var-annotated]

    def _init_frameless(
        self, outer, *, resizable: bool = True, title_bar=None, shell_layout=None,
    ) -> None:
        self._outer = outer
        self._resizable = resizable
        self._title_bar = title_bar
        self._shell_layout = shell_layout
        outer.setObjectName("OuterFrame")
        self.setMouseTracking(True)          # type: ignore[attr-defined]
        outer.setMouseTracking(True)
        if shell_layout is not None:
            shadow = QGraphicsDropShadowEffect()
            shadow.setBlurRadius(24)
            shadow.setOffset(0, 2)
            outer.setGraphicsEffect(shadow)
            self._shadow = shadow
        self._apply_chrome()

    def _apply_chrome(self) -> None:
        """Restyle for the current maximized + active state.

        Depth cues: the window shadow and border are stronger when the window
        is active, so the foreground window is visually distinct.
        """
        maximized = self.isMaximized()       # type: ignore[attr-defined]
        active = self.isActiveWindow()       # type: ignore[attr-defined]
        radius = 0 if maximized else RADIUS["window"]
        border = COLORS["border"] if active else COLORS["border_soft"]
        self._outer.setStyleSheet(
            f"#OuterFrame {{ background: {COLORS['bg']}; "
            f"border-radius: {radius}px; border: 1px solid {border}; }}"
        )
        if self._shell_layout is not None:
            m = 0 if maximized else CHROME_MARGIN
            self._shell_layout.setContentsMargins(m, m, m, m)
        if self._shadow is not None:
            self._shadow.setEnabled(not maximized)
            self._shadow.setColor(QColor(0, 0, 0, 90 if active else 40))
        if self._title_bar is not None and hasattr(self._title_bar, "set_window_active"):
            self._title_bar.set_window_active(active)

    # Back-compat shim: toggle_max used to call this.
    def _apply_outer_style(self, *, maximized: bool) -> None:
        del maximized
        self._apply_chrome()

    def toggle_max(self) -> None:
        if self.isMaximized():               # type: ignore[attr-defined]
            self.showNormal()                # type: ignore[attr-defined]
            if self._title_bar is not None:
                self._title_bar.set_maximized(False)
        else:
            self.showMaximized()             # type: ignore[attr-defined]
            if self._title_bar is not None:
                self._title_bar.set_maximized(True)
        self._apply_chrome()

    def changeEvent(self, e: QEvent) -> None:
        # Activation + window-state changes drive the depth styling. Guard on
        # _outer: changeEvent can fire during __init__ before _init_frameless.
        if (
            e.type() in (QEvent.Type.ActivationChange, QEvent.Type.WindowStateChange)
            and self._outer is not None
        ):
            self._apply_chrome()
            if e.type() == QEvent.Type.WindowStateChange and self._title_bar is not None:
                self._title_bar.set_maximized(self.isMaximized())  # type: ignore[attr-defined]
        super().changeEvent(e)                                     # type: ignore[misc]

    def _resize_band(self) -> int:
        if self._shell_layout is not None:
            return CHROME_MARGIN + _EDGE_INSET
        return _RESIZE_MARGIN

    def _edge_at(self, pos: QPoint):
        edges = Qt.Edges()
        if not self._resizable or self.isMaximized():   # type: ignore[attr-defined]
            return edges
        band = self._resize_band()
        rect = self.rect()                               # type: ignore[attr-defined]
        if pos.x() <= band:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= rect.width() - band:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= band:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= rect.height() - band:
            edges |= Qt.Edge.BottomEdge
        return edges
```

Keep `_cursor_for_edges`, `mouseMoveEvent`, `mousePressEvent`, `leaveEvent` exactly as they are.

Add to `title_bar.py` (inside `TitleBar`):

```python
    def set_window_active(self, active: bool) -> None:
        """Dim the title when the window is in the background (depth cue)."""
        from teams_transcriber.ui.theme import COLORS
        self.title_label.setStyleSheet(
            "" if active else f"color: {COLORS['text_tertiary']};"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ui/test_frameless.py -v`
Expected: all PASS. Then `uv run pytest` — full suite green (nothing else uses `shell_layout` yet).

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/frameless.py src/teams_transcriber/ui/title_bar.py tests/ui/test_frameless.py
git commit -m "feat(ui): window shadow margin, active/inactive chrome, wider resize band"
```

---

### Task 2: Native window move (Aero Snap) + drag-to-restore in TitleBar

**Files:**
- Modify: `src/teams_transcriber/ui/title_bar.py:79-94`
- Test: `tests/ui/test_frameless.py`

**Interfaces:**
- Produces: `TitleBar` drag uses `QWindow.startSystemMove()` when available (native snap/tiling), falls back to manual `move()`. Dragging a maximized window restores it first and continues the drag.
- Consumes: host window's `toggle_max()` (from Task 1's mixin) when present.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ui/test_frameless.py`:

```python
def test_titlebar_drag_moves_window_via_fallback(qapp):
    """Offscreen startSystemMove returns False, so the manual fallback must move."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    from teams_transcriber.ui.title_bar import TitleBar

    win = QWidget()
    win.resize(300, 200)
    tb = TitleBar(win, title="T", controls=("close",))
    win.move(100, 100)

    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(50, 10), QPointF(150, 110),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    tb.mousePressEvent(press)
    move = QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(70, 20), QPointF(170, 120),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    tb.mouseMoveEvent(move)
    assert win.pos().x() == 120
    assert win.pos().y() == 110


def test_titlebar_drag_restores_maximized_window(qapp):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    from teams_transcriber.ui.title_bar import TitleBar

    win = _Win()  # FramelessWindowMixin host with toggle_max
    win.resize(400, 300)
    tb = TitleBar(win, title="T", controls=("close",))
    win.showMaximized()
    assert win.isMaximized()
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(50, 10), QPointF(50, 10),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    tb.mousePressEvent(press)
    move = QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(60, 15), QPointF(60, 15),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    tb.mouseMoveEvent(move)
    assert not win.isMaximized()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_frameless.py -v -k titlebar_drag`
Expected: FAIL — old code ignores drags while maximized, and (first test) old code actually passes; verify: the old manual path also moves — so the first test may PASS already. Keep it as a regression guard; the second must FAIL.

- [ ] **Step 3: Implement in `title_bar.py`**

Replace the three mouse handlers:

```python
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_anchor = e.globalPosition().toPoint() - self.window().pos()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag_anchor is None:
            return
        win = self.window()
        if win.isMaximized():
            # Restore first (native title bars do this), keeping the cursor at
            # the same relative x over the title bar so the drag feels anchored.
            rel_x = e.position().x() / max(1, self.width())
            toggle = getattr(win, "toggle_max", None)
            if callable(toggle):
                toggle()
            else:
                win.showNormal()
            gp = e.globalPosition().toPoint()
            win.move(int(gp.x() - win.width() * rel_x), gp.y() - self.height() // 2)
            self._drag_anchor = gp - win.pos()
        handle = win.windowHandle()
        if handle is not None and handle.startSystemMove():
            # The OS owns the move now (enables Windows Aero Snap / Snap
            # Layouts); we get no further move events until release.
            self._drag_anchor = None
            return
        win.move(e.globalPosition().toPoint() - self._drag_anchor)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        del e
        self._drag_anchor = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ui/test_frameless.py -v` then `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/title_bar.py tests/ui/test_frameless.py
git commit -m "feat(ui): native system move for title-bar drag with drag-to-restore"
```

---

### Task 3: Adopt the new chrome in all eight frameless windows

**Files:**
- Modify: `src/teams_transcriber/ui/main_window.py`
- Modify: `src/teams_transcriber/ui/workspace_window.py:66-68,148`
- Modify: `src/teams_transcriber/ui/transcript_window.py:39-41,62`
- Modify: `src/teams_transcriber/ui/settings_dialog.py:84-86,121`
- Modify: `src/teams_transcriber/ui/first_run_wizard.py:89-91,124`
- Modify: `src/teams_transcriber/ui/wrike_sync_planner.py:97-98,136`
- Modify: `src/teams_transcriber/ui/wrike_folder_picker.py:36-38,78`
- Modify: `src/teams_transcriber/ui/update_dialog.py:85-87,117`
- Test: `tests/ui/test_main_window.py`

**Interfaces:**
- Consumes: `_init_frameless(..., shell_layout=...)` from Task 1.
- Produces: every frameless window has `_shell_layout` set (shadow + margin band active).

The change is mechanical and identical for the seven QWidget/QDialog windows: each already builds a top-level layout holding the `OuterFrame` (named `shell`, `outer`, or similar with `setContentsMargins(0,0,0,0)`); pass that layout to `_init_frameless` and delete its explicit zero-margins call (the mixin manages margins now).

- [ ] **Step 1: Write the failing test**

Append to `tests/ui/test_main_window.py`:

```python
def test_main_window_has_chrome_shell(qapp):
    from teams_transcriber.ui.frameless import CHROME_MARGIN
    from teams_transcriber.ui.main_window import MainWindow
    w = MainWindow()
    assert w._shell_layout is not None
    assert w._shell_layout.contentsMargins().left() == CHROME_MARGIN
    assert w._outer.graphicsEffect() is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_main_window.py -v`
Expected: FAIL (`_shell_layout is None`).

- [ ] **Step 3: Migrate the windows**

`main_window.py` — QMainWindow needs a wrapper container because the outer frame is the central widget. In `__init__`, replace `self.setCentralWidget(outer)` and the `_init_frameless` call with:

```python
        shell_host = QWidget()
        shell_host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        shell = QVBoxLayout(shell_host)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.addWidget(outer)
        self.setCentralWidget(shell_host)

        self._init_frameless(outer, resizable=True, title_bar=self.title_bar,
                             shell_layout=shell)
```

The other seven: pass the existing top-level layout. Examples (repeat the same pattern in every file listed above):

`workspace_window.py` — the layout is `outer` (line 66):
```python
        self._init_frameless(self._frame, resizable=True,
                             title_bar=self._title_bar, shell_layout=outer)
```

`settings_dialog.py` — the layout is `shell` (line 84):
```python
        self._init_frameless(frame, resizable=True, title_bar=self._title_bar,
                             shell_layout=shell)
```

`transcript_window.py` → `shell_layout=outer`; `first_run_wizard.py` → `shell_layout=shell`; `wrike_sync_planner.py` → `shell_layout=shell`; `wrike_folder_picker.py` → `shell_layout=shell`; `update_dialog.py` → `shell_layout=shell`.

Note for `shell_host` transparency: without `WA_TranslucentBackground` on the wrapper, the global `QWidget { background: ... }` QSS would paint the margin band opaque. The seven QWidget/QDialog windows already set `WA_TranslucentBackground` on the window itself, but their layout is on the window directly so nothing extra is needed. Verify visually in Step 5.

- [ ] **Step 4: Run tests**

Run: `uv run pytest`
Expected: PASS. Existing per-window tests (`test_workspace_window.py`, `test_settings_dialog.py`, `test_wrike_sync_planner.py`, etc.) construct these windows and will catch signature/layout mistakes.

- [ ] **Step 5: Visual smoke check**

Run (from Bash, with proxy scrubbed):
`env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy uv run python -m teams_transcriber`
Verify: main window shows a soft shadow; margins collapse on maximize; clicking another app dims the title + shadow; edge-resize grabs work on all four sides (in the shadow band). Close the app.

- [ ] **Step 6: Commit**

```bash
git add src/teams_transcriber/ui/*.py tests/ui/test_main_window.py
git commit -m "feat(ui): adopt shadow-margin chrome in all frameless windows"
```

---

### Task 4: Geometry + splitter persistence (`ui/window_state.py`)

**Files:**
- Create: `src/teams_transcriber/ui/window_state.py`
- Modify: `src/teams_transcriber/ui/main_window.py` (closeEvent + restore)
- Modify: `src/teams_transcriber/ui/workspace_window.py` (`__init__`, `closeEvent`)
- Modify: `src/teams_transcriber/ui/transcript_window.py` (add `closeEvent`)
- Modify: `src/teams_transcriber/ui/settings_dialog.py` (restore + `done()` override)
- Test: `tests/ui/test_window_state.py` (new)

**Interfaces:**
- Produces:
  - `restore_window_geometry(window: QWidget, key: str, *, default_size: tuple[int, int] | None = None, settings: QSettings | None = None) -> bool`
  - `save_window_geometry(window: QWidget, key: str, *, settings: QSettings | None = None) -> None`
  - `restore_splitter_state(splitter: QSplitter, key: str, *, settings: QSettings | None = None) -> bool`
  - `save_splitter_state(splitter: QSplitter, key: str, *, settings: QSettings | None = None) -> None`
  - Geometry keys used by the app: `"main"`, `"workspace"`, `"transcript"`, `"settings"`. Splitter keys (Task 5): `"main_body"`, `"main_columns"`.
- Consumes: nothing app-specific. Default store is `QSettings("Teams Transcriber", "Teams Transcriber")` (HKCU registry); tests pass an INI-backed QSettings.

- [ ] **Step 1: Write the failing tests**

Create `tests/ui/test_window_state.py`:

```python
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QSplitter, QWidget

from teams_transcriber.ui.window_state import (
    restore_splitter_state,
    restore_window_geometry,
    save_splitter_state,
    save_window_geometry,
)


def _ini(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "state.ini"), QSettings.Format.IniFormat)


def test_geometry_roundtrip(qapp, tmp_path):
    s = _ini(tmp_path)
    w = QWidget()
    w.resize(555, 333)
    w.move(40, 50)
    save_window_geometry(w, "main", settings=s)
    s.sync()

    w2 = QWidget()
    assert restore_window_geometry(w2, "main", settings=_ini(tmp_path)) is True
    assert w2.size().width() == 555
    assert w2.size().height() == 333


def test_restore_falls_back_to_default_size(qapp, tmp_path):
    w = QWidget()
    ok = restore_window_geometry(
        w, "never-saved", default_size=(640, 480), settings=_ini(tmp_path),
    )
    assert ok is False
    assert (w.width(), w.height()) == (640, 480)


def test_splitter_roundtrip(qapp, tmp_path):
    s = _ini(tmp_path)
    sp = QSplitter()
    sp.addWidget(QWidget())
    sp.addWidget(QWidget())
    sp.resize(1000, 400)
    sp.setSizes([300, 700])
    save_splitter_state(sp, "cols", settings=s)
    s.sync()

    sp2 = QSplitter()
    sp2.addWidget(QWidget())
    sp2.addWidget(QWidget())
    sp2.resize(1000, 400)
    assert restore_splitter_state(sp2, "cols", settings=_ini(tmp_path)) is True
    assert sp2.sizes()[0] < sp2.sizes()[1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_window_state.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `window_state.py`**

```python
"""Persist window geometry and splitter layout across sessions.

Backed by QSettings (HKCU registry on Windows). Every top-level window gets a
stable string key; `saveGeometry`/`restoreGeometry` handle multi-monitor,
DPI, and off-screen validation for us.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import QSplitter, QWidget

_ORG = "Teams Transcriber"
_APP = "Teams Transcriber"


def _store(settings: QSettings | None) -> QSettings:
    return settings if settings is not None else QSettings(_ORG, _APP)


def restore_window_geometry(
    window: QWidget,
    key: str,
    *,
    default_size: tuple[int, int] | None = None,
    settings: QSettings | None = None,
) -> bool:
    data = _store(settings).value(f"geometry/{key}")
    if isinstance(data, QByteArray) and not data.isEmpty() and window.restoreGeometry(data):
        return True
    if default_size is not None:
        window.resize(*default_size)
    return False


def save_window_geometry(
    window: QWidget, key: str, *, settings: QSettings | None = None,
) -> None:
    _store(settings).setValue(f"geometry/{key}", window.saveGeometry())


def restore_splitter_state(
    splitter: QSplitter, key: str, *, settings: QSettings | None = None,
) -> bool:
    data = _store(settings).value(f"splitter/{key}")
    return (
        isinstance(data, QByteArray)
        and not data.isEmpty()
        and splitter.restoreState(data)
    )


def save_splitter_state(
    splitter: QSplitter, key: str, *, settings: QSettings | None = None,
) -> None:
    _store(settings).setValue(f"splitter/{key}", splitter.saveState())
```

- [ ] **Step 4: Wire the four windows**

`main_window.py` — add imports and, at the end of `__init__` (after `_init_frameless`):

```python
        from teams_transcriber.ui.window_state import restore_window_geometry
        restore_window_geometry(self, "main", default_size=(1200, 760))
```

and add:

```python
    def closeEvent(self, ev) -> None:  # noqa: N802
        from teams_transcriber.ui.window_state import save_window_geometry
        save_window_geometry(self, "main")
        super().closeEvent(ev)
```

(Keep the existing `self.resize(1200, 760)` line — it becomes the pre-restore default and is harmless.)

`workspace_window.py` — after `_init_frameless(...)` at the end of `__init__`:

```python
        from teams_transcriber.ui.window_state import restore_window_geometry
        restore_window_geometry(self, "workspace", default_size=(1100, 700))
```

and in the existing `closeEvent`, first line:

```python
        from teams_transcriber.ui.window_state import save_window_geometry
        save_window_geometry(self, "workspace")
```

`transcript_window.py` — same pattern, key `"transcript"`, default `(720, 600)`; add a new `closeEvent`:

```python
    def closeEvent(self, ev) -> None:  # noqa: N802
        from teams_transcriber.ui.window_state import save_window_geometry
        save_window_geometry(self, "transcript")
        super().closeEvent(ev)
```

`settings_dialog.py` — after `_init_frameless(...)`:

```python
        from teams_transcriber.ui.window_state import restore_window_geometry
        restore_window_geometry(self, "settings", default_size=(700, 540))
```

and add (QDialog closes via `done()`, not always `closeEvent`):

```python
    def done(self, result: int) -> None:  # noqa: N802
        from teams_transcriber.ui.window_state import save_window_geometry
        save_window_geometry(self, "settings")
        super().done(result)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest`
Expected: PASS. (The wiring writes to the real registry when windows close in the app; tests that close these windows offscreen will also write registry keys under `HKCU\Software\Teams Transcriber` — acceptable for this personal-use project, matches QSettings norms.)

- [ ] **Step 6: Commit**

```bash
git add src/teams_transcriber/ui/window_state.py src/teams_transcriber/ui/main_window.py src/teams_transcriber/ui/workspace_window.py src/teams_transcriber/ui/transcript_window.py src/teams_transcriber/ui/settings_dialog.py tests/ui/test_window_state.py
git commit -m "feat(ui): persist window geometry across sessions"
```

---

### Task 5: Resizable main-window layout — splitters + flexible sidebar + smaller minimum

**Files:**
- Modify: `src/teams_transcriber/ui/main_window.py` (body → QSplitter, min size)
- Modify: `src/teams_transcriber/ui/sidebar.py:40` (fixed → min/max width)
- Modify: `src/teams_transcriber/ui/app.py:311-344` (`_build_main_content` columns → QSplitter)
- Test: `tests/ui/test_main_window.py`, `tests/ui/test_sidebar.py`

**Interfaces:**
- Consumes: `restore_splitter_state`/`save_splitter_state` (Task 4), keys `"main_body"` (sidebar|content) and `"main_columns"` (history|summary).
- Produces: `MainWindow.body_splitter: QSplitter` attribute; `app._build_columns_splitter(history, summary) -> QSplitter` module-level function.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ui/test_main_window.py`:

```python
def test_sidebar_is_user_resizable_via_splitter(qapp):
    from PySide6.QtWidgets import QSplitter
    from teams_transcriber.ui.main_window import MainWindow
    w = MainWindow()
    assert isinstance(w.body_splitter, QSplitter)
    assert w.body_splitter.widget(0) is w.sidebar
    # Sidebar is no longer fixed-width.
    assert w.sidebar.minimumWidth() < w.sidebar.maximumWidth()


def test_main_window_minimum_allows_half_screen(qapp):
    from teams_transcriber.ui.main_window import MainWindow
    w = MainWindow()
    assert w.minimumWidth() <= 680
    assert w.minimumHeight() <= 460
```

Append to `tests/ui/test_main_window.py` (columns splitter is a pure function in app.py):

```python
def test_columns_splitter_builds_resizable_columns(qapp):
    from PySide6.QtWidgets import QSplitter, QWidget
    from teams_transcriber.ui.app import _build_columns_splitter
    left, right = QWidget(), QWidget()
    sp = _build_columns_splitter(left, right)
    assert isinstance(sp, QSplitter)
    assert sp.widget(0) is left and sp.widget(1) is right
    assert sp.childrenCollapsible() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_main_window.py -v`
Expected: FAIL (`no attribute body_splitter`, `_build_columns_splitter` missing).

- [ ] **Step 3: Implement**

`sidebar.py` — replace `self.setFixedWidth(220)` with:

```python
        self.setMinimumWidth(150)
        self.setMaximumWidth(340)
```

`main_window.py` — replace the `body` QWidget/QHBoxLayout block (lines 46-60) with a splitter:

```python
        from PySide6.QtWidgets import QSplitter
        from teams_transcriber.ui.window_state import (
            restore_splitter_state, save_splitter_state,
        )

        self.body_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.body_splitter.setHandleWidth(6)
        self.body_splitter.setChildrenCollapsible(False)
        self.sidebar = Sidebar()
        self.body_splitter.addWidget(self.sidebar)

        self.content = QWidget()
        self.content.setObjectName("ContentArea")
        self._content_layout = QVBoxLayout(self.content)
        self._content_layout.setContentsMargins(24, 24, 24, 24)
        self._content_layout.setSpacing(16)
        self.body_splitter.addWidget(self.content)
        self.body_splitter.setStretchFactor(0, 0)
        self.body_splitter.setStretchFactor(1, 1)
        self.body_splitter.setSizes([220, 980])

        outer_layout.addWidget(self.body_splitter, 1)
        self.setCentralWidget(shell_host)
        ...
        restore_splitter_state(self.body_splitter, "main_body")
        self.body_splitter.splitterMoved.connect(
            lambda *_: save_splitter_state(self.body_splitter, "main_body")
        )
```

Also change `self.setMinimumSize(900, 540)` → `self.setMinimumSize(640, 440)`.

`app.py` — add module-level function near the other `_`-helpers:

```python
def _build_columns_splitter(history, summary):
    """History | Summary as a user-resizable splitter (was a fixed 50/50 box)."""
    from PySide6.QtWidgets import QSplitter
    sp = QSplitter(Qt.Orientation.Horizontal)
    sp.setHandleWidth(6)
    sp.setChildrenCollapsible(False)
    sp.addWidget(history)
    sp.addWidget(summary)
    sp.setStretchFactor(0, 1)
    sp.setStretchFactor(1, 1)
    return sp
```

In `_build_main_content`, replace the `body` QWidget + `body_layout` block (lines 311-331 minus the signal hookups) with:

```python
        self.history = HistoryList()
        self.history.recording_selected.connect(self._show_summary)
        self.summary = SummaryPane(
            self.db,
            wrike_available=self._wrike_is_configured,
            anthropic_key_getter=self._anthropic_key,
        )
        # ... keep all existing self.summary.*.connect(...) lines unchanged ...
        from teams_transcriber.ui.window_state import (
            restore_splitter_state, save_splitter_state,
        )
        body = _build_columns_splitter(self.history, self.summary)
        restore_splitter_state(body, "main_columns")
        body.splitterMoved.connect(
            lambda *_: save_splitter_state(body, "main_columns")
        )
```

(`body` still goes into `self._content_stack.addWidget(body)` as before — QSplitter is a QWidget.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest`
Expected: PASS. Check `tests/ui/test_sidebar.py` for a fixed-width assertion and update it if present.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/main_window.py src/teams_transcriber/ui/sidebar.py src/teams_transcriber/ui/app.py tests/ui/test_main_window.py tests/ui/test_sidebar.py
git commit -m "feat(ui): user-resizable sidebar and history/summary columns"
```

---

### Task 6: Modal scrim + shadow-safe ConfirmDialog/Toast margins

**Files:**
- Create: `src/teams_transcriber/ui/scrim.py`
- Modify: `src/teams_transcriber/ui/confirm_dialog.py` (margins, use exec_modal in `ask`)
- Modify: `src/teams_transcriber/ui/toast_banner.py:113-117` (margins)
- Modify: `src/teams_transcriber/ui/app.py` (dialog exec sites), `src/teams_transcriber/ui/settings_dialog.py:408` (`_install_update`), `src/teams_transcriber/ui/wrike_sync_planner.py:221` (`_pick_folder`)
- Test: `tests/ui/test_scrim.py` (new)

**Interfaces:**
- Produces: `scrim.exec_modal(dialog: QDialog) -> int` — shows a rounded semi-transparent overlay on the parent window's `OuterFrame` for the duration of `dialog.exec()`. `scrim.Scrim(host: QWidget)` overlay widget.
- Consumes: host's `_outer` attribute (FramelessWindowMixin, Task 1) when present; falls back to the parent window itself.

- [ ] **Step 1: Write the failing tests**

Create `tests/ui/test_scrim.py`:

```python
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QWidget

from teams_transcriber.ui.scrim import Scrim, exec_modal


def test_scrim_covers_host(qapp):
    host = QWidget()
    host.resize(400, 300)
    s = Scrim(host)
    assert s.parent() is host
    assert s.geometry() == host.rect()


def test_exec_modal_returns_dialog_result_and_cleans_up(qapp):
    host = QWidget()
    host.resize(400, 300)
    host.show()
    dlg = QDialog(host)
    QTimer.singleShot(0, dlg.accept)
    result = exec_modal(dlg)
    assert result == QDialog.DialogCode.Accepted


def test_exec_modal_without_parent_is_safe(qapp):
    dlg = QDialog()
    QTimer.singleShot(0, dlg.reject)
    assert exec_modal(dlg) == QDialog.DialogCode.Rejected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_scrim.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `scrim.py`**

```python
"""Dim-the-parent overlay for modal dialogs — a depth cue that makes it obvious
which window is interactive. Use exec_modal(dlg) instead of dlg.exec()."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QWidget

from teams_transcriber.ui.theme import RADIUS


class Scrim(QWidget):
    """Semi-transparent rounded overlay covering a host widget."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet(
            f"background: rgba(31, 41, 55, 0.35); "
            f"border-radius: {RADIUS['window']}px;"
        )
        self.setGeometry(host.rect())
        self.show()
        self.raise_()


def _scrim_host(parent: QWidget | None) -> QWidget | None:
    if parent is None:
        return None
    win = parent.window()
    outer = getattr(win, "_outer", None)   # FramelessWindowMixin hosts
    return outer if outer is not None else win


def exec_modal(dialog: QDialog) -> int:
    """dialog.exec() with a dimming scrim over the parent window."""
    host = _scrim_host(dialog.parentWidget())
    scrim = Scrim(host) if host is not None else None
    try:
        return dialog.exec()
    finally:
        if scrim is not None:
            scrim.deleteLater()
```

- [ ] **Step 4: Adopt at every exec site + fix clipped shadows**

`confirm_dialog.py`:
- In `ask()`, replace `return dlg.exec() == QDialog.DialogCode.Accepted` with:
```python
        from teams_transcriber.ui.scrim import exec_modal
        return exec_modal(dlg) == QDialog.DialogCode.Accepted
```
- The card's 40px-blur shadow is currently clipped to nothing because the outer layout has zero margins. Replace `outer.setContentsMargins(0, 0, 0, 0)` with `outer.setContentsMargins(20, 20, 20, 20)` and `self.setFixedWidth(440)` with `self.setFixedWidth(480)`.

`toast_banner.py`: replace `layout.setContentsMargins(0, 0, 0, 0)` with `layout.setContentsMargins(16, 16, 16, 16)` and `self.setFixedWidth(380)` with `self.setFixedWidth(412)` (same card width, room for the 28px-blur shadow).

`app.py`: add `from teams_transcriber.ui.scrim import exec_modal` and replace `dlg.exec()`/`wizard.exec()` calls at: `_open_settings` (line 503), `_open_settings_audio_tab`/`_transcription_tab`/`_ai_tab` (724/744/760 — these merge in Task 15; update whichever exist at execution time), first-run `wizard.exec()` (243), `_wrike_planner_show` `dlg.exec()` (1089), `_start_update_download` `dlg.exec()` (1206) — each becomes `exec_modal(dlg)` (keep comparisons, e.g. `if exec_modal(dlg) != dlg.DialogCode.Accepted:`).

`settings_dialog.py` `_install_update`: `dlg.exec()` → `exec_modal(dlg)` (import inside method).
`wrike_sync_planner.py` `_pick_folder`: `dlg.exec()` → `exec_modal(dlg)` (import inside method).

- [ ] **Step 5: Run tests**

Run: `uv run pytest`
Expected: PASS. `tests/ui/test_wrike_planner_flow.py` and app-wiring tests may monkeypatch `.exec` — if one fails, adapt it to monkeypatch `teams_transcriber.ui.scrim.exec_modal` instead.

- [ ] **Step 6: Commit**

```bash
git add src/teams_transcriber/ui/scrim.py src/teams_transcriber/ui/confirm_dialog.py src/teams_transcriber/ui/toast_banner.py src/teams_transcriber/ui/app.py src/teams_transcriber/ui/settings_dialog.py src/teams_transcriber/ui/wrike_sync_planner.py tests/ui/test_scrim.py
git commit -m "feat(ui): modal scrim depth cue; unclip dialog and toast shadows"
```

---

### Task 7: Toasts — correct monitor, reflow on dismiss, wrapping title

**Files:**
- Modify: `src/teams_transcriber/ui/toast_banner.py`
- Test: `tests/ui/test_toast_banner.py` (new — no toast test file exists yet)

**Interfaces:**
- Produces: toasts appear on the screen of the active app window (fallback: primary); `_reflow_toasts()` module function repositions the stack whenever a toast is added or dismissed; toast titles word-wrap.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests**

Create `tests/ui/test_toast_banner.py`:

```python
from __future__ import annotations

from teams_transcriber.ui.toast_banner import _ACTIVE_TOASTS, ToastBanner, show_in_app_toast


def _cleanup():
    for t in list(_ACTIVE_TOASTS):
        _ACTIVE_TOASTS.remove(t)
        t.close()


def test_toast_title_wraps(qapp):
    t = ToastBanner(title="A very long toast title that must wrap instead of clipping",
                    body="b", duration_ms=60000)
    try:
        assert t._title_lbl.wordWrap() is True
    finally:
        t.close()


def test_dismiss_reflows_remaining_toasts(qapp):
    _cleanup()
    t1 = show_in_app_toast("one", "body", duration_ms=60000)
    t2 = show_in_app_toast("two", "body", duration_ms=60000)
    assert t1 is not None and t2 is not None
    y_before = t2.y()
    t1._dismiss()
    qapp.processEvents()
    assert t2.y() > y_before   # slid down into the freed slot
    _cleanup()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_toast_banner.py -v`
Expected: FAIL (`_title_lbl` attribute missing; y unchanged after dismiss).

- [ ] **Step 3: Implement**

In `ToastBanner.__init__`: store the title label and make it wrap-safe like the body:

```python
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size: 14px; font-weight: 600;")
        title_lbl.setWordWrap(True)
        title_lbl.setMinimumWidth(0)
        title_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._title_lbl = title_lbl
```

Replace `show_at_bottom_right` and `_dismiss`, and add `_reflow_toasts`:

```python
    def show_at_bottom_right(self) -> None:
        active = QApplication.activeWindow()
        screen = (
            (active.screen() if active is not None else None)
            or self.screen()
            or QGuiApplication.primaryScreen()
        )
        if screen is None:
            self.show()
            return
        self._screen = screen
        self.show()
        _ACTIVE_TOASTS.append(self)
        _reflow_toasts()

    def _dismiss(self) -> None:
        if self._fade is not None:
            return
        with contextlib.suppress(ValueError):
            _ACTIVE_TOASTS.remove(self)
        _reflow_toasts()
        self._fade = QPropertyAnimation(self, b"windowOpacity")
        self._fade.setDuration(180)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.close)
        self._fade.start()
```

Module-level (after `_ACTIVE_TOASTS`):

```python
def _reflow_toasts() -> None:
    """Re-stack all live toasts bottom-up on their own screens, closing gaps."""
    margin = 8
    offsets: dict[object, int] = {}
    for t in _ACTIVE_TOASTS:
        screen = getattr(t, "_screen", None) or QGuiApplication.primaryScreen()
        if screen is None:
            continue
        geom = screen.availableGeometry()
        off = offsets.get(screen, 0)
        t.move(geom.right() - t.width() - margin,
               geom.bottom() - t.height() - margin - off)
        offsets[screen] = off + t.height() + margin
```

Also delete the now-unused `QRect` import if nothing else uses it, and initialize `self._screen = None` in `__init__`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ui/test_toast_banner.py -v` then `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/toast_banner.py tests/ui/test_toast_banner.py
git commit -m "fix(ui): toasts follow the app's monitor, reflow on dismiss, wrap titles"
```

---

### Task 8: Shared text helpers — `ui/labels.py` (selectable, todo row, elided)

**Files:**
- Create: `src/teams_transcriber/ui/labels.py`
- Modify: `src/teams_transcriber/ui/summary_pane.py` (delegate to labels.py, keep aliases)
- Test: `tests/ui/test_labels.py` (new)

**Interfaces:**
- Produces:
  - `make_selectable(label: QLabel) -> QLabel` — mouse+keyboard selection flags.
  - `make_wrapping(label: QLabel) -> QLabel` — wordWrap + minWidth 0 + `(Ignored, Preferred)` size policy (the project's guard #3).
  - `make_todo_row(text: str, *, checked: bool, on_toggle: Callable[[bool], None]) -> QWidget` — checkbox + wrapping selectable label (moved verbatim from `summary_pane._make_todo_row`).
  - `ElidedLabel(QLabel)` — single-line label that elides with "…" to its current width and sets the full text as tooltip; `set_full_text(text: str)`.
- Consumes: nothing app-specific.

- [ ] **Step 1: Write the failing tests**

Create `tests/ui/test_labels.py`:

```python
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLabel

from teams_transcriber.ui.labels import ElidedLabel, make_selectable, make_todo_row, make_wrapping


def test_make_selectable_sets_flags(qapp):
    lbl = make_selectable(QLabel("x"))
    flags = lbl.textInteractionFlags()
    assert flags & Qt.TextInteractionFlag.TextSelectableByMouse
    assert flags & Qt.TextInteractionFlag.TextSelectableByKeyboard


def test_make_wrapping_applies_all_three_guards(qapp):
    from PySide6.QtWidgets import QSizePolicy
    lbl = make_wrapping(QLabel("x"))
    assert lbl.wordWrap() is True
    assert lbl.minimumWidth() == 0
    assert lbl.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored


def test_make_todo_row_wraps_and_selects(qapp):
    row = make_todo_row("task text", checked=True, on_toggle=lambda _c: None)
    cb = row.findChild(QCheckBox)
    lbl = row.findChild(QLabel)
    assert cb.isChecked() is True
    assert cb.text() == ""          # text lives in the wrapping label, not the checkbox
    assert lbl.wordWrap() is True
    assert lbl.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse


def test_todo_row_toggle_fires_callback(qapp):
    calls: list[bool] = []
    row = make_todo_row("t", checked=False, on_toggle=calls.append)
    row.findChild(QCheckBox).setChecked(True)
    assert calls == [True]


def test_elided_label_elides_and_tooltips(qapp):
    lbl = ElidedLabel()
    lbl.setFixedWidth(60)
    long = "A very long recording title that cannot possibly fit in sixty pixels"
    lbl.set_full_text(long)
    assert lbl.toolTip() == long
    assert lbl.text() != long
    assert lbl.text().endswith("…")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_labels.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `labels.py`**

```python
"""Shared label helpers: selection flags, the three-guard wrap pattern, the
checkbox+wrapping-label todo row, and a single-line eliding label.

Every view that shows user text should build it from these helpers so wrap /
select / overflow behavior stays consistent app-wide.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics, QResizeEvent
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSizePolicy, QWidget


def make_selectable(label: QLabel) -> QLabel:
    label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.TextSelectableByKeyboard,
    )
    return label


def make_wrapping(label: QLabel) -> QLabel:
    """The project's three-guard wrap pattern: wordWrap + minWidth 0 + an
    Ignored horizontal policy so a long unbroken token can't push the column
    wider than its container."""
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def make_todo_row(
    text: str,
    *,
    checked: bool,
    on_toggle: Callable[[bool], None],
) -> QWidget:
    """A wrap-friendly todo line: small checkbox + wrapping selectable label.

    QCheckBox's own label does NOT word-wrap — long todo text bleeds past the
    card edge. Splitting into checkbox + sibling wrapping QLabel fixes that;
    the checkbox pins to the top so it aligns with the first wrapped line.
    """
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)

    cb = QCheckBox()
    cb.setChecked(checked)
    cb.toggled.connect(on_toggle)
    h.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)

    label = QLabel(text)
    make_wrapping(label)
    make_selectable(label)
    h.addWidget(label, 1)
    return row


class ElidedLabel(QLabel):
    """Single-line label that elides to its width; full text in the tooltip."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        if text:
            self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._update_elide()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, e: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._update_elide()

    def _update_elide(self) -> None:
        metrics = QFontMetrics(self.font())
        width = max(0, self.width() - 4)
        self.setText(metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, width))
```

Note: `QFontMetrics.elidedText` uses `"…"` (U+2026); the test's `endswith("…")` matches.

`summary_pane.py` — delete the local `_make_selectable` and `_make_todo_row` definitions; import and alias so all existing call sites and any test imports keep working:

```python
from teams_transcriber.ui.labels import make_selectable as _make_selectable
from teams_transcriber.ui.labels import make_todo_row as _make_todo_row
```

(`_build_todos_card` calls `_make_todo_row(text, checked=..., on_toggle=...)` — signature is identical.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ui/test_labels.py tests/ui/test_summary_pane.py -v` then `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/labels.py src/teams_transcriber/ui/summary_pane.py tests/ui/test_labels.py
git commit -m "refactor(ui): shared selectable/wrapping/eliding label helpers"
```

---

### Task 9: Wrap + selectability sweep across views

**Files:**
- Modify: `src/teams_transcriber/ui/history_list.py` (width-pin resizeEvent)
- Modify: `src/teams_transcriber/ui/meeting_card.py` (size policies, selectable error)
- Modify: `src/teams_transcriber/ui/master_todo_view.py:115-121` (todo rows)
- Modify: `src/teams_transcriber/ui/active_recording_banner.py:53-57` (elided title)
- Modify: `src/teams_transcriber/ui/wrike_sync_planner.py:151` (full-text tooltip)
- Modify: `src/teams_transcriber/ui/settings_dialog.py` (selectable status labels)
- Modify: `src/teams_transcriber/ui/theme.py:75-77` (constant-width selected border)
- Test: `tests/ui/test_history_list.py`, `tests/ui/test_meeting_card.py`, `tests/ui/test_master_todo_view.py`, `tests/ui/test_active_recording_banner.py`, `tests/ui/test_wrike_sync_planner.py`, `tests/ui/test_theme.py`

**Interfaces:**
- Consumes: `labels.make_wrapping`, `labels.make_selectable`, `labels.make_todo_row`, `labels.ElidedLabel` (Task 8).
- Produces: no new API; behavior only.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ui/test_history_list.py`:

```python
def test_history_list_pins_container_to_viewport(qapp):
    from teams_transcriber.ui.history_list import HistoryList
    hl = HistoryList()
    hl.resize(300, 400)
    hl.resizeEvent(None) if False else None  # real resize below
    hl.show()
    qapp.processEvents()
    assert hl._container.maximumWidth() <= hl.viewport().width()
```

Append to `tests/ui/test_meeting_card.py` (reuse that file's existing recording fixture/helpers for constructing a `MeetingCard`; the error-state recording needs `status=RecordingStatus.SUMMARY_FAILED, error_message="boom"`):

```python
def test_card_error_text_is_selectable_and_title_shrinkable(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QSizePolicy
    card = _make_card_with_error()  # build per this file's existing pattern
    labels = card.findChildren(QLabel)
    error = next(lbl for lbl in labels if lbl.text() == "boom")
    assert error.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    title = labels[0]
    assert title.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored
```

Append to `tests/ui/test_master_todo_view.py` (reuse its db fixture that seeds a summary with todos):

```python
def test_master_todos_use_wrapping_rows(qapp, ...):  # match existing fixture names
    from PySide6.QtWidgets import QCheckBox, QLabel
    view = ...  # construct + reload per this file's existing pattern
    cbs = view._container.findChildren(QCheckBox)
    assert cbs and all(cb.text() == "" for cb in cbs)  # text lives in labels now
```

Append to `tests/ui/test_active_recording_banner.py`:

```python
def test_banner_elides_long_titles(qapp):
    from teams_transcriber.ui.active_recording_banner import ActiveRecordingBanner
    b = ActiveRecordingBanner()
    b.resize(300, 48)
    b.show_recording(1, "An enormously long meeting title that cannot fit in the banner at all")
    assert b._title_label.toolTip().startswith("Recording: An enormously")
    assert b._title_label.text().endswith("…")
    b.hide_banner()
```

Append to `tests/ui/test_wrike_sync_planner.py` (reuse its existing items/folders fixtures):

```python
def test_planner_preview_tooltip_has_full_text(qapp, ...):
    ...  # build planner per this file's existing pattern, with one long item text
    preview = ...  # the row's preview QLabel (findChildren(QLabel), pick wrapped one)
    assert preview.toolTip() == long_item_text
```

Append to `tests/ui/test_theme.py`:

```python
def test_selected_card_border_keeps_width_constant() -> None:
    qss = app_stylesheet()
    # Selection must not change border width (1px→2px causes layout jiggle).
    assert 'QFrame[card="true"][selected="true"]' in qss
    assert "2px solid" not in qss.split('selected="true"')[1].split("}")[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_history_list.py tests/ui/test_meeting_card.py tests/ui/test_master_todo_view.py tests/ui/test_active_recording_banner.py tests/ui/test_theme.py -v`
Expected: new tests FAIL.

- [ ] **Step 3: Implement**

`history_list.py` — add (plus `QResizeEvent` import):

```python
    def resizeEvent(self, e) -> None:  # noqa: N802
        # Guard #2: pin the container to the viewport so cards must wrap
        # instead of overflowing past the (hidden) horizontal scrollbar.
        super().resizeEvent(e)
        vp = self.viewport()
        if vp is not None:
            self._container.setMaximumWidth(vp.width())
```

`meeting_card.py`:
- title: replace `title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)` with `make_wrapping(title)` (import `from teams_transcriber.ui.labels import make_selectable, make_wrapping`; keep the styleSheet call).
- `one_line` label: after `ol.setWordWrap(True)` add `make_wrapping(ol)`.
- error label: keep existing styling, add `make_selectable(err)` (error text is what users copy into bug reports; title/one-line stay non-selectable so the card's click-to-select keeps working).

`master_todo_view.py` — replace the checkbox loop in `_build_group`:

```python
        from teams_transcriber.ui.labels import make_selectable, make_todo_row
        make_selectable(title)
        for i, td in enumerate(summary.my_todos):
            text = td.task + (f"  (due {td.due})" if td.due else "")

            def _handler(rid=rec.id, idx=i, task=td.task):
                return lambda checked: self._toggle(rid, idx, task, checked)

            v.addWidget(make_todo_row(text, checked=bool(states.get(i)), on_toggle=_handler()))
```

`active_recording_banner.py` — replace the `_title_label` QLabel block with:

```python
        from teams_transcriber.ui.labels import ElidedLabel
        self._title_label = ElidedLabel()
        self._title_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        layout.addWidget(self._title_label, 1, Qt.AlignmentFlag.AlignVCenter)
```

and in `show_recording` / `set_processing`, replace `self._title_label.setText(...)` with `self._title_label.set_full_text(...)`; in `set_processing` read the current text via `self._title_label.full_text()` instead of `.text()` (the elided text would corrupt the prefix swap):

```python
        title_text = self._title_label.full_text()
        if title_text.startswith("Recording:"):
            self._title_label.set_full_text("Processing:" + title_text[len("Recording:"):])
```

`wrike_sync_planner.py` `_build_row` — after creating `preview`, add `preview.setToolTip(item.text)`.

`settings_dialog.py` — make the three status labels selectable (add `from teams_transcriber.ui.labels import make_selectable` at top):
- `self.wrike_status_label = make_selectable(QLabel(""))`
- `self._update_check_result = make_selectable(QLabel(""))`
- `self._last_check_label = make_selectable(QLabel(f"Last update check: {last_check}"))`

`theme.py` — replace the selected-card rule:

```python
    QFrame[card="true"][selected="true"] {{
        border: 1px solid {c['accent']};
    }}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest`
Expected: PASS. If `test_meeting_card.py`/`test_master_todo_view.py` have assertions on the old checkbox text or size policies, update them to the new structure.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/history_list.py src/teams_transcriber/ui/meeting_card.py src/teams_transcriber/ui/master_todo_view.py src/teams_transcriber/ui/active_recording_banner.py src/teams_transcriber/ui/wrike_sync_planner.py src/teams_transcriber/ui/settings_dialog.py src/teams_transcriber/ui/theme.py tests/ui/
git commit -m "fix(ui): wrap/selectability sweep — history, todos, banner, planner, settings"
```

---

### Task 10: Chat bubbles — auto-sizing, selectable, no nested scrollbars

**Files:**
- Modify: `src/teams_transcriber/ui/chat_card.py:146-174`
- Test: `tests/ui/test_chat_card.py`

**Interfaces:**
- Produces: `ChatCard._add_bubble` builds wrapping selectable `QLabel`s (auto-height) instead of fixed-size `QTextEdit`s.
- Consumes: `labels.make_wrapping`, `labels.make_selectable`.

- [ ] **Step 1: Write the failing test**

Append to `tests/ui/test_chat_card.py`:

```python
def test_bubbles_are_autosizing_selectable_labels(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QTextEdit
    from teams_transcriber.ui.chat_card import ChatCard
    card = ChatCard(1, [], enabled=True)
    card.append_user_message("hello")
    card.append_assistant_message("world " * 200)
    bubbles = [w for w in card._message_container.findChildren(QLabel)
               if w.wordWrap()]
    assert len(bubbles) >= 2
    assert all(
        b.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
        for b in bubbles
    )
    # No nested-scroll QTextEdit bubbles remain in the message list.
    assert card._message_container.findChildren(QTextEdit) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_chat_card.py -v`
Expected: new test FAILs (bubbles are QTextEdits).

- [ ] **Step 3: Implement**

Replace `_add_bubble`:

```python
    def _add_bubble(self, role: str, content: str) -> None:
        from PySide6.QtCore import QTimer
        from teams_transcriber.ui.labels import make_selectable, make_wrapping

        if self._placeholder is not None:
            self._placeholder.setVisible(False)
        bubble = QLabel(content)
        make_wrapping(bubble)
        make_selectable(bubble)
        if role == "user":
            bubble.setStyleSheet(
                "background: #10B981; color: white; border-radius: 10px; padding: 8px;"
            )
        elif role == "error":
            bubble.setStyleSheet(
                "background: #FEE2E2; color: #991B1B; border-radius: 10px; "
                "padding: 8px; border: 1px solid #FCA5A5;"
            )
        else:
            bubble.setStyleSheet(
                "background: #FFFFFF; color: #111827; border-radius: 10px; "
                "padding: 8px; border: 1px solid #E5E7EB;"
            )
        self._msg_layout.addWidget(bubble)

        # Scroll after the layout pass, not before it — bar.maximum() is stale
        # until the new bubble has a height.
        bar = self._scroll.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))
```

Drop the now-unused `QTextEdit` import from the widgets import list **only if** `_ChatInput` no longer needs it (it does need it — keep the import).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ui/test_chat_card.py -v` then `uv run pytest`
Expected: PASS. Update any existing bubble-shape assertions in `test_chat_card.py`.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/chat_card.py tests/ui/test_chat_card.py
git commit -m "fix(ui): chat bubbles auto-size and select; no nested scrollbars"
```

---

### Task 11: Theme QSS coverage for every stock widget the app uses

**Files:**
- Modify: `src/teams_transcriber/ui/theme.py` (append rules inside `app_stylesheet`)
- Test: `tests/ui/test_theme.py`

**Interfaces:**
- Produces: themed QSS for `QComboBox`, `QTabWidget/QTabBar`, `QSpinBox`, `QListWidget`, `QGroupBox`, `QProgressBar`, `QCheckBox/QRadioButton` indicators, `QScrollBar:horizontal`, `QToolTip`.

- [ ] **Step 1: Write the failing test**

Append to `tests/ui/test_theme.py`:

```python
def test_stylesheet_covers_all_stock_widgets_in_use() -> None:
    qss = app_stylesheet()
    for selector in (
        "QComboBox", "QTabBar::tab", "QSpinBox", "QListWidget",
        "QGroupBox", "QProgressBar::chunk", "QCheckBox::indicator",
        "QScrollBar:horizontal", "QToolTip",
    ):
        assert selector in qss, f"missing QSS for {selector}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_theme.py -v`
Expected: FAIL (`missing QSS for QComboBox`).

- [ ] **Step 3: Append the rules**

Add before the closing `"""` of `app_stylesheet` (inside the f-string, using the existing `c`/`r`/`s` shorthands):

```python
    QComboBox {{
        background: {c['card']}; color: {c['text_primary']};
        border: 1px solid {c['border']}; border-radius: {r['input']}px;
        padding: {s['xs']}px {s['md']}px; min-height: 24px;
    }}
    QComboBox:focus {{ border: 1px solid {c['accent']}; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{
        background: {c['card']}; color: {c['text_primary']};
        border: 1px solid {c['border']}; border-radius: {r['button']}px;
        selection-background-color: {c['hover']};
        selection-color: {c['text_primary']};
        outline: none;
    }}

    QTabWidget::pane {{
        border: 1px solid {c['border_soft']};
        border-radius: {r['card']}px;
        top: -1px;
    }}
    QTabBar::tab {{
        background: transparent; color: {c['text_secondary']};
        padding: {s['sm']}px {s['lg']}px; border: none;
        border-top-left-radius: {r['button']}px;
        border-top-right-radius: {r['button']}px;
    }}
    QTabBar::tab:selected {{
        background: {c['card']}; color: {c['text_primary']}; font-weight: 600;
    }}
    QTabBar::tab:hover:!selected {{ background: {c['hover']}; }}

    QSpinBox {{
        background: {c['card']}; color: {c['text_primary']};
        border: 1px solid {c['border']}; border-radius: {r['input']}px;
        padding: {s['xs']}px {s['sm']}px;
    }}
    QSpinBox:focus {{ border: 1px solid {c['accent']}; }}

    QListWidget {{
        background: {c['card']};
        border: 1px solid {c['border']}; border-radius: {r['input']}px;
        padding: {s['xs']}px;
        outline: none;
    }}
    QListWidget::item {{
        padding: {s['xs']}px {s['sm']}px; border-radius: {r['button']}px;
        color: {c['text_primary']};
    }}
    QListWidget::item:selected {{
        background: {c['accent_soft']}; color: {c['text_primary']};
    }}
    QListWidget::item:hover:!selected {{ background: {c['hover']}; }}

    QGroupBox {{
        border: 1px solid {c['border_soft']}; border-radius: {r['card']}px;
        margin-top: {s['md']}px; font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; left: {s['md']}px; padding: 0 {s['xs']}px;
        color: {c['text_primary']};
    }}

    QProgressBar {{
        background: {c['card_alt']};
        border: 1px solid {c['border_soft']}; border-radius: {r['button']}px;
        min-height: 14px; text-align: center; font-size: 11px;
        color: {c['text_secondary']};
    }}
    QProgressBar::chunk {{
        background: {c['accent']}; border-radius: {r['button']}px;
    }}

    QCheckBox::indicator, QRadioButton::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {c['border']}; border-radius: 4px;
        background: {c['card']};
    }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
        border-color: {c['accent']};
    }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {c['accent']}; border-color: {c['accent']};
    }}

    QScrollBar:horizontal {{
        background: transparent; height: 10px; margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {c['border']}; border-radius: 5px; min-width: 30px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {c['text_tertiary']};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    QToolTip {{
        background: {c['text_primary']}; color: #FFFFFF;
        border: none; padding: {s['xs']}px {s['sm']}px;
        border-radius: 4px; font-size: 12px;
    }}
```

- [ ] **Step 4: Run tests + visual check**

Run: `uv run pytest tests/ui/test_theme.py -v` then `uv run pytest`
Launch the app (proxy-scrubbed) and open Settings — tabs, combos, spinbox, list, checkboxes, progress bar should all look themed. Close.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/theme.py tests/ui/test_theme.py
git commit -m "feat(ui): theme QSS for combo/tabs/spin/list/group/progress/check/hscroll/tooltip"
```

---

### Task 12: `ConfirmDialog.info` + remove the QMessageBox violations

**Files:**
- Modify: `src/teams_transcriber/ui/confirm_dialog.py`
- Modify: `src/teams_transcriber/ui/settings_dialog.py:442-508` (`_redownload_model`)
- Test: `tests/ui/test_confirm_dialog.py` (new), `tests/ui/test_settings_dialog.py`

**Interfaces:**
- Produces: `ConfirmDialog(cancel_label: str | None = "Cancel", ...)` — `None` hides the cancel button; `ConfirmDialog.info(parent, *, title, body, ok_label="OK") -> None` classmethod.
- Consumes: `scrim.exec_modal` (Task 6).

- [ ] **Step 1: Write the failing tests**

Create `tests/ui/test_confirm_dialog.py`:

```python
from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from teams_transcriber.ui.confirm_dialog import ConfirmDialog


def test_cancel_label_none_hides_cancel_button(qapp):
    dlg = ConfirmDialog(title="T", body="B", confirm_label="OK", cancel_label=None)
    texts = [b.text() for b in dlg.findChildren(QPushButton)]
    assert texts == ["OK"]


def test_default_still_has_both_buttons(qapp):
    dlg = ConfirmDialog(title="T", body="B")
    texts = [b.text() for b in dlg.findChildren(QPushButton)]
    assert texts == ["Cancel", "OK"]


def test_settings_module_does_not_use_qmessagebox():
    import inspect
    import teams_transcriber.ui.settings_dialog as sd
    assert "QMessageBox" not in inspect.getsource(sd)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_confirm_dialog.py -v`
Expected: FAIL (constructor rejects `cancel_label=None`? No — it accepts a str; the first test fails because a Cancel button exists; third fails on the source scan).

- [ ] **Step 3: Implement**

`confirm_dialog.py`:
- change signature: `cancel_label: str | None = "Cancel"` (both `__init__` and `ask`).
- wrap the cancel button creation:

```python
        if cancel_label is not None:
            cancel_btn = QPushButton(cancel_label)
            cancel_btn.setProperty("role", "secondary")
            cancel_btn.clicked.connect(self.reject)
            btn_row.addWidget(cancel_btn)
```

- add the classmethod:

```python
    @classmethod
    def info(
        cls,
        parent: QWidget | None,
        *,
        title: str,
        body: str,
        ok_label: str = "OK",
    ) -> None:
        """Themed replacement for QMessageBox.information — single OK button."""
        from teams_transcriber.ui.scrim import exec_modal
        dlg = cls(
            title=title, body=body,
            confirm_label=ok_label, cancel_label=None, parent=parent,
        )
        exec_modal(dlg)
```

`settings_dialog.py` `_redownload_model` — delete `from PySide6.QtWidgets import QMessageBox` and replace both `QMessageBox.information(...)` calls:

```python
            ConfirmDialog.info(
                self, title="Model cache not found",
                body=(
                    f"Could not find a cached Whisper model under {cache_root}.\n"
                    "The model will download fresh on next transcription."
                ),
            )
```

```python
            ConfirmDialog.info(
                self, title="Done",
                body=(
                    "Model cache cleared. The model will re-download on the "
                    "next transcription. Use Retry on any failed recordings "
                    "to trigger it now."
                ),
            )
```

(`ConfirmDialog` is already imported inside the method; move the import to the top of the method: `from teams_transcriber.ui.confirm_dialog import ConfirmDialog`.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ui/test_confirm_dialog.py tests/ui/test_settings_dialog.py -v` then `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/confirm_dialog.py src/teams_transcriber/ui/settings_dialog.py tests/ui/test_confirm_dialog.py
git commit -m "fix(ui): themed info dialog replaces QMessageBox in settings"
```

---

### Task 13: First-run wizard — downloads off the GUI thread

**Files:**
- Modify: `src/teams_transcriber/ui/first_run_wizard.py`
- Test: `tests/ui/test_first_run_wizard.py`

**Interfaces:**
- Produces: `_DownloadRunner(QObject)` with signals `progress(int)`, `status(str)`, `finished(str)` (`""` = success) and `start()` spawning a daemon thread. `FirstRunWizard._set_nav_enabled(enabled: bool)`. The injected `model_downloader` contract is unchanged (`Callable[[Callable[[int], None]], None]`) but now runs on a worker thread; its progress callback emits a signal (thread-safe).
- Consumes: existing `gpu_runtime.download_runtime`, `gpu_runtime.is_runtime_installed`.

- [ ] **Step 1: Update/extend the tests**

In `tests/ui/test_first_run_wizard.py`, the two GPU tests currently assert synchronously after `_next()`. Update `test_wizard_kicks_off_gpu_runtime_download_when_not_installed` to wait:

```python
    wiz._next()  # welcome → setup
    wiz._next()  # setup → gpu runtime → auto-kick download (now async)
    qtbot.waitUntil(lambda: download_calls == ["invoked"], timeout=3000)
    qtbot.waitUntil(lambda: wiz.gpu_progress_bar.value() == 100, timeout=3000)
```

Add a new test:

```python
def test_nav_disabled_while_download_runs(qapp, qtbot, paths, monkeypatch) -> None:
    import threading
    from teams_transcriber.config import load_settings
    from teams_transcriber.ui.first_run_wizard import FirstRunWizard

    gate = threading.Event()

    def slow_download(runtime_base, progress_callback=None):
        gate.wait(timeout=5)

    monkeypatch.setattr(
        "teams_transcriber.runtime.gpu_runtime.is_runtime_installed",
        lambda _base: False,
    )
    monkeypatch.setattr(
        "teams_transcriber.runtime.gpu_runtime.download_runtime", slow_download,
    )
    wiz = FirstRunWizard(
        settings=load_settings(paths), paths=paths,
        model_downloader=lambda progress: progress(100),
    )
    wiz._next(); wiz._next()   # land on GPU page → download starts
    assert not wiz._next_btn.isEnabled()
    gate.set()
    qtbot.waitUntil(lambda: wiz._next_btn.isEnabled(), timeout=3000)
```

- [ ] **Step 2: Run tests to verify the new one fails**

Run: `uv run pytest tests/ui/test_first_run_wizard.py -v`
Expected: `test_nav_disabled_while_download_runs` FAILs (nav never disabled — and worse, the GUI thread blocks on `gate.wait`, so the test may time out: that timeout IS the bug being fixed).

- [ ] **Step 3: Implement**

Add to `first_run_wizard.py` (top-level, after imports):

```python
import threading

from PySide6.QtCore import QObject


class _DownloadRunner(QObject):
    """Runs a blocking download on a daemon thread; Qt auto-queues the signal
    emissions back to the GUI thread, so slots can touch widgets safely."""

    progress = Signal(int)
    status = Signal(str)
    finished = Signal(str)   # error message; "" = success

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn        # Callable[[_DownloadRunner], None]

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self._fn(self)
            self.finished.emit("")
        except Exception as exc:
            logger.exception("wizard download failed")
            self.finished.emit(str(exc))
```

Replace `_kick_model_download`, `_kick_gpu_runtime_download`, `_download_gpu_runtime` and add helpers:

```python
    def _set_nav_enabled(self, enabled: bool) -> None:
        self._next_btn.setEnabled(enabled)
        self._back_btn.setEnabled(enabled and self._stack.currentIndex() > 0)

    def _kick_model_download(self) -> None:
        self.progress_label.setText("Downloading model...")
        self._set_nav_enabled(False)
        runner = _DownloadRunner(lambda r: self._model_downloader(r.progress.emit))
        runner.progress.connect(self.progress_bar.setValue)
        runner.finished.connect(self._on_model_download_finished)
        self._model_runner = runner   # keep a ref so it isn't GC'd mid-download
        runner.start()

    def _on_model_download_finished(self, error: str) -> None:
        self._set_nav_enabled(True)
        if error:
            self.progress_label.setText(
                f"Model download failed: {error}. You can retry later from Settings."
            )
        else:
            self.progress_bar.setValue(100)
            self.progress_label.setText("Model ready.")

    def _kick_gpu_runtime_download(self) -> None:
        runtime_base = self._paths.runtime_dir / "nvidia"
        if gpu_runtime.is_runtime_installed(runtime_base):
            self.gpu_progress_label.setText("GPU runtime already installed.")
            self.gpu_progress_bar.setValue(100)
            return
        self.gpu_progress_label.setText("Downloading GPU runtime...")
        self._set_nav_enabled(False)
        runner = _DownloadRunner(
            lambda r: self._download_gpu_runtime(runtime_base, r)
        )
        runner.progress.connect(self.gpu_progress_bar.setValue)
        runner.status.connect(self.gpu_progress_label.setText)
        runner.finished.connect(self._on_gpu_download_finished)
        self._gpu_runner = runner
        runner.start()

    def _on_gpu_download_finished(self, error: str) -> None:
        self._set_nav_enabled(True)
        if error:
            self.gpu_progress_label.setText(
                f"GPU runtime download failed: {error}. You can retry on next launch."
            )
        else:
            self.gpu_progress_bar.setValue(100)
            self.gpu_progress_label.setText("GPU runtime ready.")

    def _download_gpu_runtime(self, runtime_base, runner: _DownloadRunner) -> None:
        """Worker-thread body: forwards package progress via runner signals."""
        seen_packages: list[str] = []

        def progress(name: str, done: int, total: int) -> None:
            if name not in seen_packages:
                seen_packages.append(name)
            pct = int(100 * len(seen_packages) / max(1, len(gpu_runtime.REQUIRED_PACKAGES)))
            runner.progress.emit(min(99, pct))
            runner.status.emit(f"Downloading {name}...")

        gpu_runtime.download_runtime(runtime_base, progress_callback=progress)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ui/test_first_run_wizard.py -v` then `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/first_run_wizard.py tests/ui/test_first_run_wizard.py
git commit -m "fix(ui): wizard downloads run on worker threads; nav locked during download"
```

---

### Task 14: Settings dialog — network calls off the GUI thread

**Files:**
- Modify: `src/teams_transcriber/ui/settings_dialog.py` (`_wrike_test_connection`, `_manual_update_check`)
- Test: `tests/ui/test_settings_integrations_tab.py`, `tests/ui/test_settings_dialog.py`

**Interfaces:**
- Produces: `_wrike_test_connection` and `_manual_update_check` dispatch to daemon threads and hop back with the 3-arg `QTimer.singleShot(0, self, callable)` pattern; new slots `_on_wrike_test_done(msg: str)` and `_on_update_check_done(latest, error: str)`; the triggering buttons (`self._wrike_test_btn`, `self._check_updates_btn` — store them as attributes) disable while in flight. Both `QApplication.processEvents()` hacks are removed.
- Consumes: existing `wrike_client.WrikeClient`, `update_checker.fetch_latest_release`.

- [ ] **Step 1: Update/extend the tests**

Existing tests that call `_wrike_test_connection()` and assert the label synchronously (see `tests/ui/test_settings_integrations_tab.py`) must add
`qtbot.waitUntil(lambda: "Checking" not in dlg.wrike_status_label.text(), timeout=3000)` before their assertions. Add one new test there:

```python
def test_wrike_test_disables_button_while_checking(qapp, qtbot, ...) -> None:
    import threading
    gate = threading.Event()

    class _SlowClient:
        def __init__(self, token): ...
        def test_connection(self):
            gate.wait(timeout=5)
            return {"firstName": "A", "lastName": "B"}
        def close(self): ...

    # monkeypatch wrike_client.WrikeClient to _SlowClient per this file's pattern
    dlg = ...  # construct SettingsDialog per this file's existing fixture
    dlg.wrike_token_input.setText("tok")
    dlg._wrike_test_connection()
    assert not dlg._wrike_test_btn.isEnabled()
    gate.set()
    qtbot.waitUntil(lambda: dlg._wrike_test_btn.isEnabled(), timeout=3000)
    assert "Connected as A B" in dlg.wrike_status_label.text()
```

- [ ] **Step 2: Run tests to verify the new one fails**

Run: `uv run pytest tests/ui/test_settings_integrations_tab.py -v`
Expected: new test FAILs (button attribute missing / GUI blocks on gate).

- [ ] **Step 3: Implement**

In `_build_integrations_tab`, store the button: `self._wrike_test_btn = test_btn` (rename local uses accordingly). In `_build_about_tab`, store: `self._check_updates_btn = check_btn`.

Replace `_wrike_test_connection`:

```python
    def _wrike_test_connection(self) -> None:
        import threading
        from PySide6.QtCore import QTimer
        from teams_transcriber.integrations import wrike_client as _wc

        token = self.wrike_token_input.text().strip() or (
            keyring.get_password(KEYRING_SERVICE, KEYRING_USER_WRIKE) or ""
        )
        if not token:
            self.wrike_status_label.setText("Enter a token first.")
            return
        self.wrike_status_label.setText("Checking…")
        self._wrike_test_btn.setEnabled(False)

        def _worker() -> None:
            client = _wc.WrikeClient(token=token)
            try:
                me = client.test_connection()
                name = (me.get("firstName") or "user") + " " + (me.get("lastName") or "")
                msg = f"<span style='color:#065F46;'>✓ Connected as {name.strip()}</span>"
            except Exception as exc:
                msg = f"<span style='color:#DC2626;'>✗ {exc}</span>"
            finally:
                client.close()
            QTimer.singleShot(0, self, lambda: self._on_wrike_test_done(msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_wrike_test_done(self, msg: str) -> None:
        self.wrike_status_label.setText(msg)
        self._wrike_test_btn.setEnabled(True)
```

Replace `_manual_update_check`:

```python
    def _manual_update_check(self) -> None:
        """Fetch the latest release on a worker thread; UI stays responsive."""
        import threading
        from PySide6.QtCore import QTimer
        from teams_transcriber.update_checker import UpdateCheckError, fetch_latest_release

        self._update_check_result.setText("Checking…")
        self._check_updates_btn.setEnabled(False)

        def _worker() -> None:
            try:
                latest, err = fetch_latest_release(), ""
            except UpdateCheckError as exc:
                latest, err = None, str(exc)
            QTimer.singleShot(0, self, lambda: self._on_update_check_done(latest, err))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_update_check_done(self, latest, error: str) -> None:
        from datetime import UTC, datetime
        from teams_transcriber import __version__
        from teams_transcriber.update_checker import is_update_available

        self._check_updates_btn.setEnabled(True)
        if error:
            self._update_check_result.setText(
                f"<span style='color: #DC2626;'>Check failed: {error}</span>"
            )
            return
        ts = datetime.now(UTC).strftime("%Y-%m-%d %I:%M %p UTC")
        self._last_check_label.setText(f"Last update check: {ts}")
        if is_update_available(__version__, latest):
            self._latest_release = latest
            self._update_check_result.setText(
                f"<b>Update available: {latest.tag}</b><br>"
                f"<a href='{latest.html_url}'>View release notes</a>"
            )
            self._update_check_result.setOpenExternalLinks(True)
            self._install_btn.setVisible(True)
        else:
            self._latest_release = None
            self._install_btn.setVisible(False)
            self._update_check_result.setText("You're on the latest version.")
```

Remove both `QApplication.processEvents()` calls; drop the `QApplication` import if now unused.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ui/test_settings_integrations_tab.py tests/ui/test_settings_dialog.py -v` then `uv run pytest`
Expected: PASS (after the sync→waitUntil updates in Step 1).

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/settings_dialog.py tests/ui/
git commit -m "fix(ui): settings network calls run on worker threads"
```

---

### Task 15: App wiring — graceful update quit, transcript raise/cleanup, flicker-free pin, one settings opener

**Files:**
- Modify: `src/teams_transcriber/ui/update_dialog.py` (quit_callback)
- Modify: `src/teams_transcriber/ui/settings_dialog.py` (`update_quit_callback` passthrough)
- Modify: `src/teams_transcriber/ui/transcript_window.py` (closed signal)
- Modify: `src/teams_transcriber/ui/workspace_window.py:182-188` (`_on_always_on_top`)
- Modify: `src/teams_transcriber/ui/app.py` (`_quit_for_update`, `_show_transcript`, settings-opener consolidation)
- Test: `tests/ui/test_update_dialog.py`, `tests/ui/test_transcript_window.py`

**Interfaces:**
- Produces:
  - `UpdateDialog(..., quit_callback: Callable[[], None] | None = None)`; on "Restart now" it launches the installer then calls `quit_callback` (fallback `QApplication.quit()`); `sys.exit(0)` is gone.
  - `SettingsDialog(..., update_quit_callback: Callable[[], None] | None = None)` forwarded to `UpdateDialog` in `_install_update`.
  - `TranscriptWindow.closed = Signal(int)` emitted from `closeEvent`.
  - `App._quit_for_update()` — hotkeys.stop + pipeline.shutdown + db.close + `qapp.exit(0)`.
  - `App._open_settings_tab(tab: str | None)` — single opener; `_open_settings()` delegates; the three `_open_settings_*_tab` methods are deleted and their toast callbacks become `lambda: self._open_settings_tab("Audio")` / `"Transcription"` / `"AI"`.
- Consumes: `scrim.exec_modal` (Task 6).

- [ ] **Step 1: Write the failing tests**

Append to `tests/ui/test_update_dialog.py` (reuse its existing paths/monkeypatch fixtures):

```python
def test_restart_uses_quit_callback_not_sys_exit(qapp, tmp_path, monkeypatch):
    import subprocess
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.ui.update_dialog import UpdateDialog

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: None)
    quits: list[bool] = []
    dlg = UpdateDialog(
        version="v9.9.9", download_url="http://localhost/x.exe",
        paths=AppPaths(root=tmp_path / "TT"),
        quit_callback=lambda: quits.append(True),
    )
    dlg._launch_installer_and_quit()
    assert quits == [True]
```

Append to `tests/ui/test_transcript_window.py` (reuse its db fixture):

```python
def test_transcript_window_emits_closed(qapp, qtbot, ...):
    win = ...  # construct per this file's existing pattern
    received: list[int] = []
    win.closed.connect(received.append)
    win.close()
    assert received == [win._recording_id]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_update_dialog.py tests/ui/test_transcript_window.py -v`
Expected: FAIL (`unexpected keyword 'quit_callback'`; `sys.exit` would kill pytest — the monkeypatched Popen path reaches `sys.exit(0)` → pytest reports SystemExit; no `closed` attribute).

- [ ] **Step 3: Implement**

`update_dialog.py`:
- `__init__` gains `quit_callback: Callable[[], None] | None = None` (import `Callable` from `collections.abc`), stored as `self._quit_callback`.
- Replace the end of `_launch_installer_and_quit` (`sys.exit(0)`) with:

```python
        if self._quit_callback is not None:
            self._quit_callback()
            return
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.quit()
```

- Remove the now-unused `import sys`.

`settings_dialog.py`: `__init__` gains `update_quit_callback: Callable[[], None] | None = None`, stored; `_install_update` passes `quit_callback=self._update_quit_callback` to `UpdateDialog`.

`transcript_window.py`:

```python
from PySide6.QtCore import Qt, Signal
...
class TranscriptWindow(FramelessWindowMixin, QWidget):
    closed = Signal(int)   # recording_id
```

and extend the Task-4 `closeEvent` to emit before `super()`:

```python
    def closeEvent(self, ev) -> None:  # noqa: N802
        from teams_transcriber.ui.window_state import save_window_geometry
        save_window_geometry(self, "transcript")
        self.closed.emit(self._recording_id)
        super().closeEvent(ev)
```

`workspace_window.py` — replace `_on_always_on_top`:

```python
    def _on_always_on_top(self, enabled: bool) -> None:
        # SetWindowPos toggles topmost without recreating the native window
        # (setWindowFlags + show() destroys/recreates it: visible flicker,
        # dropped maximized state). Fall back to the flag dance if it fails.
        import ctypes
        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010
        try:
            ok = bool(ctypes.windll.user32.SetWindowPos(
                int(self.winId()),
                HWND_TOPMOST if enabled else HWND_NOTOPMOST,
                0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            ))
        except Exception:
            ok = False
        if not ok:
            flags = self.windowFlags()
            if enabled:
                flags |= Qt.WindowType.WindowStaysOnTopHint
            else:
                flags &= ~Qt.WindowType.WindowStaysOnTopHint
            self.setWindowFlags(flags)
            self.show()
```

`app.py`:
- add:

```python
    def _quit_for_update(self) -> None:
        """Clean shutdown before the installer replaces files on disk."""
        self.hotkeys.stop()
        self.pipeline.shutdown()
        self.db.close()
        self.qapp.exit(0)
```

- `_start_update_download`: pass `quit_callback=self._quit_for_update` to `UpdateDialog`.
- replace `_open_settings` + the three `_open_settings_*_tab` methods with:

```python
    def _open_settings(self) -> None:
        self._open_settings_tab(None)

    def _open_settings_tab(self, tab: str | None) -> None:
        """Open Settings, optionally jumping to a named tab."""
        from PySide6.QtWidgets import QTabWidget
        from teams_transcriber.ui.scrim import exec_modal
        dlg = SettingsDialog(
            self.settings, self.paths,
            hotkey_reload_callback=self._on_hotkey_reload,
            update_quit_callback=self._quit_for_update,
            parent=self.window,
        )
        if tab is not None:
            for child in dlg.findChildren(QTabWidget):
                for i in range(child.count()):
                    if child.tabText(i) == tab:
                        child.setCurrentIndex(i)
                        break
        dlg.saved.connect(self._refresh_history)
        exec_modal(dlg)
```

- update the three callback references: in `_on_recording_failed` and `_on_recording_device_fallback` use `action_callback=lambda: self._open_settings_tab("Audio")`; in `_on_transcription_failed` use `lambda: self._open_settings_tab("Transcription")`; in `_retry_recording` and `_on_summary_failed` use `lambda: self._open_settings_tab("AI")`.
- `_show_transcript` — raise-if-open + cleanup:

```python
    def _show_transcript(self, recording_id: int) -> None:
        from teams_transcriber.ui.transcript_window import TranscriptWindow
        self._transcript_windows = getattr(self, "_transcript_windows", {})
        existing = self._transcript_windows.get(recording_id)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        win = TranscriptWindow(db=self.db, recording_id=recording_id)
        win.closed.connect(
            lambda rid: self._transcript_windows.pop(rid, None)
        )
        self._transcript_windows[recording_id] = win
        win.show()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest`
Expected: PASS. Grep for stale references first: `grep -rn "_open_settings_audio_tab\|_open_settings_ai_tab\|_open_settings_transcription_tab" src tests` — update any test that patches/calls them.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/update_dialog.py src/teams_transcriber/ui/settings_dialog.py src/teams_transcriber/ui/transcript_window.py src/teams_transcriber/ui/workspace_window.py src/teams_transcriber/ui/app.py tests/ui/
git commit -m "fix(ui): graceful update restart, transcript raise/cleanup, flicker-free pin, single settings opener"
```

---

### Task 16: Honor the saved audio devices (Settings → Audio was a no-op)

**Files:**
- Modify: `src/teams_transcriber/ui/app.py:186-187` (audio_factory)
- Modify: `src/teams_transcriber/cli.py:30-32` (_audio_factory)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `RealAudioSource.from_settings(settings)` (exists at `audio/source.py:121` — resolves saved id → saved name → Windows default and populates `device_fallbacks`, which feeds the RecordingDeviceFallback toast that is currently dead code).
- Produces: both factories construct from settings.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_audio_factory_uses_saved_devices(tmp_path, monkeypatch):
    """The pipeline's audio factory must construct from settings, not defaults."""
    from teams_transcriber.cli import _build_pipeline
    from teams_transcriber.paths import AppPaths

    captured: list = []

    def fake_from_settings(settings):
        captured.append(settings)
        return object()

    monkeypatch.setattr(
        "teams_transcriber.audio.source.RealAudioSource.from_settings",
        staticmethod(fake_from_settings),
    )
    paths = AppPaths(root=tmp_path / "TT")
    paths.ensure_dirs()
    pipeline = _build_pipeline(paths, with_watcher=False)
    pipeline._audio_source_factory()
    assert len(captured) == 1
    assert hasattr(captured[0], "audio_mic_device")   # a real Settings object
    pipeline.shutdown()


def test_ui_app_factory_does_not_use_default_devices_shim():
    import inspect
    import teams_transcriber.ui.app as app_mod
    assert "from_default_devices" not in inspect.getsource(app_mod)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v -k audio_factory`
Expected: FAIL (`from_settings` never called; source scan finds `from_default_devices`).

- [ ] **Step 3: Implement**

`app.py` (inside `App.__init__`):

```python
        def audio_factory() -> Any:
            # from_settings resolves the saved mic/loopback (id → name →
            # Windows default) and records fallbacks for the warning toast.
            return RealAudioSource.from_settings(self.settings)
```

`cli.py`:

```python
    def _audio_factory() -> Any:
        from teams_transcriber.audio.source import RealAudioSource
        return RealAudioSource.from_settings(settings)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cli.py tests/audio -v` then `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/app.py src/teams_transcriber/cli.py tests/test_cli.py
git commit -m "fix(audio): honor saved mic/loopback devices from Settings"
```

---

### Task 17: Hotkey callbacks marshal to the Qt main thread

**Files:**
- Modify: `src/teams_transcriber/ui/app.py:469-477` (`_apply_hotkeys`, new `_marshal`)
- Test: `tests/ui/test_app_hotkey_marshal.py` (new)

**Interfaces:**
- Produces: `App._marshal(fn: Callable[[], None]) -> Callable[[], None]` — wraps a callback so invoking it (from any thread) queues `fn` onto the Qt main thread via the 3-arg `QTimer.singleShot(0, self.window, fn)` pattern. `_apply_hotkeys` wraps all three callbacks.
- Consumes: `self.window` (QWidget on the main thread).

The `keyboard` library invokes hotkey callbacks on its listener thread; today those callbacks construct `WorkspaceWindow`/`ToastBanner` QWidgets off-thread (undefined behavior, intermittent crashes).

- [ ] **Step 1: Write the failing test**

Create `tests/ui/test_app_hotkey_marshal.py`:

```python
from __future__ import annotations

import threading

from PySide6.QtWidgets import QWidget

from teams_transcriber.ui.app import App


def test_marshal_runs_callback_on_main_thread(qapp, qtbot):
    class _Fake:
        window = QWidget()
    fake = _Fake()

    ran_on: list[int] = []
    wrapped = App._marshal(fake, lambda: ran_on.append(threading.get_ident()))

    worker = threading.Thread(target=wrapped)
    worker.start()
    worker.join(timeout=2)

    qtbot.waitUntil(lambda: len(ran_on) == 1, timeout=2000)
    assert ran_on[0] == threading.get_ident()   # main (test) thread, not worker


def test_apply_hotkeys_wraps_callbacks(qapp):
    import inspect
    from teams_transcriber.ui import app as app_mod
    src = inspect.getsource(app_mod.App._apply_hotkeys)
    assert "_marshal" in src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_app_hotkey_marshal.py -v`
Expected: FAIL (`App` has no `_marshal`).

- [ ] **Step 3: Implement in `app.py`**

```python
    def _marshal(self, fn):
        """Wrap a callback so it executes on the Qt main thread.

        Global hotkeys fire on the keyboard library's listener thread;
        creating QWidgets there is undefined behavior. The 3-arg singleShot
        binds the timer to self.window's (main) thread — same pattern as the
        worker-thread hops elsewhere in this file.
        """
        from PySide6.QtCore import QTimer
        return lambda: QTimer.singleShot(0, self.window, fn)

    def _apply_hotkeys(self, hotkey_map: dict[str, str]) -> None:
        self.hotkeys.reload([
            (hotkey_map.get("toggle_manual_recording", "ctrl+alt+r"),
             self._marshal(self._toggle_manual)),
            (hotkey_map.get("open_workspace", "ctrl+alt+n"),
             self._marshal(self._open_workspace_for_active)),
            (hotkey_map.get("toggle_pause_detection", "ctrl+alt+p"),
             self._marshal(self._toggle_pause_detection)),
        ])
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ui/test_app_hotkey_marshal.py tests/test_hotkeys.py -v` then `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/app.py tests/ui/test_app_hotkey_marshal.py
git commit -m "fix(ui): hotkey callbacks marshal to the Qt main thread"
```

---

### Task 18: Re-summarization must not clobber todo done-state

**Files:**
- Modify: `src/teams_transcriber/storage/todos.py` (add `seed`)
- Modify: `src/teams_transcriber/summarizer.py:292-294` (`_persist` uses `seed`)
- Test: `tests/storage/test_todos.py` (or the storage test file that covers TodoStateRepo — locate with `uv run pytest tests/storage --collect-only -q | grep -i todo`)

**Interfaces:**
- Produces: `TodoStateRepo.seed(recording_id: int, todo_index: int, task_text: str) -> None` — inserts an unchecked row if none exists; on conflict updates only `task_text`, preserving `done`/`done_at`.
- Consumes: existing `todo_state` table.

- [ ] **Step 1: Write the failing test**

Add to the TodoStateRepo test file (uses the shared `db` fixture from `tests/conftest.py`):

```python
def test_seed_preserves_done_state(db):
    from teams_transcriber.storage import TodoStateRepo
    repo = TodoStateRepo(db)
    # ... create a recording row first, per this file's existing pattern ...
    repo.upsert(rid, 0, "task A", True)     # user checked it off
    repo.seed(rid, 0, "task A (reworded)")  # re-summarization reseeds
    rows = repo.list_for_recording(rid)
    assert rows[0].done is True             # done survived
    assert rows[0].task_text == "task A (reworded)"
    assert rows[0].done_at is not None


def test_seed_creates_unchecked_row_when_missing(db):
    from teams_transcriber.storage import TodoStateRepo
    repo = TodoStateRepo(db)
    # ... create a recording row rid ...
    repo.seed(rid, 1, "new task")
    rows = repo.list_for_recording(rid)
    assert rows[0].done is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/storage -v -k seed`
Expected: FAIL (`TodoStateRepo` has no `seed`).

- [ ] **Step 3: Implement**

`todos.py` — add:

```python
    def seed(self, recording_id: int, todo_index: int, task_text: str) -> None:
        """Ensure a row exists for a (re-)generated todo WITHOUT touching its
        done state — done flags must survive re-summarization (the module
        contract). Only the task text is refreshed on conflict."""
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO todo_state (recording_id, todo_index, task_text, done, done_at)
                VALUES (?, ?, ?, 0, NULL)
                ON CONFLICT(recording_id, todo_index) DO UPDATE SET
                    task_text = excluded.task_text
                """,
                (recording_id, todo_index, task_text),
            )
            conn.commit()
```

`summarizer.py` `_persist` — replace the seeding loop:

```python
        # Seed todo_state rows for each my_todo so the UI can toggle them.
        # seed() (not upsert) so re-summarization keeps existing done flags.
        for i, td in enumerate(summary.my_todos):
            todo_repo.seed(recording_id, todo_index=i, task_text=td.task)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/storage tests/test_summarizer.py -v` then `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/storage/todos.py src/teams_transcriber/summarizer.py tests/storage/
git commit -m "fix(storage): re-summarization preserves todo done-state"
```

---

### Task 19: Wrike planner — typed-but-unmatched assignee must not use stale selection

**Files:**
- Modify: `src/teams_transcriber/ui/wrike_sync_planner.py:181-189` (combo setup), `:252-258` (`build_plan`)
- Test: `tests/ui/test_wrike_sync_planner.py`

**Interfaces:**
- Produces: `build_plan` resolves the assignee from the combo's **current text** (exact item match → that contact id; anything else → `None`/Unassigned). Combos get `setInsertPolicy(NoInsert)` so Enter can't insert phantom items.

- [ ] **Step 1: Write the failing test**

Append to `tests/ui/test_wrike_sync_planner.py` (reuse its fixtures for items/contacts; the planner needs one `action_other` item and ≥1 contact):

```python
def test_typed_unmatched_assignee_falls_back_to_unassigned(qapp, ...):
    planner = ...  # build per this file's existing pattern, with a suggested assignee preselected
    row = planner._rows[0]        # the action_other row
    combo = row["assignee_combo"]
    assert combo.currentIndex() > 0            # suggestion preselected
    combo.setEditText("Bob The Contractor")    # not in contacts
    plan = planner.build_plan()
    target = next(r for r in plan if r.item.kind == "action_other")
    assert target.assignee_id is None          # NOT the stale suggestion


def test_typed_exact_contact_name_resolves(qapp, ...):
    planner = ...
    combo = planner._rows[0]["assignee_combo"]
    combo.setEditText(contact_full_name)       # exact known contact name
    plan = planner.build_plan()
    target = next(r for r in plan if r.item.kind == "action_other")
    assert target.assignee_id == contact_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_wrike_sync_planner.py -v -k assignee`
Expected: first test FAILs (stale suggestion id returned).

- [ ] **Step 3: Implement**

In `_build_row`, after `assignee_cb.setEditable(True)` add:

```python
            assignee_cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
```

In `build_plan`, replace the assignee block:

```python
            assignee = None
            if s["assignee_combo"] is not None:
                combo = s["assignee_combo"]
                idx = combo.currentIndex()
                typed = combo.currentText().strip()
                # The combo is editable: typing over a selection does NOT
                # reset currentIndex, so trust the text, not the index. An
                # exact item match resolves to that contact; anything else
                # (including a typed name not in contacts) is Unassigned.
                if idx >= 0 and combo.itemText(idx) == typed:
                    assignee = combo.itemData(idx)
                else:
                    match = combo.findText(typed, Qt.MatchFlag.MatchFixedString)
                    assignee = combo.itemData(match) if match >= 0 else None
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ui/test_wrike_sync_planner.py -v` then `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/wrike_sync_planner.py tests/ui/test_wrike_sync_planner.py
git commit -m "fix(wrike): planner resolves assignee from typed text, not stale index"
```

---

### Task 20: Wrike sync sends the todo's current done state

**Files:**
- Modify: `src/teams_transcriber/integrations/wrike_sync.py:253-315` (`sync_items`)
- Test: `tests/integrations/test_wrike_sync.py` (the existing sync_items test file — locate exact name via `ls tests/integrations`)

**Interfaces:**
- Produces: for `my_todo` items already checked done locally, the created Wrike task has `"status": "Completed"` and the `wrike_tasks` row is persisted with `last_synced_done=True` (so the close-loop won't push a spurious un-complete).
- Consumes: `TodoStateRepo.list_for_recording`.

- [ ] **Step 1: Write the failing test**

Append to the sync_items test file (reuse its fake client + seeded-recording fixtures):

```python
def test_sync_sends_completed_for_locally_done_todo(db, ...):
    from teams_transcriber.storage import TodoStateRepo
    TodoStateRepo(db).upsert(rid, 0, "task A", True)   # checked off locally
    # plan contains the my_todo item with index 0, format 'task'
    report = sync_items(db, rid, plan, client=fake_client)
    payload = fake_client.created_tasks[0]             # per fake's recording convention
    assert payload["status"] == "Completed"
    rows = WrikeTaskRepo(db).list_for_recording(rid)
    my_row = next(r for r in rows if r.kind == "my")
    assert my_row.last_synced_done is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integrations -v -k completed`
Expected: FAIL (`status == "Active"`, `last_synced_done is False`).

- [ ] **Step 3: Implement in `sync_items`**

After building `already`, load the done map:

```python
    from teams_transcriber.storage import TodoStateRepo
    done_by_index = {
        s.todo_index: s.done
        for s in TodoStateRepo(db).list_for_recording(recording_id)
    }
```

In the `row.format == "task"` branch:

```python
            if row.format == "task":
                title, body = _task_title_and_body(item, rec_title, started_at)
                # A my_todo checked off locally before the send must land in
                # Wrike as Completed; the close-loop only reacts to future
                # toggles, so this is the only chance to get parity.
                is_done = item.kind == "my_todo" and bool(done_by_index.get(item.index))
                payload: dict[str, Any] = {
                    "title": title,
                    "description": body,
                    "status": "Completed" if is_done else "Active",
                }
```

and in the `WrikeTaskRow` insert, replace `last_synced_done=False` with `last_synced_done=(row.format == "task" and item.kind == "my_todo" and bool(done_by_index.get(item.index)))` — or simpler, compute `is_done = False` before the `if row.format == "task"` block, assign inside it, and use `last_synced_done=is_done`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/integrations -v` then `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/integrations/wrike_sync.py tests/integrations/
git commit -m "fix(wrike): sync sends Completed for todos already done locally"
```

---

### Task 21: Master to-do view toggles run the Wrike close-loop

**Files:**
- Modify: `src/teams_transcriber/ui/app.py:340-343` (todo_toggled wiring)
- Test: `tests/ui/test_app_wrike_close_loop.py`

**Interfaces:**
- Produces: `App._on_master_todo_toggled(recording_id: int)` — refreshes history AND calls `_wrike_close_loop_sync(recording_id)` (parity with the summary pane's `_on_todo_state_changed`, minus the `master_todos.reload()` which would tear down the checkbox mid-signal).

- [ ] **Step 1: Write the failing test**

Append to `tests/ui/test_app_wrike_close_loop.py`, following that file's existing pattern for exercising App-level wiring (it already fakes/patches `_wrike_close_loop_sync` collaborators):

```python
def test_master_todo_toggle_triggers_close_loop(qapp, ...):
    # per this file's existing App-fixture pattern:
    calls: list[int] = []
    app._wrike_close_loop_sync = calls.append          # type: ignore[assignment]
    app.master_todos.todo_toggled.emit(42)
    assert calls == [42]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_app_wrike_close_loop.py -v -k master`
Expected: FAIL (close-loop never called).

- [ ] **Step 3: Implement in `app.py`**

Replace the lambda connection in `_build_main_content`:

```python
        self.master_todos.go_to_summary.connect(self._go_to_summary_from_todos)
        self.master_todos.todo_toggled.connect(self._on_master_todo_toggled)
```

Add next to `_on_todo_state_changed`:

```python
    def _on_master_todo_toggled(self, recording_id: int) -> None:
        """Master-view toggle: same close-loop as the summary pane's checkbox.
        No master_todos.reload() here — the toggled checkbox is the sender and
        reload would delete it mid-signal; the view already shows the new state."""
        self._refresh_history(query=self.search.input.text() or None)
        self._wrike_close_loop_sync(recording_id)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ui/test_app_wrike_close_loop.py -v` then `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/app.py tests/ui/test_app_wrike_close_loop.py
git commit -m "fix(wrike): master to-do toggles push completion to Wrike too"
```

---

### Task 22: Pipeline — resume summarization at recovery; retries off the caller thread

**Files:**
- Modify: `src/teams_transcriber/pipeline.py:85-87` (`retry_summary`), `:302-309` (recovery branch)
- Test: `tests/test_pipeline.py` (or `tests/test_finalize_or_recover.py` — whichever holds the `_recover_stuck_recordings` tests; check both)

**Interfaces:**
- Produces: recovery of a `TRANSCRIBING`-with-segments row sets `SUMMARIZING` **and** submits `summarizer.summarize` on the pipeline executor; `retry_summary` also submits to the executor instead of running the Anthropic call synchronously on the caller (UI) thread.
- Consumes: `self._executor`, `self._pending_futures`, `self._settings.anthropic_api_key()`.

- [ ] **Step 1: Write the failing tests**

Add to the recovery test file (reuse its fixture style — fake summarizer/transcriber objects recording calls):

```python
def test_recover_transcribing_with_segments_resumes_summarization(...):
    # seed: recording at TRANSCRIBING with ≥1 transcript segment (existing pattern)
    pipeline._recover_stuck_recordings()
    pipeline._executor.shutdown(wait=True)     # drain the submitted work
    assert fake_summarizer.calls == [rid]      # summarize actually ran


def test_retry_summary_runs_on_executor_not_caller(...):
    import threading
    seen_threads: list[int] = []
    fake_summarizer.on_summarize = lambda *a, **kw: seen_threads.append(threading.get_ident())
    pipeline.retry_summary(rid, api_key="k")
    pipeline._executor.shutdown(wait=True)
    assert seen_threads and seen_threads[0] != threading.get_ident()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py tests/test_finalize_or_recover.py -v -k "recover_transcribing or retry_summary"`
Expected: FAIL (summarize never called at recovery; retry runs on the caller thread).

- [ ] **Step 3: Implement**

`retry_summary`:

```python
    def retry_summary(self, recording_id: int, *, api_key: str | None) -> None:
        """Re-run summarization on the post-processing executor. Never on the
        caller thread — the UI invokes this from a Qt slot and the Anthropic
        call would freeze the event loop."""
        future = self._executor.submit(
            self._summarizer.summarize, recording_id, api_key=api_key,
        )
        self._pending_futures.append(future)
```

Recovery branch (`TRANSCRIBING` loop):

```python
            if segments:
                logger.info("recover: %d had segments, resuming summarization", rec.id)
                rec_repo.update_status(rec.id, RecordingStatus.SUMMARIZING)
                future = self._executor.submit(
                    self._summarizer.summarize, rec.id,
                    api_key=self._settings.anthropic_api_key(),
                )
                self._pending_futures.append(future)
                continue
```

Also grep `retry_transcription` in `pipeline.py`: if it calls `self._transcriber.transcribe(...)` synchronously, route it through `self._submit_post_processing(recording_id)` the same way (same UI-freeze class of bug); update its tests identically.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_pipeline.py tests/test_finalize_or_recover.py tests/test_phase6_pipeline.py tests/test_pipeline_defer.py -v` then `uv run pytest`
Expected: PASS. Existing tests that relied on retry being synchronous need an executor drain (`pipeline._executor.shutdown(wait=True)` or a waitUntil) — update them.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/pipeline.py tests/
git commit -m "fix(pipeline): resume summarization at recovery; retries run on the executor"
```

---

### Task 23: Re-download deletes only the configured model's cache

**Files:**
- Modify: `src/teams_transcriber/ui/settings_dialog.py:442-509` (`_redownload_model`, new module-level helper)
- Test: `tests/ui/test_settings_dialog.py`

**Interfaces:**
- Produces: module-level `_model_cache_candidates(cache_root: Path, model: str) -> list[Path]` in `settings_dialog.py` — returns only directories whose name ends with `faster-whisper-{model}` or contains `model.replace("/", "--")`; the indiscriminate `"faster-whisper" in d.name` sweep is gone.

- [ ] **Step 1: Write the failing test**

Append to `tests/ui/test_settings_dialog.py`:

```python
def test_model_cache_candidates_only_configured_model(tmp_path):
    from teams_transcriber.ui.settings_dialog import _model_cache_candidates
    (tmp_path / "models--Systran--faster-whisper-large-v3").mkdir()
    (tmp_path / "models--Systran--faster-whisper-medium").mkdir()
    (tmp_path / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo").mkdir()

    got = [d.name for d in _model_cache_candidates(tmp_path, "medium")]
    assert got == ["models--Systran--faster-whisper-medium"]

    got = [d.name for d in _model_cache_candidates(tmp_path, "large-v3")]
    assert got == ["models--Systran--faster-whisper-large-v3"]   # NOT the turbo dir
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_settings_dialog.py -v -k cache_candidates`
Expected: FAIL (`ImportError: _model_cache_candidates`).

- [ ] **Step 3: Implement**

Add module-level in `settings_dialog.py`:

```python
def _model_cache_candidates(cache_root, model: str) -> list:
    """Cached HF snapshot dirs for exactly this Whisper model.

    HF cache dirs look like `models--<org>--faster-whisper-<model>`. endswith
    keeps 'large-v3' from also matching 'large-v3-turbo'; the replace() form
    covers a fully-qualified `org/repo` model value."""
    if not cache_root.is_dir():
        return []
    marker_suffix = f"faster-whisper-{model}"
    marker_full = model.replace("/", "--")
    return [
        d for d in sorted(cache_root.iterdir())
        if d.is_dir() and (d.name.endswith(marker_suffix) or marker_full in d.name)
    ]
```

In `_redownload_model`, replace the whole candidate scan (the `candidates` build plus the dedup block) with:

```python
        candidates = _model_cache_candidates(cache_root, repo_id)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ui/test_settings_dialog.py -v` then `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/settings_dialog.py tests/ui/test_settings_dialog.py
git commit -m "fix(ui): model re-download deletes only the configured model's cache"
```

---

### Task 24: Small confirmed fixes — workspace placeholder actually shows; banner width restores

**Files:**
- Modify: `src/teams_transcriber/ui/workspace_window.py:154-175` (`_show_placeholder`, `_on_summary_ready_refresh`)
- Modify: `src/teams_transcriber/ui/active_recording_banner.py:79-90` (`show_recording`)
- Test: `tests/ui/test_workspace_window.py`, `tests/ui/test_active_recording_banner.py`

**Interfaces:** behavior only.

Bug (a): `_show_placeholder` reads `transcript_view.parentWidget().layout()` — the parent is the QSplitter, whose `layout()` is `None`, so the "Transcription will appear when the meeting ends." placeholder is silently never shown when live streaming is disabled.
Bug (b): `set_processing` shrinks the elapsed label to `setFixedWidth(20)`; the next `show_recording` never restores 48px, so the timer text "00:00" is clipped on every recording after the first.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ui/test_workspace_window.py` (reuse its db/bridge fixtures; construct with `live=True` and a settings stub whose `transcription_live_enabled` is False, per the file's existing pattern):

```python
def test_placeholder_visible_when_live_streaming_disabled(qapp, ...):
    win = ...  # live=True, settings.transcription_live_enabled=False
    assert win._placeholder is not None
    assert win._placeholder.parent() is not None      # actually in the widget tree
    assert win._splitter.indexOf(win._placeholder) != -1
    assert win.transcript_view.isHidden()
```

Append to `tests/ui/test_active_recording_banner.py`:

```python
def test_elapsed_width_restored_on_next_recording(qapp):
    from teams_transcriber.ui.active_recording_banner import ActiveRecordingBanner
    b = ActiveRecordingBanner()
    b.show_recording(1, "First")
    b.set_processing()                    # shrinks the label to 20px
    b.hide_banner()
    b.show_recording(2, "Second")
    assert b._elapsed_label.width() >= 48 or b._elapsed_label.minimumWidth() >= 48
    b.hide_banner()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_workspace_window.py tests/ui/test_active_recording_banner.py -v -k "placeholder or elapsed"`
Expected: FAIL (placeholder has no parent / splitter index -1; width stuck at 20).

- [ ] **Step 3: Implement**

`workspace_window.py` — replace `_show_placeholder`:

```python
    def _show_placeholder(self, text: str) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel
        placeholder = QLabel(text)
        placeholder.setStyleSheet("color: #6B7280; padding: 24px; font-size: 13px;")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setWordWrap(True)
        # The transcript view's parent is the QSplitter (no layout()); swap the
        # view for the placeholder at the same splitter slot instead.
        idx = self._splitter.indexOf(self.transcript_view)
        self._splitter.insertWidget(idx, placeholder)
        self.transcript_view.hide()
        self._placeholder = placeholder
```

and in `_on_summary_ready_refresh`, after removing the placeholder, restore the view:

```python
        if self._placeholder is not None:
            self._placeholder.deleteLater()
            self._placeholder = None
        self.transcript_view.show()
```

`active_recording_banner.py` — in `show_recording`, before starting the timer:

```python
        self._elapsed_label.setFixedWidth(48)   # undo set_processing's shrink
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ui/test_workspace_window.py tests/ui/test_active_recording_banner.py -v` then `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/workspace_window.py src/teams_transcriber/ui/active_recording_banner.py tests/ui/
git commit -m "fix(ui): live-disabled placeholder actually shows; banner timer width restores"
```

---

## Final verification (after Task 24)

- [ ] `uv run pytest` — full suite green.
- [ ] Launch the app with the proxy scrubbed (`env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy uv run python -m teams_transcriber`) and walk through: move (snap to screen edge — Aero Snap engages), resize from all edges, maximize/restore by double-click and by dragging the title bar, resize the sidebar and the history/summary columns, close and relaunch (geometry + splitters restored), open Settings (scrim dims the main window, all widgets themed), trigger a confirm dialog, background the window (title dims, shadow softens).
- [ ] `git log --oneline` — one conventional commit per task.
