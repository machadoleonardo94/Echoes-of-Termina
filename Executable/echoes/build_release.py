"""
Build a distributable ZIP of the Echoes light-show editor.

Usage (from anywhere in the repo):
    python tools/echoes/build_release.py

Output: tools/echoes/dist/Echoes_v<VERSION>.zip

The ZIP extracts to a self-contained  Echoes/  folder.
Testers double-click  Echoes/Echoes.pyw  to launch.
Dependencies are installed automatically on first run (requires internet).
"""
from __future__ import annotations

import zipfile
from pathlib import Path

VERSION  = "0.4.0"
HERE     = Path(__file__).resolve().parent   # tools/echoes/
DIST_DIR = HERE / "dist"
ZIP_PATH = DIST_DIR / f"Echoes_v{VERSION}.zip"

# ── Include list (relative to HERE) ──────────────────────────────────────────
INCLUDE = [
    "Run Echoes.bat",
    "Echoes.pyw",
    "requirements.txt",
    "main.py",
    "core",
    "ui",
    "assets",
    "projects",
]

# ── Exclusion rules ───────────────────────────────────────────────────────────
SKIP_NAMES = {
    "__pycache__",
    "dist",
    # Development / reference DXF files not needed at runtime
    "LED_areas.dxf",
    "LED_order.dxf",
    "LED_order2.dxf",
    "LED_order2.svg",
    "areas_clean.dxf",      # old kokiri layout, superseded by echoes_outline.dxf
    # Leftover dev helpers
    "_dxf_inspect.py",
    "_dxf_zones.py",
}
SKIP_SUFFIXES = {
    ".pyc", ".pyo",
    # Audio — testers supply their own; never bundle third-party recordings
    ".mp3", ".wav", ".ogg", ".opus", ".flac", ".aac", ".m4a", ".wma",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _excluded(rel: Path) -> bool:
    """True if any component of *rel* should be skipped."""
    for part in rel.parts:
        if part in SKIP_NAMES or part.startswith("."):
            return True
    return rel.suffix in SKIP_SUFFIXES


def _add(zf: zipfile.ZipFile, src: Path, arc_root: str) -> int:
    """Recursively add *src* into the archive under *arc_root*. Returns file count."""
    count = 0
    if src.is_file():
        zf.write(src, arc_root)
        return 1
    for item in sorted(src.rglob("*")):
        rel = item.relative_to(src)
        if _excluded(rel):
            continue
        if item.is_file():
            zf.write(item, f"{arc_root}/{rel.as_posix()}")
            count += 1
    return count


# ── Main ──────────────────────────────────────────────────────────────────────

def build() -> None:
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Building Echoes v{VERSION}…\n")
    total = 0

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name in INCLUDE:
            src = HERE / name
            if not src.exists():
                print(f"  [skip] {name}  (not found)")
                continue
            arc = f"Echoes/{name}"
            n   = _add(zf, src, arc)
            label = f"{n} file{'s' if n != 1 else ''}"
            print(f"  [+]  {name:<28}  {label}")
            total += n

    size_kb = ZIP_PATH.stat().st_size / 1024
    print(f"\n{'─' * 48}")
    print(f"  Output : {ZIP_PATH}")
    print(f"  Files  : {total}")
    print(f"  Size   : {size_kb:.1f} KB")
    print(f"{'─' * 48}")
    print("\nTester instructions:")
    print("  1. Extract the ZIP anywhere")
    print("  2. Double-click  Echoes/Run Echoes.bat")
    print("  3. Install Python 3.10+ if prompted (https://python.org)")
    print("  4. On first run the launcher installs dependencies automatically")


if __name__ == "__main__":
    # Quick import-time sanity check before packaging
    import importlib, sys as _sys
    _sys.path.insert(0, str(HERE))
    for _mod in ("core.script_engine", "core.audio_player",
                 "core.dxf_loader", "ui.transport_bar",
                 "ui.script_editor", "ui.led_canvas", "ui.main_window"):
        try:
            importlib.import_module(_mod)
        except Exception as exc:
            print(f"[ERROR] {_mod}: {exc}")
            print("Fix the error above before building a release.")
            raise SystemExit(1)
    print("Sanity check passed.\n")
    build()
