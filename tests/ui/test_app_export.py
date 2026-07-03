"""Tests for the _default_export_name helper in app.py."""

from __future__ import annotations


def test_export_default_name_uses_title_and_pdf(qapp):
    from teams_transcriber.ui.app import _default_export_name
    n = _default_export_name("Potter Sync", "2026-05-20T15:00:00+00:00")
    assert n.endswith(".pdf")
    assert "potter-sync" in n
