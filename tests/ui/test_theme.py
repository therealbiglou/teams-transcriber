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


def test_selected_card_border_keeps_width_constant() -> None:
    qss = app_stylesheet()
    # Selection must not change border width (1px→2px causes layout jiggle).
    assert 'QFrame[card="true"][selected="true"]' in qss
    assert "2px solid" not in qss.split('selected="true"')[1].split("}")[0]


def test_stylesheet_covers_all_stock_widgets_in_use() -> None:
    qss = app_stylesheet()
    for selector in (
        "QComboBox", "QTabBar::tab", "QSpinBox", "QListWidget",
        "QGroupBox", "QProgressBar::chunk", "QCheckBox::indicator",
        "QScrollBar:horizontal", "QToolTip",
    ):
        assert selector in qss, f"missing QSS for {selector}"


def test_base_push_button_rule_exists_before_role_rules() -> None:
    """Role-less QPushButtons (dialog OK/Cancel, wizard Back/Next, update
    Close) must get base theming instead of rendering native. The base rule
    must appear before the role-specific rules so attribute-selector rules
    (which are more specific anyway) still win by cascade + specificity."""
    qss = app_stylesheet()
    assert "QPushButton {" in qss
    base_idx = qss.index("QPushButton {")
    role_idx = qss.index('QPushButton[role="primary"]')
    assert base_idx < role_idx
