from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton

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


def _body_label(dlg):
    # title is the first QLabel, body the second
    return dlg.findChildren(QLabel)[1]


def test_body_is_not_selectable_by_default(qtbot):
    dlg = ConfirmDialog(title="T", body="plain")
    qtbot.addWidget(dlg)
    flags = _body_label(dlg).textInteractionFlags()
    assert not (flags & Qt.TextInteractionFlag.TextSelectableByMouse)


def test_selectable_body_can_be_copied(qtbot):
    dlg = ConfirmDialog(title="T", body="a long api error", selectable=True)
    qtbot.addWidget(dlg)
    flags = _body_label(dlg).textInteractionFlags()
    assert flags & Qt.TextInteractionFlag.TextSelectableByMouse


def test_html_looking_body_is_displayed_literally(qtbot):
    """Stored error_message strings come from HTTP/proxy layers and can look
    like HTML (e.g. a 502 Bad Gateway response body). QLabel's default
    AutoText would render that as rich text -- tags vanish and the user
    can't see what actually happened. The label must be forced to
    PlainText so the raw string round-trips through .text() unchanged."""
    body = "<html><head><title>502 Bad Gateway</title></head><body>oops</body></html>"
    dlg = ConfirmDialog(title="Sync failed", body=body)
    qtbot.addWidget(dlg)
    label = _body_label(dlg)
    assert label.textFormat() == Qt.TextFormat.PlainText
    assert label.text() == body


def test_html_looking_title_is_displayed_literally(qtbot):
    """The same label renders the delete-confirmation body, which
    interpolates the LLM-generated meeting title -- a title containing
    e.g. '<div>' must not silently vanish."""
    body = 'Permanently delete "<div>Weekly Sync</div>"?'
    dlg = ConfirmDialog(title="Delete meeting?", body=body)
    qtbot.addWidget(dlg)
    assert _body_label(dlg).text() == body


def test_long_body_does_not_grow_dialog_past_a_bounded_height(qtbot):
    """A failure body can be up to ~2000 chars; the dialog must stay
    scrollable rather than growing taller than the screen (which would push
    the Close button off-screen)."""
    body = "line\n" * 400  # long enough to dwarf any reasonable screen height
    dlg = ConfirmDialog(title="Sync failed", body=body, cancel_label=None)
    qtbot.addWidget(dlg)
    dlg.show()
    assert dlg.height() < 1000
