"""
Shared dark theme for Echoes windows.

Kept in one place so a window opened on its own — the spectrum analyser can be
launched straight from the command line, without the main window — looks
exactly like the rest of the editor.  ``apply_dark_theme(app)`` is the whole
public surface: style, palette and stylesheet in one call.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

# ── Palette colours (VS Code-inspired) ──────────────────────────────────────

BG     = "#1e1e1e"
BG2    = "#252526"
TEXT   = "#d4d4d4"
BTN    = "#3c3c3c"
ACCENT = "#007acc"
DIM    = "#555555"
WHITE  = "#ffffff"


def dark_palette() -> QPalette:
    """The application palette (VS Code-inspired dark)."""
    p = QPalette()
    s = p.setColor

    s(QPalette.ColorRole.Window,          QColor(BG))
    s(QPalette.ColorRole.WindowText,      QColor(TEXT))
    s(QPalette.ColorRole.Base,            QColor(BG))
    s(QPalette.ColorRole.AlternateBase,   QColor(BG2))
    s(QPalette.ColorRole.Text,            QColor(TEXT))
    s(QPalette.ColorRole.BrightText,      QColor(WHITE))
    s(QPalette.ColorRole.Button,          QColor(BTN))
    s(QPalette.ColorRole.ButtonText,      QColor(TEXT))
    s(QPalette.ColorRole.Highlight,       QColor(ACCENT))
    s(QPalette.ColorRole.HighlightedText, QColor(WHITE))
    s(QPalette.ColorRole.Link,            QColor("#4fc1ff"))
    s(QPalette.ColorRole.PlaceholderText, QColor(DIM))
    s(QPalette.ColorRole.ToolTipBase,     QColor(BG2))
    s(QPalette.ColorRole.ToolTipText,     QColor(TEXT))

    s(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor(DIM))
    s(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(DIM))

    return p


STYLESHEET = """
    QMenuBar            { background: #2d2d2d; }
    QMenuBar::item:selected          { background: #094771; }
    QMenu               { background: #252526; border: 1px solid #454545; }
    QMenu::item:selected             { background: #094771; }
    QStatusBar          { background: #007acc; color: #ffffff; font-size: 11px; padding: 0 6px; }
    QSplitter::handle   { background: #3e3e3e; }

    QToolBar {
        background: #2d2d2d;
        border-bottom: 1px solid #3a3a3a;
        spacing: 4px;
        padding: 2px 4px;
    }
    QToolBar::separator {
        background: #4a4a4a;
        width: 1px;
        margin: 4px 2px;
    }
    QToolButton {
        background: transparent;
        border: 1px solid transparent;
        border-radius: 4px;
        padding: 3px 8px;
        color: #cccccc;
        font-size: 12px;
    }
    QToolButton:hover   { background: #3e3e3e; border-color: #555555; }
    QToolButton:pressed { background: #094771; border-color: #007acc; }

    QPushButton {
        background: #3c3c3c; border: 1px solid #555555;
        border-radius: 4px; padding: 2px 8px;
    }
    QPushButton:hover   { background: #4a4a4a; }
    QPushButton:pressed { background: #007acc; border-color: #007acc; }

    QScrollBar:vertical             { background: #252526; width: 12px; border: none; }
    QScrollBar::handle:vertical     { background: #454545; border-radius: 4px; min-height: 20px; }
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical   { height: 0; }
    QScrollBar:horizontal           { background: #252526; height: 12px; border: none; }
    QScrollBar::handle:horizontal   { background: #454545; border-radius: 4px; min-width: 20px; }
    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal { width: 0; }

    QToolTip { background: #252526; color: #d4d4d4; border: 1px solid #555555; }
"""


def apply_dark_theme(app) -> None:
    """Apply the Fusion style, dark palette and stylesheet to *app*."""
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLESHEET)
