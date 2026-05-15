from __future__ import annotations

from PySide6.QtGui import QColor

from teams_transcriber.ui.theme import COLORS, RADIUS, SPACING, app_stylesheet


def test_palette_has_expected_keys() -> None:
    for key in ("bg", "card", "text_primary", "text_secondary", "text_tertiary",
                "border", "accent", "accent_hover", "accent_active", "amber",
                "red", "shadow"):
        assert key in COLORS, f"missing palette key {key}"
        assert QColor(COLORS[key]).isValid(), f"invalid color for {key}: {COLORS[key]}"


def test_spacing_and_radius_constants_are_ints() -> None:
    for v in SPACING.values():
        assert isinstance(v, int) and v >= 0
    for v in RADIUS.values():
        assert isinstance(v, int) and v >= 0


def test_app_stylesheet_returns_qss_string() -> None:
    qss = app_stylesheet()
    assert isinstance(qss, str)
    # Sanity: stylesheet references the bg color.
    assert COLORS["bg"] in qss
    assert COLORS["accent"] in qss
