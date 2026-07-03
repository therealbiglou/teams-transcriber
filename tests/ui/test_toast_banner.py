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
