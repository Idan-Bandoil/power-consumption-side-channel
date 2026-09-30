"""Recover operand sparsity (density) from package power -- Phase 3 item 1/2.

    ./venv/bin/python3 -m analysis.sparsity results/<run_id> [more dirs...]

A phase3_sparsity run streams an L3-resident buffer filled to a selector-chosen
density: fraction f of the 32-bit words carry a nonzero pattern, the rest are
zero. Condition 0 is always density 0 (an all-zero stream) and condition 1 the
test density, interleaved, so every measurement is a drift-robust contrast
against a common baseline -- the same design the Hamming-weight sweep used.

This entry point answers three questions the Phase 1 slope tools do not:

  1. The leakage curve dP(density), aggregated over repeats. Because a word is
     either zero or the all-ones pattern, the *mean Hamming weight per word* is
     density x popcount(pattern), so the fitted slope in mW per mean-set-bit is
     directly comparable to Phase 1's +50.75 mW/bit -- a built-in cross-check
     that this is the same leakage one level up, not a new artifact. (A sparse
     stream also raises Hamming *distance* between consecutive words, which
     Phase 1 showed leaks too, so a slope somewhat above 50.75 is expected.)

  2. A sparsity classifier. Per-block power is baselined against each run's own
     density-0 condition, which removes the run's DC drift and leaves a per-block
     delta comparable across runs. A leave-one-repeat-out nearest-centroid
     classifier over those deltas gives the confusion matrix and accuracy the
     plan's verification section requires, and a level MAE in density points.
     Leaving out a whole repeat -- not random blocks -- keeps the run-level
     thermal state that the notes repeatedly warn is the real error bar out of
     the training fold. Accuracy is reported for a few observation lengths (k
     blocks averaged per decision), which is the sparsity analogue of the
     covert channel's accuracy-vs-n -> bit-rate curve.

  3. The compute channel, when a run set holds two victims (the VNNI experiment:
     ws_sparse_op_vnni against ws_sparse_op_mov). The load-movement channel is
     common to both, so the paired difference dP(vnni,d) - dP(mov,d) per repeat
     is activation sparsity seen through the int8 MAC array with movement
     differenced out -- read it against the between-repeat SD.

Gates are not re-enforced here; run `analysis.report` per run for those. This
tool assumes the runs it is given already passed them.
"""
import argparse
from collections import defaultdict
from fnmatch import fnmatch
from pathlib import Path

import numpy as np

from .load import load_results
from .stats import block_means, block_bootstrap_ci, accuracy_vs_n


def popcount(x):
    return bin(int(x) & 0xFFFFFFFF).count("1")


def decode_density(sel):
    """Selector -> (density fraction, mean Hamming weight per word, count, pattern).

    Low 16 bits are nonzero words per 1024; high 32 bits the nonzero pattern
    (0 -> the default all-ones). Mean HW per 32-bit word is the fraction of
    nonzero words times the popcount of the pattern."""
    sel = int(sel)
    count = min(sel & 0xFFFF, 1024)
    patt = (sel >> 32) & 0xFFFFFFFF
    if patt == 0:
        patt = 0xFFFFFFFF
    frac = count / 1024.0
    return frac, frac * popcount(patt), count, patt


def run_signal(run, n_boot):
    """Density contrast for one run, plus its drift-baselined per-block deltas.

    Returns None for a run without two conditions. `blocks` are the condition-1
    block means minus the run's condition-0 mean -- one drift-robust sample of
    the leakage at this density per block, which the classifier consumes."""
    conds = sorted(set(run.cond.tolist()))
    if len(conds) < 2:
        return None
    a, b = conds[0], conds[1]
    ci = block_bootstrap_ci(run.power_w, run.block, run.cond, a, b, n_boot=n_boot)
    means, cs = block_means(run.power_w, run.block, run.cond)
    base_blocks = means[cs == a]
    if base_blocks.size == 0:
        return None
    base = float(base_blocks.mean())
    sig_blocks = means[cs == b] - base

    frac, mean_hw, count, patt = decode_density(run.selectors[b])
    # Detector accuracy of this density against baseline, at full block length.
    curve, _ = accuracy_vs_n(run.power_w, run.block, run.cond, a, b)
    det = curve[max(curve)] if curve else float("nan")
    return {
        "label": run.label,
        "victim": run.victim,
        "repeat": int(run.meta.get("repeat", 0)),
        "frac": frac,
        "mean_hw": mean_hw,
        "count": count,
        "pattern": patt,
        "diff": ci["diff"],
        "half_width": (ci["hi"] - ci["lo"]) / 2.0,
        "detector": det,
        "blocks": sig_blocks,
        "is_aa": run.is_aa_control,
    }


def ols(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 2 or np.ptp(x) == 0:
        return float("nan"), float("nan"), float("nan")
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return float(slope), float(intercept), r2


def leakage_curve(points):
    """Print dP(density) aggregated over repeats, one row per density level."""
    by_key = defaultdict(list)
    for p in points:
        by_key[round(p["frac"], 6)].append(p)

    print("leakage vs density (dP of density d against an all-zero stream)")
    print(f"{'density':>8} {'sparsity':>8} {'meanHW':>7} {'n':>2} {'dP (W)':>9} "
          f"{'betwSD':>8} {'detector':>8}")
    print("-" * 60)
    for frac in sorted(by_key):
        ps = by_key[frac]
        d = np.array([p["diff"] for p in ps])
        det = np.array([p["detector"] for p in ps])
        between = float(d.std(ddof=1)) if len(d) > 1 else float("nan")
        tag = "  (A/A)" if ps[0]["is_aa"] else ""
        print(f"{frac:>8.3f} {1 - frac:>8.3f} {ps[0]['mean_hw']:>7.2f} {len(d):>2} "
              f"{d.mean():>+9.4f} {between:>8.4f} {np.nanmean(det):>8.3f}{tag}")


def slope_crosscheck(points):
    """Per-repeat fit of dP against mean Hamming weight; compare to Phase 1."""
    by_rep = defaultdict(list)
    for p in points:
        if not p["is_aa"]:
            by_rep[p["repeat"]].append(p)

    slopes, r2s = [], []
    print("\nper-repeat fit of dP = a + b*meanHW  (meanHW = density * popcount(pattern))")
    print(f"{'repeat':>6} {'n':>3} {'b (mW/bit)':>11} {'a (mW)':>9} {'R^2':>7}")
    for rep in sorted(by_rep):
        ps = by_rep[rep]
        b, a, r2 = ols([p["mean_hw"] for p in ps], [p["diff"] for p in ps])
        if np.isfinite(b):
            slopes.append(b)
            r2s.append(r2)
            print(f"{rep:>6} {len(ps):>3} {1000 * b:>11.2f} {1000 * a:>9.2f} {r2:>7.3f}")

    if slopes:
        s = np.array(slopes)
        sd = float(s.std(ddof=1)) if len(s) > 1 else float("nan")
        print(f"\nslope   : {1000 * s.mean():+.2f} mW per mean-set-bit"
              + (f"  (SD {1000 * sd:.2f} over {len(s)} repeats)" if np.isfinite(sd) else ""))
        print(f"mean R^2: {np.mean(r2s):.3f}")
        print("cross-check: Phase 1's Hamming-weight slope is +50.75 mW/bit. A"
              " density\n            slope near it says sparsity leaks as the"
              " same movement effect;\n            a slope above it is the added"
              " Hamming-distance term a sparse stream\n            also"
              " modulates.")


def loro_classifier(points, fracs, ks, trials, seed):
    """Leave-one-repeat-out nearest-centroid classification of density level.

    For each held-out repeat, centroids are the mean baselined per-block delta of
    each density in the remaining repeats; a test decision averages k blocks from
    one held-out run and picks the nearest centroid. Returns, per k, the
    confusion matrix (rows true, cols predicted), accuracy, and level MAE in
    density points."""
    rng = np.random.default_rng(seed)
    idx = {f: i for i, f in enumerate(fracs)}
    reps = sorted({p["repeat"] for p in points})
    out = {}
    for k in ks:
        K = len(fracs)
        conf = np.zeros((K, K), dtype=np.int64)
        abs_err = 0.0
        ntot = 0
        for held in reps:
            train = [p for p in points if p["repeat"] != held]
            cent = {}
            for f in fracs:
                vals = np.concatenate([p["blocks"] for p in train
                                       if round(p["frac"], 6) == f]) \
                    if any(round(p["frac"], 6) == f for p in train) else np.array([])
                if vals.size:
                    cent[f] = float(vals.mean())
            cfracs = [f for f in fracs if f in cent]
            if len(cfracs) < 2:
                continue
            cvals = np.array([cent[f] for f in cfracs])
            for p in (q for q in points if q["repeat"] == held):
                blk = p["blocks"]
                if blk.size == 0:
                    continue
                ftrue = round(p["frac"], 6)
                for _ in range(trials):
                    draw = float(blk[rng.integers(0, blk.size, size=k)].mean())
                    j = int(np.argmin(np.abs(cvals - draw)))
                    fpred = cfracs[j]
                    conf[idx[ftrue], idx[fpred]] += 1
                    abs_err += abs(ftrue - fpred)
                    ntot += 1
        acc = float(np.trace(conf) / conf.sum()) if conf.sum() else float("nan")
        mae = abs_err / ntot if ntot else float("nan")
        out[k] = dict(conf=conf, acc=acc, mae=mae)
    return out


def print_confusion(fracs, res):
    conf = res["conf"]
    print(f"  accuracy {res['acc']:.3f}   level MAE {100 * res['mae']:.1f} density points")
    hdr = "true\\pred " + " ".join(f"{f:>6.2f}" for f in fracs)
    print("  " + hdr)
    for i, f in enumerate(fracs):
        row = conf[i]
        tot = row.sum()
        cells = " ".join(f"{(c / tot if tot else 0):>6.2f}" for c in row)
        print(f"  {f:>7.2f}  {cells}")


def compute_channel(points):
    """Paired dP(victimA,d) - dP(victimB,d) per density, for the VNNI experiment."""
    victims = sorted({p["victim"] for p in points})
    if len(victims) != 2:
        return
    va, vb = victims  # alphabetical: ws_sparse_op_mov, ws_sparse_op_vnni
    print(f"\ncompute channel: {vb} minus {va}, paired per repeat")
    print(f"{'density':>8} {'n':>2} {'compute dP (W)':>15} {'betwSD':>8}  verdict")
    print("-" * 52)
    by_frac_rep = defaultdict(dict)
    for p in points:
        by_frac_rep[round(p["frac"], 6)].setdefault(p["repeat"], {})[p["victim"]] = p["diff"]
    for frac in sorted(by_frac_rep):
        paired = [r[vb] - r[va] for r in by_frac_rep[frac].values()
                  if va in r and vb in r]
        if not paired:
            continue
        d = np.array(paired)
        between = float(d.std(ddof=1)) if len(d) > 1 else float("nan")
        verdict = ("differs" if np.isfinite(between) and abs(d.mean()) > 2 * between
                   else "within noise")
        print(f"{frac:>8.3f} {len(d):>2} {d.mean():>+15.4f} {between:>8.4f}  {verdict}")


def figure(points, out_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by_frac = defaultdict(list)
    for p in points:
        by_frac[p["frac"]].append(p["diff"])
    xs = np.array(sorted(by_frac))
    ys = np.array([np.mean(by_frac[f]) for f in xs])
    es = np.array([np.std(by_frac[f], ddof=1) if len(by_frac[f]) > 1 else 0.0 for f in xs])

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.errorbar(xs, ys, yerr=es, fmt="o-", capsize=3, lw=1.4)
    ax.axhline(0.0, color="k", lw=0.8, alpha=0.4)
    ax.set_xlabel("operand density (fraction of nonzero words)")
    ax.set_ylabel("delta package power vs all-zero stream (W)")
    ax.set_title("Sparsity leakage: power against operand density")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dirs", nargs="+")
    ap.add_argument("--labels", default=None,
                    help="glob restricting which runs are read")
    ap.add_argument("--victim", default=None,
                    help="restrict the classifier to one victim when a run set "
                         "holds several (default: the one with the most runs)")
    ap.add_argument("--bootstrap", type=int, default=4000)
    ap.add_argument("--ks", default="1,4,16",
                    help="blocks averaged per classifier decision")
    ap.add_argument("--trials", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-figure", action="store_true")
    args = ap.parse_args()

    points = []
    for d in args.results_dirs:
        _, runs = load_results(Path(d))
        for run in runs:
            if args.labels and not fnmatch(run.label, args.labels):
                continue
            s = run_signal(run, args.bootstrap)
            if s:
                points.append(s)
    if not points:
        raise SystemExit("no usable runs")

    victims = sorted({p["victim"] for p in points})
    print(f"victims: {', '.join(victims)}   runs: {len(points)}   "
          f"repeats: {len({p['repeat'] for p in points})}\n")

    # ---- compute channel (VNNI experiment, two victims) -------------------
    if len(victims) > 1:
        compute_channel(points)

    # ---- everything below is per single victim ----------------------------
    if args.victim:
        primary = args.victim
    else:
        counts = defaultdict(int)
        for p in points:
            counts[p["victim"]] += 1
        primary = max(counts, key=counts.get)
    vpts = [p for p in points if p["victim"] == primary]
    if len(victims) > 1:
        print(f"\n--- density recovery on {primary} ---\n")

    leakage_curve(vpts)
    slope_crosscheck(vpts)

    fracs = sorted({round(p["frac"], 6) for p in vpts})
    ks = [int(k) for k in args.ks.split(",") if k.strip()]
    if len({p["repeat"] for p in vpts}) >= 2 and len(fracs) >= 2:
        res = loro_classifier(vpts, fracs, ks, args.trials, args.seed)
        print(f"\nsparsity classification ({len(fracs)} density levels, "
              f"leave-one-repeat-out, nearest centroid)")
        for k in ks:
            if k in res:
                print(f"\n k = {k} block(s) averaged per decision:")
                print_confusion(fracs, res[k])
    else:
        print("\n(need >=2 repeats and >=2 densities for the classifier)")

    if not args.no_figure:
        out = Path(args.results_dirs[0]) / "figures"
        out.mkdir(exist_ok=True)
        figure(vpts, out / "sparsity-density.png")
        print(f"\nfigure -> {out / 'sparsity-density.png'}")


if __name__ == "__main__":
    main()
