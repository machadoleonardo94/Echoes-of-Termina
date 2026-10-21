"""
Echoes — standalone FFT spectrum analyser.

    python spectrum.py [audio-file]

Opens the spectrum window on its own, without the editor: useful for looking
at a file's spectrum while the main window is closed, or for evaluating the
analyser in isolation.  The same window is available from the editor's
Tools menu, where it follows the main transport instead of its own.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from PySide6.QtWidgets import QApplication
except ImportError:                                   # pragma: no cover
    print("PySide6 is not installed — run  python main.py  once to set it up.",
          file=sys.stderr)
    sys.exit(1)

from ui.fft_spectrum_window import SpectrumWindow
from ui.theme               import apply_dark_theme


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Echoes")
    app.setOrganizationName("lux-et-tenebris")
    apply_dark_theme(app)

    win = SpectrumWindow()
    win.show()

    for arg in sys.argv[1:]:                # optional audio file to analyse
        if os.path.isfile(arg):
            win.load_file(arg)
            break

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
