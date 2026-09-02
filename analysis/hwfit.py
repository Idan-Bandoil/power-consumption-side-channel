"""Fit leakage against operand Hamming weight, or against Hamming distance.

    ./venv/bin/python3 -m analysis.hwfit results/<run_id> [more dirs...]
    ./venv/bin/python3 -m analysis.hwfit --axis hd --labels 'hd*' results/<id>

`analysis.aggregate` reports each condition contrast on its own. A sweep wants
the contrasts read together, as a slope: how many milliwatts does one more set
bit per 32-bit operand word cost?

Two axes, same statistics. `--axis hw` reads the selector as one operand word
and fits against its Hamming weight. `--axis hd` reads it as the two 32-bit
words a ws_*_ab victim alternates between, and fits against the number of bits
that flip between them -- switching activity per transfer, which is what
classical DPA predicts leakage tracks. Since a ws_*_ab sweep holds Hamming
weight equal across both words, the two axes are separated by construction and
the fitted mW/bit are directly comparable: cost of a set bit against cost of a
flipped one.

`--labels` restricts the fit to runs whose label matches a glob, which a
mixed session needs: an anchor run tying the session to earlier ones sits at
distance 0 with a large weight effect, and would otherwise be fitted as if it
were a point on the distance axis. A/A controls are always kept, since they
are reported separately rather than fitted.

Two things this deliberately does not do. It does not fit across all runs
pooled, because runs from the same repeat share a thermal state and would be
counted as independent evidence; instead one slope is fitted per repeat and
the spread across repeats is the error bar, which is the same rule
`analysis.aggregate` applies to single effects. And it does not average over
the patterns at one Hamming weight before fitting -- the patterns are reported
side by side first, because a real difference between two operands sitting at
the same point on the axis would mean the leakage is not a function of that
axis at all.
"""
import argparse
from collections import defaultdict
from fnmatch import fnmatch
from pathlib import Path

import numpy as np

from .load import load_results
from .stats import block_bootstrap_ci


def figure(points, slope, intercept, axis, out_path):
    """Per-repeat effects against the swept axis, with the fitted line."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x = np.array([p["x"] for p in points], float)
    y = np.array([p["diff"] for p in points], float)

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.scatter(x, y, s=26, alpha=0.75, label="per-repeat effect", zorder=3)
    by_x = defaultdict(list)
    for h, d in zip(x, y):
        by_x[h].append(d)
    xs = np.array(sorted(by_x))
    ax.plot(xs, [np.mean(by_x[v]) for v in xs], "o-", ms=7, lw=1.4,
            label=f"mean per {axis.short}", zorder=4)
    grid = np.linspace(0, x.max(), 128)
    ax.plot(grid, intercept + slope * grid, "--", lw=1.2,
            label=f"fit: {1000 * slope:.1f} mW/bit, intercept {1000 * intercept:+.0f} mW")
    ax.axhline(0.0, color="k", lw=0.8, alpha=0.4)
    ax.set_xlabel(axis.xlabel)
    ax.set_ylabel(f"delta package power vs {axis.baseline} (W)")
    ax.set_title(f"Leakage against operand {axis.short}")
    ax.grid(alpha=0.25)
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def popcount(x):
    return bin(int(x)).count("1")


class Axis:
    """What to plot the effect against, and how to read it off a selector."""

    def __init__(self, key, short, xlabel, baseline, value):
        self.key = key
        self.short = short
        self.xlabel = xlabel
        self.baseline = baseline
        self.value = value


AXES = {
    # The whole selector is one operand word; the ws_*_ab high half is unused.
    "hw": Axis("hw", "Hamming weight",
               "operand Hamming weight (bits set per 32-bit word)",
               "the all-zero operand",
               lambda x: popcount(x)),
    # The two 32-bit halves are the words a ws_*_ab victim alternates between,
    # so their XOR is the number of bits that flip on every 32-byte transfer.
    "hd": Axis("hd", "Hamming distance",
               "Hamming distance between alternating words (bits flipped per transfer)",
               "the constant-word baseline",
               lambda x: popcount((x & 0xFFFFFFFF) ^ (x >> 32))),
}


def contrast(run, n_boot, axis):
    """Effect of this run's condition 1 over condition 0, with its axis step."""
    conds = sorted(set(run.cond.tolist()))
    if len(conds) < 2:
        return None
    a, b = conds[0], conds[1]
    ci = block_bootstrap_ci(run.power_w, run.block, run.cond, a, b, n_boot=n_boot)
    return {
        "label": run.label,
        "repeat": int(run.meta.get("repeat", 0)),
        "x": axis.value(int(run.selectors[b])) - axis.value(int(run.selectors[a])),
        "selector": int(run.selectors[b]),
        "diff": ci["diff"],
        "half_width": (ci["hi"] - ci["lo"]) / 2.0,
        "is_aa": run.is_aa_control,
        "bytes_per_s": run.bytes_per_s,
    }


def ols(x, y):
    """Slope, intercept and R^2. Returns nan slope for a degenerate x."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if len(x) < 2 or np.ptp(x) == 0:
        return float("nan"), float("nan"), float("nan")
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return float(slope), float(intercept), r2


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dirs", nargs="+")
    ap.add_argument("--bootstrap", type=int, default=4000)
    ap.add_argument("--axis", choices=sorted(AXES), default="hw",
                    help="fit against Hamming weight (default) or Hamming distance")
    ap.add_argument("--labels", default=None,
                    help="glob restricting which runs are fitted; A/A controls "
                         "are kept regardless")
    ap.add_argument("--no-figure", action="store_true")
    args = ap.parse_args()

    axis = AXES[args.axis]
    points = []
    for d in args.results_dirs:
        _, runs = load_results(Path(d))
        for run in runs:
            if (args.labels and not run.is_aa_control
                    and not fnmatch(run.label, args.labels)):
                continue
            c = contrast(run, args.bootstrap, axis)
            if c:
                points.append(c)

    if not points:
        raise SystemExit("no usable runs")

    aa = [p for p in points if p["is_aa"]]
    fit_pts = [p for p in points if not p["is_aa"]]

    # ---- per operand, one row per label -----------------------------------
    by_label = defaultdict(list)
    for p in fit_pts:
        by_label[p["label"]].append(p)

    axis_col = axis.key.upper()
    print(f"{'label':<12} {axis_col:>3} {'operand':>21} {'n':>2} {'mean dW':>9} "
          f"{'between':>9} {'within':>9} {'mW/bit':>8}")
    print("-" * 87)
    for label in sorted(by_label, key=lambda l: (by_label[l][0]["x"], l)):
        ps = by_label[label]
        diffs = np.array([p["diff"] for p in ps])
        x = ps[0]["x"]
        between = float(diffs.std(ddof=1)) if len(diffs) > 1 else float("nan")
        within = float(np.mean([p["half_width"] for p in ps]))
        per_bit = 1000.0 * diffs.mean() / x if x else float("nan")
        print(f"{label:<12} {x:>3} {ps[0]['selector']:>21} {len(diffs):>2} "
              f"{diffs.mean():>+9.4f} {between:>9.4f} {within:>9.4f} {per_bit:>8.2f}")

    if aa:
        d = np.array([p["diff"] for p in aa])
        print(f"{'(A/A)':<12} {0:>3} {aa[0]['selector']:>21} {len(d):>2} "
              f"{d.mean():>+9.4f} {d.std(ddof=1) if len(d) > 1 else float('nan'):>9.4f}")

    # ---- does bit placement matter at a fixed point on the axis? ----------
    by_x = defaultdict(list)
    for label, ps in by_label.items():
        by_x[ps[0]["x"]].append((label, np.array([p["diff"] for p in ps])))

    multi = {x: v for x, v in by_x.items() if len(v) > 1}
    if multi:
        print(f"\nbit placement at fixed {axis.short}")
        print(f"{axis_col:>3}  {'patterns':<28} {'spread W':>9} {'between W':>10}  verdict")
        for x in sorted(multi):
            entries = multi[x]
            means = np.array([d.mean() for _, d in entries])
            spread = float(np.ptp(means))
            between = float(np.mean([d.std(ddof=1) for _, d in entries if len(d) > 1]))
            names = ", ".join(n for n, _ in entries)
            verdict = ("within run-to-run noise" if not np.isfinite(between)
                       or spread <= 2 * between else "LARGER than run-to-run noise")
            print(f"{x:>3}  {names:<28} {spread:>9.4f} {between:>10.4f}  {verdict}")

    # ---- slope, one fit per repeat ----------------------------------------
    by_repeat = defaultdict(list)
    for p in fit_pts:
        by_repeat[p["repeat"]].append(p)

    slopes, r2s = [], []
    print(f"\nper-repeat fit of dP = a + b*{axis_col}")
    print(f"{'repeat':>6} {'n':>3} {'b (mW/bit)':>11} {'a (mW)':>9} {'R^2':>7}")
    for rep in sorted(by_repeat):
        ps = by_repeat[rep]
        b, a, r2 = ols([p["x"] for p in ps], [p["diff"] for p in ps])
        if np.isfinite(b):
            slopes.append(b)
            r2s.append(r2)
            print(f"{rep:>6} {len(ps):>3} {1000 * b:>11.2f} {1000 * a:>9.2f} {r2:>7.3f}")

    if slopes:
        s = np.array(slopes)
        sd = float(s.std(ddof=1)) if len(s) > 1 else float("nan")
        se = sd / np.sqrt(len(s)) if np.isfinite(sd) else float("nan")
        print(f"\nslope   : {1000 * s.mean():+.2f} mW/bit"
              + (f"  (SD {1000 * sd:.2f} over {len(s)} repeats,"
                 f" SE {1000 * se:.2f})" if np.isfinite(sd) else ""))
        if np.isfinite(se) and se > 0:
            print(f"          95% CI [{1000 * (s.mean() - 1.96 * se):+.2f},"
                  f" {1000 * (s.mean() + 1.96 * se):+.2f}] mW/bit")
        print(f"mean R^2: {np.mean(r2s):.3f}")
        x_max = max(p["x"] for p in fit_pts)
        print(f"          i.e. {1000 * s.mean() * x_max:+.0f} mW over the full "
              f"0 -> {x_max} bit range")
        bps = [p["bytes_per_s"] for p in fit_pts if np.isfinite(p["bytes_per_s"])]
        if bps:
            gbs = float(np.mean(bps)) / 1e9
            print(f"          {1000 * s.mean() / gbs:+.4f} mW/bit per GB/s at "
                  f"{gbs:.0f} GB/s aggregate operand throughput")

    if slopes and not args.no_figure:
        b, a, _ = ols([p["x"] for p in fit_pts], [p["diff"] for p in fit_pts])
        out = Path(args.results_dirs[0]) / "figures"
        out.mkdir(exist_ok=True)
        name = f"hamming-{'weight' if axis.key == 'hw' else 'distance'}-fit.png"
        figure(fit_pts, b, a, axis, out / name)
        print(f"\nfigure -> {out / name}")

    print("\nRead the slope against the A/A row, not against zero. Spread across")
    print(f"patterns at one {axis.short} larger than the between-repeat SD would")
    print("mean the leakage depends on which bits, not on how many.")


if __name__ == "__main__":
    main()
