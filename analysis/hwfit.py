"""Fit leakage against operand Hamming weight.

    ./venv/bin/python3 -m analysis.hwfit results/<run_id> [more dirs...]

`analysis.aggregate` reports each condition contrast on its own. A Hamming
weight sweep wants the contrasts read together, as a slope: how many
milliwatts does one more set bit per 32-bit operand word cost?

Two things this deliberately does not do. It does not fit across all runs
pooled, because runs from the same repeat share a thermal state and would be
counted as independent evidence; instead one slope is fitted per repeat and
the spread across repeats is the error bar, which is the same rule
`analysis.aggregate` applies to single effects. And it does not average over
the patterns at one Hamming weight before fitting -- the patterns are reported
side by side first, because a real difference between two operands of equal
weight would mean the leakage is not a function of Hamming weight at all.
"""
import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np

from .load import load_results
from .stats import block_bootstrap_ci


def figure(points, slope, intercept, out_path):
    """Per-repeat effects against Hamming weight, with the fitted line."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    hw = np.array([p["hw"] for p in points], float)
    y = np.array([p["diff"] for p in points], float)

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.scatter(hw, y, s=26, alpha=0.75, label="per-repeat effect", zorder=3)
    by_hw = defaultdict(list)
    for h, d in zip(hw, y):
        by_hw[h].append(d)
    xs = np.array(sorted(by_hw))
    ax.plot(xs, [np.mean(by_hw[x]) for x in xs], "o-", ms=7, lw=1.4,
            label="mean per Hamming weight", zorder=4)
    grid = np.linspace(0, hw.max(), 128)
    ax.plot(grid, intercept + slope * grid, "--", lw=1.2,
            label=f"fit: {1000 * slope:.1f} mW/bit, intercept {1000 * intercept:+.0f} mW")
    ax.axhline(0.0, color="k", lw=0.8, alpha=0.4)
    ax.set_xlabel("operand Hamming weight (bits set per 32-bit word)")
    ax.set_ylabel("delta package power vs all-zero operand (W)")
    ax.set_title("Leakage against operand Hamming weight")
    ax.grid(alpha=0.25)
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def popcount(x):
    return bin(int(x)).count("1")


def contrast(run, n_boot):
    """Effect of this run's condition 1 over condition 0, with its HW step."""
    conds = sorted(set(run.cond.tolist()))
    if len(conds) < 2:
        return None
    a, b = conds[0], conds[1]
    ci = block_bootstrap_ci(run.power_w, run.block, run.cond, a, b, n_boot=n_boot)
    return {
        "label": run.label,
        "repeat": int(run.meta.get("repeat", 0)),
        "hw": popcount(run.selectors[b]) - popcount(run.selectors[a]),
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
    ap.add_argument("--no-figure", action="store_true")
    args = ap.parse_args()

    points = []
    for d in args.results_dirs:
        _, runs = load_results(Path(d))
        for run in runs:
            c = contrast(run, args.bootstrap)
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

    print(f"{'label':<12} {'HW':>3} {'operand':>12} {'n':>2} {'mean dW':>9} "
          f"{'between':>9} {'within':>9} {'mW/bit':>8}")
    print("-" * 78)
    for label in sorted(by_label, key=lambda l: (by_label[l][0]["hw"], l)):
        ps = by_label[label]
        diffs = np.array([p["diff"] for p in ps])
        hw = ps[0]["hw"]
        between = float(diffs.std(ddof=1)) if len(diffs) > 1 else float("nan")
        within = float(np.mean([p["half_width"] for p in ps]))
        per_bit = 1000.0 * diffs.mean() / hw if hw else float("nan")
        print(f"{label:<12} {hw:>3} {ps[0]['selector']:>12} {len(diffs):>2} "
              f"{diffs.mean():>+9.4f} {between:>9.4f} {within:>9.4f} {per_bit:>8.2f}")

    if aa:
        d = np.array([p["diff"] for p in aa])
        print(f"{'(A/A)':<12} {0:>3} {aa[0]['selector']:>12} {len(d):>2} "
              f"{d.mean():>+9.4f} {d.std(ddof=1) if len(d) > 1 else float('nan'):>9.4f}")

    # ---- does bit placement matter at fixed HW? ---------------------------
    by_hw = defaultdict(list)
    for label, ps in by_label.items():
        by_hw[ps[0]["hw"]].append((label, np.array([p["diff"] for p in ps])))

    multi = {hw: v for hw, v in by_hw.items() if len(v) > 1}
    if multi:
        print("\nbit placement at fixed Hamming weight")
        print(f"{'HW':>3}  {'patterns':<28} {'spread W':>9} {'between W':>10}  verdict")
        for hw in sorted(multi):
            entries = multi[hw]
            means = np.array([d.mean() for _, d in entries])
            spread = float(np.ptp(means))
            between = float(np.mean([d.std(ddof=1) for _, d in entries if len(d) > 1]))
            names = ", ".join(n for n, _ in entries)
            verdict = ("within run-to-run noise" if not np.isfinite(between)
                       or spread <= 2 * between else "LARGER than run-to-run noise")
            print(f"{hw:>3}  {names:<28} {spread:>9.4f} {between:>10.4f}  {verdict}")

    # ---- slope, one fit per repeat ----------------------------------------
    by_repeat = defaultdict(list)
    for p in fit_pts:
        by_repeat[p["repeat"]].append(p)

    slopes, r2s = [], []
    print("\nper-repeat fit of dP = a + b*HW")
    print(f"{'repeat':>6} {'n':>3} {'b (mW/bit)':>11} {'a (mW)':>9} {'R^2':>7}")
    for rep in sorted(by_repeat):
        ps = by_repeat[rep]
        b, a, r2 = ols([p["hw"] for p in ps], [p["diff"] for p in ps])
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
        hw_max = max(p["hw"] for p in fit_pts)
        print(f"          i.e. {1000 * s.mean() * hw_max:+.0f} mW over the full "
              f"0 -> {hw_max} bit range")
        bps = [p["bytes_per_s"] for p in fit_pts if np.isfinite(p["bytes_per_s"])]
        if bps:
            gbs = float(np.mean(bps)) / 1e9
            print(f"          {1000 * s.mean() / gbs:+.4f} mW/bit per GB/s at "
                  f"{gbs:.0f} GB/s aggregate operand throughput")

    if slopes and not args.no_figure:
        b, a, _ = ols([p["hw"] for p in fit_pts], [p["diff"] for p in fit_pts])
        out = Path(args.results_dirs[0]) / "figures"
        out.mkdir(exist_ok=True)
        figure(fit_pts, b, a, out / "hamming-weight-fit.png")
        print(f"\nfigure -> {out / 'hamming-weight-fit.png'}")

    print("\nRead the slope against the A/A row, not against zero. Bit-placement")
    print("spread larger than the between-repeat SD would mean the leakage")
    print("depends on which bits are set, not on how many.")


if __name__ == "__main__":
    main()
