from __future__ import annotations

from teams_transcriber.ui.search_bar import SearchBar


def test_typed_text_emits_debounced_query(qapp, qtbot) -> None:
    bar = SearchBar()
    received: list[str] = []
    bar.query_changed.connect(received.append)

    bar.input.setText("hello")
    qtbot.wait(300)
    assert received == ["hello"]


def test_rapid_typing_collapses_to_final_value(qapp, qtbot) -> None:
    bar = SearchBar()
    received: list[str] = []
    bar.query_changed.connect(received.append)

    bar.input.setText("a")
    bar.input.setText("ab")
    bar.input.setText("abc")
    qtbot.wait(300)
    assert received == ["abc"]
