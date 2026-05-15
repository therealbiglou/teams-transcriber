"""Windows toast notifications via winsdk, with a Qt fallback."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable

from PySide6.QtWidgets import QSystemTrayIcon

logger = logging.getLogger(__name__)


def show_toast(
    title: str,
    body: str,
    *,
    on_click: Callable[[], None] | None = None,
    fallback_tray: QSystemTrayIcon | None = None,
) -> None:
    """Show a Windows toast. Falls back to the tray balloon if WinRT path fails."""
    if _try_winsdk_toast(title, body, on_click):
        return
    if fallback_tray is not None:
        fallback_tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 5000)
        if on_click is not None:
            with contextlib.suppress(RuntimeError, TypeError):
                fallback_tray.messageClicked.disconnect()
            fallback_tray.messageClicked.connect(on_click)


def _try_winsdk_toast(
    title: str, body: str, on_click: Callable[[], None] | None,
) -> bool:
    """Attempt the WinRT toast path. Returns True if displayed, False otherwise."""
    try:
        from winsdk.windows.data.xml.dom import XmlDocument
        from winsdk.windows.ui.notifications import (
            ToastNotification,
            ToastNotificationManager,
        )
    except ImportError:
        return False

    xml = (
        "<toast>"
        "<visual>"
        "<binding template=\"ToastGeneric\">"
        f"<text>{_xml_escape(title)}</text>"
        f"<text>{_xml_escape(body)}</text>"
        "</binding>"
        "</visual>"
        "</toast>"
    )

    try:
        doc = XmlDocument()
        doc.load_xml(xml)
        notifier = ToastNotificationManager.create_toast_notifier("TeamsTranscriber")
        if notifier is None:
            return False
        notification = ToastNotification(doc)
        if on_click is not None:
            notification.add_activated(lambda *_args: on_click())
        notifier.show(notification)
        return True
    except Exception:
        logger.exception("winsdk toast failed; falling back")
        return False


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&apos;")
    )
