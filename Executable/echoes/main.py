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

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.theme       import apply_dark_theme


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Echoes")
    app.setOrganizationName("lux-et-tenebris")
    apply_dark_theme(app)          # Fusion style + dark palette + stylesheet

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
