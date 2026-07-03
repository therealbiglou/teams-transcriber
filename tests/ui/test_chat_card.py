from PySide6.QtWidgets import QTextEdit
from teams_transcriber.storage.chat import ChatMessage
from teams_transcriber.ui.chat_card import ChatCard


def _msg(role: str, content: str, mid: int = 1) -> ChatMessage:
    return ChatMessage(id=mid, recording_id=10, role=role,
                       content=content, created_at="x")


def test_empty_history_shows_placeholder(qapp):
    card = ChatCard(recording_id=10, history=[])
    txt = card._placeholder.text().lower()
    assert "ask" in txt or "chat" in txt


def test_history_renders_user_and_assistant_bubbles(qapp):
    from PySide6.QtWidgets import QLabel
    card = ChatCard(
        recording_id=10,
        history=[
            _msg("user", "what was decided?"),
            _msg("assistant", "Ship Friday.", mid=2),
        ],
    )
    # Find wrapping QLabel bubbles (not the placeholder).
    bubbles = [w for w in card._message_container.findChildren(QLabel)
               if w.wordWrap()]
    texts = [b.text() for b in bubbles]
    assert any("what was decided?" in t for t in texts)
    assert any("Ship Friday." in t for t in texts)


def test_send_emits_signal_with_text_and_recording_id(qapp):
    card = ChatCard(recording_id=42, history=[])
    captured: list[tuple[int, str]] = []
    card.send_requested.connect(lambda rid, txt: captured.append((rid, txt)))
    card._input.setPlainText("how long was the meeting?")
    card._send_btn.click()
    assert captured == [(42, "how long was the meeting?")]


def test_send_does_nothing_when_input_is_blank(qapp):
    card = ChatCard(recording_id=42, history=[])
    captured: list[tuple[int, str]] = []
    card.send_requested.connect(lambda rid, txt: captured.append((rid, txt)))
    card._input.setPlainText("   \n   ")
    card._send_btn.click()
    assert captured == []


def test_set_pending_disables_input_and_send(qapp):
    card = ChatCard(recording_id=10, history=[])
    card.set_pending(True)
    assert not card._input.isEnabled()
    assert not card._send_btn.isEnabled()
    card.set_pending(False)
    assert card._input.isEnabled()
    assert card._send_btn.isEnabled()


def test_disabled_card_shows_hint_and_blocks_send(qapp):
    card = ChatCard(
        recording_id=10, history=[], enabled=False,
        disabled_hint="Set your Anthropic API key in Settings → AI to chat.",
    )
    assert "Anthropic" in card._disabled_label.text()
    captured: list[tuple[int, str]] = []
    card.send_requested.connect(lambda rid, txt: captured.append((rid, txt)))
    card._input.setPlainText("hi")
    card._send_btn.click()
    assert captured == []


def test_append_assistant_message_adds_bubble(qapp):
    from PySide6.QtWidgets import QLabel
    card = ChatCard(recording_id=10, history=[])
    card.append_assistant_message("here's an answer")
    bubbles = [w for w in card._message_container.findChildren(QLabel)
               if w.wordWrap()]
    texts = [b.text() for b in bubbles]
    assert any("here's an answer" in t for t in texts)


def test_append_error_message_renders_text(qapp):
    from PySide6.QtWidgets import QLabel
    card = ChatCard(recording_id=10, history=[])
    card.append_error_message("Anthropic key invalid")
    bubbles = [w for w in card._message_container.findChildren(QLabel)
               if w.wordWrap()]
    texts = [b.text() for b in bubbles]
    assert any("Anthropic key invalid" in t for t in texts)


def test_placeholder_is_hidden_when_history_renders_at_construction(qapp):
    """Constructing with non-empty history must hide the empty-state placeholder
    so it doesn't render alongside the past turns when the card is shown."""
    card = ChatCard(
        recording_id=10,
        history=[_msg("user", "first"), _msg("assistant", "second", mid=2)],
    )
    assert card._placeholder.isVisibleTo(card) is False


def test_bubbles_are_autosizing_selectable_labels(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel
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
