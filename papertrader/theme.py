"""Theme: follows the Serpantinum/Matugen palette by reading kitty's generated colors."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

KITTY_COLORS = os.path.expanduser("~/.config/kitty/colors.conf")
SERP_SETTINGS = os.path.expanduser("~/.config/serpantinum/settings.json")


def _mix(a: str, b: str, t: float) -> str:
    a, b = a.lstrip("#"), b.lstrip("#")
    ca = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    cb = [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


@dataclass
class Palette:
    bg: str = "#202018"
    surface: str = "#14140c"
    card: str = "#2a2a21"
    card_hi: str = "#33332a"
    outline: str = "#47473b"
    fg: str = "#e5e3d6"
    muted: str = "#c9c7b6"
    faint: str = "#929182"
    primary: str = "#c7cc79"
    on_primary: str = "#303300"
    primary_container: str = "#464a02"
    on_primary_container: str = "#e3e892"
    tertiary: str = "#a3d0c0"
    error: str = "#ffb4ab"
    error_container: str = "#93000a"
    green: str = "#7fd38a"
    on_green: str = "#0d2a12"
    red: str = "#ff8a80"
    on_red: str = "#3b0905"
    radius: int = 10
    font: str = "Adwaita Sans"
    mono: str = "Adwaita Mono"


def load_palette() -> Palette:
    p = Palette()
    try:
        raw = open(KITTY_COLORS).read()
        kv = dict(re.findall(r"^\s*(\w+)\s+(#[0-9a-fA-F]{6})", raw, re.M))
        p.fg = kv.get("foreground", p.fg)
        p.bg = kv.get("background", p.bg)
        p.surface = kv.get("tab_bar_background", p.surface)
        p.outline = kv.get("inactive_border_color", kv.get("color0", p.outline))
        p.muted = kv.get("color7", p.muted)
        p.faint = kv.get("color8", p.faint)
        p.primary = kv.get("color2", kv.get("cursor", p.primary))
        p.on_primary = kv.get("cursor_text_color", p.on_primary)
        p.primary_container = kv.get("active_tab_background", p.primary_container)
        p.on_primary_container = kv.get("active_tab_foreground", p.on_primary_container)
        p.tertiary = kv.get("color3", p.tertiary)
        p.error = kv.get("color1", p.error)
        p.error_container = kv.get("color9", p.error_container)
        p.card = _mix(p.bg, p.fg, 0.05)
        p.card_hi = _mix(p.bg, p.fg, 0.09)
        # Keep "confirm" green and "view" red recognisable but tinted to the palette.
        p.green = _mix("#6fd17d", p.primary, 0.25)
        p.red = _mix("#ff7b72", p.error, 0.35)
    except OSError:
        pass
    try:
        import json
        s = json.load(open(SERP_SETTINGS)).get("theme", {})
        p.radius = int(s.get("borderRadius", p.radius))
        if s.get("fontFamily"):
            p.mono = s["fontFamily"]
    except Exception:
        pass
    return p


P = load_palette()


def mix(a: str, b: str, t: float) -> str:
    return _mix(a, b, t)


def stylesheet(p: Palette = P) -> str:
    r = p.radius
    return f"""
* {{ font-family: "{p.font}", "Google Sans", sans-serif; color: {p.fg}; }}
QMainWindow, QWidget#root {{ background: {p.bg}; }}
QToolTip {{ background: {p.card_hi}; color: {p.fg}; border: 1px solid {p.outline}; border-radius: 6px; padding: 6px; }}
QLabel[role="h1"] {{ font-size: 26px; font-weight: 700; }}
QLabel[role="h2"] {{ font-size: 18px; font-weight: 650; }}
QLabel[role="muted"] {{ color: {p.faint}; }}
QLabel[role="mono"] {{ font-family: "{p.mono}", monospace; }}
QFrame#card {{ background: {p.card}; border: 1px solid {mix(p.outline, p.bg, 0.4)}; border-radius: {r + 4}px; }}
QFrame#sidebar {{ background: {p.surface}; border-right: 1px solid {mix(p.outline, p.bg, 0.5)}; }}
QFrame#header {{ background: transparent; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.outline}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p.faint}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ height: 0; }}
QProgressBar {{ background: {p.card_hi}; border: none; border-radius: 4px; height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {p.primary}; border-radius: 4px; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
  background: {p.card_hi}; border: 1px solid {p.outline}; border-radius: {r}px; padding: 6px 10px; selection-background-color: {p.primary_container};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {p.primary}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; }}
QTabBar::tab {{ background: transparent; color: {p.faint}; padding: 8px 16px; border: none; font-weight: 600; }}
QTabBar::tab:selected {{ color: {p.primary}; border-bottom: 2px solid {p.primary}; }}
QTabWidget::pane {{ border: none; }}
QTextBrowser {{ background: {p.card_hi}; border: 1px solid {p.outline}; border-radius: {r}px; padding: 8px; }}
QDialog {{ background: {p.bg}; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {p.outline}; background: {p.card_hi}; }}
QCheckBox::indicator:checked {{ background: {p.primary}; border: 1px solid {p.primary}; }}
"""
