"""
Echoes — double-click launcher (no console window).

Windows runs .pyw files with pythonw.exe automatically.

First-time setup (run once in a terminal):
    python -m pip install --user -r tools\echoes\requirements.txt
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)

from main import main
main()
