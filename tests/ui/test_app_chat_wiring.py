def test_chat_should_send_predicate():
    from teams_transcriber.ui.app import _chat_should_send
    assert _chat_should_send(api_key="k", text="hi") is True
    assert _chat_should_send(api_key="",  text="hi") is False
    assert _chat_should_send(api_key="k", text="") is False
    assert _chat_should_send(api_key="k", text="   ") is False
