"""Color palette, spacing/radius tokens, and the app QSS stylesheet."""

from __future__ import annotations

COLORS: dict[str, str] = {
    # Backgrounds
    "bg":             "#F2EFE9",   # warm off-white
    "card":           "#FFFFFF",
    "card_alt":       "#FAF8F4",
    "hover":          "#F6F4EE",
    "selected":       "#FFFFFF",

    # Text
    "text_primary":   "#1F2937",
    "text_secondary": "#6B7280",
    "text_tertiary":  "#9CA3AF",
    "text_on_accent": "#FFFFFF",

    # Borders / dividers
    "border":         "#E5E7EB",
    "border_soft":    "#EEEAE3",
    "shadow":         "#0F000000",  # rgba(0,0,0,0.06) — Qt #AARRGGBB

    # Accents
    "accent":         "#10B981",
    "accent_hover":   "#059669",
    "accent_active":  "#047857",
    "accent_soft":    "#D1FAE5",

    "amber":          "#F59E0B",
    "amber_soft":     "#FEF3C7",
    "red":            "#EF4444",
    "red_soft":       "#FEE2E2",
}

SPACING: dict[str, int] = {
    "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32,
}

RADIUS: dict[str, int] = {
    "window": 16, "card": 12, "button": 8, "input": 8, "pill": 999,
}

FONT_FAMILY: str = "Segoe UI Variable, Segoe UI, Inter, sans-serif"
FONT_SIZE_BASE: int = 13


def app_stylesheet() -> str:
    """Global QSS applied to QApplication. Built from the palette dict above."""
    c = COLORS
    r = RADIUS
    s = SPACING
    return f"""
    QWidget {{
        background: {c['bg']};
        color: {c['text_primary']};
        font-family: {FONT_FAMILY};
        font-size: {FONT_SIZE_BASE}px;
    }}

    QMainWindow, QDialog {{
        background: {c['bg']};
    }}

    /* Text-only widgets inherit visually from their parent (no gray box around labels). */
    QLabel, QCheckBox, QRadioButton {{
        background: transparent;
    }}

    QFrame[card="true"] {{
        background: {c['card']};
        border-radius: {r['card']}px;
        border: 1px solid {c['border_soft']};
    }}
    QFrame[card="true"][selected="true"] {{
        border: 2px solid {c['accent']};
    }}

    QFrame[role="sidebar"] {{
        background: {c['card_alt']};
        border: none;
    }}

    QPushButton[sidebar_item="true"] {{
        text-align: left;
        padding: {s['sm']}px {s['md']}px;
        border-radius: {r['button']}px;
        background: transparent;
        color: {c['text_secondary']};
        font-weight: 500;
        border: none;
    }}
    QPushButton[sidebar_item="true"]:hover {{ background: {c['hover']}; }}
    QPushButton[sidebar_item="true"][active="true"] {{
        background: {c['card']};
        color: {c['text_primary']};
    }}

    QPushButton[role="primary"] {{
        background: {c['accent']};
        color: {c['text_on_accent']};
        border-radius: {r['button']}px;
        padding: {s['sm']}px {s['lg']}px;
        font-weight: 600;
        border: none;
    }}
    QPushButton[role="primary"]:hover  {{ background: {c['accent_hover']}; }}
    QPushButton[role="primary"]:pressed {{ background: {c['accent_active']}; }}

    QPushButton[role="secondary"] {{
        background: {c['card']};
        color: {c['text_primary']};
        border-radius: {r['button']}px;
        padding: {s['sm']}px {s['lg']}px;
        font-weight: 500;
        border: 1px solid {c['border']};
    }}
    QPushButton[role="secondary"]:hover {{ background: {c['hover']}; }}

    QPushButton[role="ghost"] {{
        background: transparent;
        color: {c['text_secondary']};
        border-radius: {r['button']}px;
        padding: {s['xs']}px {s['sm']}px;
        border: none;
    }}
    QPushButton[role="ghost"]:hover {{
        background: {c['hover']};
        color: {c['text_primary']};
    }}

    QLineEdit, QTextEdit, QPlainTextEdit {{
        background: {c['card']};
        color: {c['text_primary']};
        border: 1px solid {c['border']};
        border-radius: {r['input']}px;
        padding: {s['sm']}px {s['md']}px;
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border: 1px solid {c['accent']};
    }}

    QLineEdit[role="search"] {{
        background: {c['card']};
        border: 1px solid {c['border_soft']};
        padding-left: {s['xl']}px;
    }}

    QLabel[role="title"]    {{ font-size: 20px; font-weight: 600; color: {c['text_primary']}; }}
    QLabel[role="subtitle"] {{ font-size: 14px; font-weight: 500; color: {c['text_secondary']}; }}
    QLabel[role="muted"]    {{ color: {c['text_secondary']}; }}
    QLabel[role="hint"]     {{ color: {c['text_tertiary']}; font-size: 12px; }}

    QLabel[role="chip"] {{
        background: {c['card_alt']};
        color: {c['text_secondary']};
        border: 1px solid {c['border_soft']};
        border-radius: {r['pill']}px;
        padding: 2px {s['md']}px;
        font-size: 12px;
    }}
    QLabel[role="chip"][variant="success"] {{
        background: {c['accent_soft']};
        color: {c['accent_active']};
        border-color: {c['accent_soft']};
    }}
    QLabel[role="chip"][variant="warn"] {{
        background: {c['amber_soft']};
        color: #92400E;
        border-color: {c['amber_soft']};
    }}
    QLabel[role="chip"][variant="error"] {{
        background: {c['red_soft']};
        color: #991B1B;
        border-color: {c['red_soft']};
    }}

    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {c['border']}; border-radius: 5px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {c['text_tertiary']};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

    QMenu {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: {r['card']}px;
        padding: {s['xs']}px;
    }}
    QMenu::item {{
        padding: {s['sm']}px {s['md']}px;
        border-radius: {r['button']}px;
        color: {c['text_primary']};
    }}
    QMenu::item:selected {{ background: {c['hover']}; }}
    QMenu::separator {{
        height: 1px; background: {c['border_soft']};
        margin: {s['xs']}px {s['sm']}px;
    }}
    """
