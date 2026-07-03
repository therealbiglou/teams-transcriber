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
