"""Sealed-box prediction for the 40 mm speaker case.

The chamber volume is READ FROM THE MESH (export/cavity.stl) rather than
hardcoded, so this can never drift out of step with the model.  Everything
else is a sweep over plausible driver parameters, because Fs is documented
for this driver class but Vas and Qts are not.

Model: the box raises the driver's free-air resonance and its total Q by the
same factor sqrt(1 + Vas/Vb), giving a 2nd-order (12 dB/oct) high-pass with
resonance fc and quality factor Qtc:

    |H(f)| = (f/fc)^2 / sqrt( (1-(f/fc)^2)^2 + ((f/fc)/Qtc)^2 )

normalised to 1.0 in the pass band, i.e. exactly what a driver with a flat
piston response would do in this box.

usage:  python acoustics.py <export-dir> [out.png]
"""
import os
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mesh_check import read_stl, stats  # noqa: E402

DAMPING_GAIN = 1.20     # loose polyfill ~ +20 % effective volume

SCENARIOS = [
    # label,                 Fs,  Qts,  Vas cm3
    ("optimistic",           400, 0.80,  15),
    ("likely",               400, 0.90,  30),
    ("typical",              450, 1.00,  45),
    ("pessimistic",          580, 1.10,  90),
    ("worst case",           580, 1.20, 200),
]


def cavity_volume_cm3(export_dir):
    """Measured net trapped air volume, from the cavity mesh."""
    path = os.path.join(export_dir, "cavity.stl")
    if not os.path.exists(path):
        raise SystemExit(
            f"cavity.stl not found in {export_dir}\n"
            "Export it with render_all.ps1 (part 'cavity').")
    return stats(read_stl(path))["volume"] / 1000.0


def response(f, fc, qtc):
    x = f / fc
    return 20 * np.log10(x ** 2 / np.sqrt((1 - x ** 2) ** 2 + (x / qtc) ** 2))


def metrics(fs, qts, vas, vb):
    alpha = vas / vb
    k = np.sqrt(1 + alpha)
    fc, qtc = fs * k, qts * k
    f = np.logspace(np.log10(20), np.log10(20000), 4000)
    db = response(f, fc, qtc)
    # F3: highest frequency still at or below -3 dB (the crossing on the way
    # up out of the stop band).
    below = np.where(db <= -3.0)[0]
    f3 = np.nan
    if len(below) and below[-1] + 1 < len(f):
        i = below[-1]
        x0, x1 = np.log10(f[i]), np.log10(f[i + 1])
        y0, y1 = db[i], db[i + 1]
        t = (-3.0 - y0) / (y1 - y0) if y1 != y0 else 0.0
        f3 = 10 ** (x0 + t * (x1 - x0))
    i = int(np.argmax(db))
    return dict(alpha=alpha, fc=fc, qtc=qtc, f3=f3, peak_db=db[i], peak_f=f[i],
                f=f, db=db)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    export_dir = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        export_dir, "acoustic_prediction.png")

    VB_CM3 = cavity_volume_cm3(export_dir)
    print(f"net air volume Vb = {VB_CM3:.2f} cm3  (measured from cavity.stl)"
          f"   (polyfill -> {VB_CM3 * DAMPING_GAIN:.2f} cm3 effective)\n")
    hdr = (f"{'scenario':<13}{'Fs':>6}{'Qts':>6}{'Vas':>7}"
           f"{'alpha':>8}{'Fc':>8}{'Qtc':>7}{'peak':>8}{'F3':>8}")
    print(hdr)
    print("-" * len(hdr))

    rows = []
    for label, fs, qts, vas in SCENARIOS:
        m = metrics(fs, qts, vas, VB_CM3)
        md = metrics(fs, qts, vas, VB_CM3 * DAMPING_GAIN)
        rows.append((label, fs, qts, vas, m, md))
        print(f"{label:<13}{fs:>6}{qts:>6.2f}{vas:>7}"
              f"{m['alpha']:>8.2f}{m['fc']:>8.0f}{m['qtc']:>7.2f}"
              f"{m['peak_db']:>+7.1f}dB{m['f3']:>8.0f}")
    print("\n(with loose polyfill fill, Vb x1.20)")
    for label, fs, qts, vas, m, md in rows:
        print(f"{label:<13}{'':>6}{'':>6}{'':>7}{md['alpha']:>8.2f}"
              f"{md['fc']:>8.0f}{md['qtc']:>7.2f}"
              f"{md['peak_db']:>+7.1f}dB{md['f3']:>8.0f}")

    fig, ax = plt.subplots(figsize=(11.5, 6.6), dpi=115)
    fig.patch.set_facecolor("#15171b")
    ax.set_facecolor("#15171b")
    colors = ["#57c7ff", "#8ee6a0", "#ffd166", "#ff9f6e", "#ff6b81"]
    for (label, fs, qts, vas, m, md), c in zip(rows, colors):
        ax.semilogx(m["f"], m["db"], color=c, lw=2.0,
                    label=f"{label}: Fs={fs} Qts={qts:.2f} Vas={vas} cm³ "
                          f"→ Fc={m['fc']:.0f} Hz, Qtc={m['qtc']:.2f}")
        ax.semilogx(md["f"], md["db"], color=c, lw=1.1, ls=(0, (5, 3)), alpha=0.75)
    ax.axhline(-3, color="#7f8c99", lw=1.0, ls=":")
    ax.text(22, -3.6, "-3 dB", color="#7f8c99", fontsize=9)
    ax.set_xlim(20, 20000)
    ax.set_ylim(-30, 9)
    ax.set_xlabel("frequency (Hz)", color="#c9d5e0")
    ax.set_ylabel("relative SPL (dB)", color="#c9d5e0")
    ax.set_title(f"Predicted sealed-box response — net volume {VB_CM3:.1f} cm³"
                 f"   (dashed = with loose polyfill, +20 % effective volume)",
                 color="#e6edf3", fontsize=12)
    ax.grid(True, which="both", color="#2a3038", lw=0.6)
    ax.tick_params(colors="#8b98a5")
    for s in ax.spines.values():
        s.set_color("#39424d")
    leg = ax.legend(fontsize=9, facecolor="#1c2026", edgecolor="#39424d",
                    loc="lower right")
    for t in leg.get_texts():
        t.set_color("#c9d5e0")
    fig.tight_layout()
    fig.savefig(out, facecolor=fig.get_facecolor())
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
