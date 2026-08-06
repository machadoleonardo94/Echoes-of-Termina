"""
Echoes — entry point.

Double-click  Echoes.pyw  (or run  python main.py).
Dependencies are installed automatically on first run.
"""
import os
import sys

# Make 'core' and 'ui' importable regardless of working directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── First-run dependency bootstrap ───────────────────────────────────────────
# Runs before any PySide6 import.  No-op when PySide6 is already present.
# On first run: installs from requirements.txt, then re-launches this process
# so the newly installed packages are available.

import subprocess as _sp

def _ensure_deps() -> None:
    try:
        import PySide6   # noqa: F401
        return           # fast path
    except ImportError:
        pass

    _req = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
    _kw  = ({"creationflags": _sp.CREATE_NO_WINDOW}
            if sys.platform == "win32" else {})

    print("[Echoes] First run — installing dependencies…")
    rc = _sp.run(
        [sys.executable, "-m", "pip", "install", "--user", "-r", _req],
        check=False, **_kw
    ).returncode

    if rc != 0:
        _err("Echoes — setup failed",
             "Could not install PySide6 automatically.\n\n"
             "Run this once in a terminal and try again:\n\n"
             f"  python -m pip install --user -r\n  {_req}")
        sys.exit(1)

    # Re-launch so newly installed site-packages are on sys.path
    rc2 = _sp.run(
        [sys.executable, os.path.abspath(__file__)] + sys.argv[1:],
        check=False, **_kw
    ).returncode
    sys.exit(rc2)


def _err(title: str, msg: str) -> None:
    """Minimal error dialog using only stdlib (Qt not available yet)."""
    try:
        import tkinter as _tk
        import tkinter.messagebox as _mb
        _r = _tk.Tk()
        _r.withdraw()
        _mb.showerror(title, msg)
        _r.destroy()
    except Exception:
        print(f"[{title}]\n{msg}", file=sys.stderr)


_ensure_deps()
del _ensure_deps, _err, _sp
# ── End bootstrap ─────────────────────────────────────────────────────────────

from PySide6.QtGui     import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


# ── Dark palette (VS Code-inspired) ──────────────────────────────────────────

def _dark_palette() -> QPalette:
    p = QPalette()
    s = p.setColor

    BG     = QColor("#1e1e1e")
    BG2    = QColor("#252526")
    TEXT   = QColor("#d4d4d4")
    BTN    = QColor("#3c3c3c")
    ACCENT = QColor("#007acc")
    DIM    = QColor("#555555")
    WHITE  = QColor("#ffffff")

    s(QPalette.ColorRole.Window,          BG)
    s(QPalette.ColorRole.WindowText,      TEXT)
    s(QPalette.ColorRole.Base,            BG)
    s(QPalette.ColorRole.AlternateBase,   BG2)
    s(QPalette.ColorRole.Text,            TEXT)
    s(QPalette.ColorRole.BrightText,      WHITE)
    s(QPalette.ColorRole.Button,          BTN)
    s(QPalette.ColorRole.ButtonText,      TEXT)
    s(QPalette.ColorRole.Highlight,       ACCENT)
    s(QPalette.ColorRole.HighlightedText, WHITE)
    s(QPalette.ColorRole.Link,            QColor("#4fc1ff"))
    s(QPalette.ColorRole.PlaceholderText, DIM)
    s(QPalette.ColorRole.ToolTipBase,     BG2)
    s(QPalette.ColorRole.ToolTipText,     TEXT)

    s(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       DIM)
    s(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, DIM)

    return p


_STYLESHEET = """
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


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Echoes")
    app.setOrganizationName("lux-et-tenebris")
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())
    app.setStyleSheet(_STYLESHEET)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
