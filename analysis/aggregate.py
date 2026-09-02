"""Aggregate repeated runs of the same condition into between-run statistics.

    ./venv/bin/python3 -m analysis.aggregate results/<run_id> [more dirs...]

`analysis.report` quantifies uncertainty *within* a run. That understates the
real error bar: repeating an identical configuration on a different thermal
state moves the answer by more than the within-run CI allows for. Nothing
should be quoted from a single run, so this reports the per-repeat effects
side by side and puts the between-run spread next to the within-run CI.

`--against LABEL` adds a paired comparison of every other row against that
one. An experiment whose rows share a large common term -- a per-instruction
table, where every victim carries the same 2 W of load traffic and only the
instruction differs -- cannot be read from the unpaired column, because the
between-run spread it reports is dominated by run-to-run variation in the
shared term. Runs from the same repeat share a thermal and frequency state, so
differencing them repeat by repeat cancels most of that and asks the question
actually of interest: is this row different from the reference row?

Pairing is not automatically the better read, and the paired SD says which it
is. It helps when the rows move together, which they do when they share a
positive common term. It *hurts* when they are anti-correlated: pairing
phase1_polarity's l3_reverse against l3_forward gives an SD of 0.180 W against
0.105 and 0.104 unpaired, because the effect scales with a thermal state and
the two rows carry it with opposite signs. Compare the two columns before
quoting either.
"""
import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np

from .load import load_results
from .stats import accuracy_vs_n, block_bootstrap_ci


def effect_of(run, n_boot):
    conds = sorted(set(run.cond.tolist()))
    if len(conds) < 2:
        return None
    a, b = conds[0], conds[1]
    ci = block_bootstrap_ci(run.power_w, run.block, run.cond, a, b, n_boot=n_boot)
    curve, _ = accuracy_vs_n(run.power_w, run.block, run.cond, a, b)
    return {
        "diff": ci["diff"],
        "half_width": (ci["hi"] - ci["lo"]) / 2.0,
        "baseline": float(run.power_w[run.mask(a)].mean()),
        "best_acc": max(curve.values()) if curve else float("nan"),
        "repeat": int(run.meta.get("repeat", 0)),
        "is_aa": run.is_aa_control,
        "bytes_per_s": run.bytes_per_s,
    }


# Two-sided 95% critical values of Student's t. At three or four repeats the
# normal approximation is badly optimistic -- 3.18 against 1.96 at 3 df -- and
# this venv has no scipy to ask.
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
       7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179,
       15: 2.131, 20: 2.086, 30: 2.042}


def t95(df):
    if df < 1:
        return float("nan")
    for k in sorted(T95):
        if df <= k:
            return T95[k]
    return 1.96


def _value(e, per_byte):
    """A run's effect, in watts or in pJ/byte. NaN if it cannot be normalised."""
    if not per_byte:
        return e["diff"]
    gbs = e["bytes_per_s"] / 1e9
    if not np.isfinite(gbs) or gbs <= 0:
        return float("nan")
    return 1000.0 * e["diff"] / gbs


def paired_section(groups, reference, per_byte=False):
    """Every row differenced against `reference`, repeat by repeat."""
    if reference not in groups:
        print(f"\n--against: no row labelled '{reference}'; "
              f"have {', '.join(sorted(groups))}")
        return

    ref = {e["repeat"]: _value(e, per_byte) for e in groups[reference]}
    unit = "pJ/B" if per_byte else "dW"

    print(f"\npaired against {reference} "
          f"(per-repeat difference, so the shared term cancels)"
          + ("  [pJ/byte]" if per_byte else ""))
    print(f"{'label':<20} {'n':>2} {'mean d' + unit:>9} {'SD':>8} {'SE':>8} "
          f"{'95% CI':>19}  verdict")
    print("-" * 88)

    for label in sorted(groups):
        if label == reference or groups[label][0]["is_aa"]:
            continue
        pairs = [v for v in (_value(e, per_byte) - ref[e["repeat"]]
                             for e in groups[label] if e["repeat"] in ref)
                 if np.isfinite(v)]
        if len(pairs) < 2:
            continue
        d = np.array(pairs)
        mean = float(d.mean())
        sd = float(d.std(ddof=1))
        se = sd / np.sqrt(len(d))
        half = t95(len(d) - 1) * se
        lo, hi = mean - half, mean + half
        verdict = "differs" if lo > 0 or hi < 0 else "indistinguishable"
        print(f"{label:<20} {len(d):>2} {mean:>+9.4f} {sd:>8.4f} {se:>8.4f} "
              f"[{lo:>+7.4f}, {hi:>+7.4f}]  {verdict}")

    if per_byte:
        print("\nRows with no operand traffic to normalise by -- register-resident")
        print("victims -- are omitted from this view rather than divided by zero.")
    print(f"\nmean d{unit} : mean over repeats of (row - {reference}) within a repeat")
    print("95% CI   : Student's t, so it widens correctly at three or four repeats")
    print("verdict  : whether that interval excludes zero")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dirs", nargs="+")
    ap.add_argument("--bootstrap", type=int, default=4000)
    ap.add_argument("--against", metavar="LABEL", default=None,
                    help="also report every row paired against this one, "
                         "differenced repeat by repeat")
    ap.add_argument("--per-byte", action="store_true",
                    help="pair in pJ/byte rather than watts, so a row that "
                         "moves operands at a different rate is comparable")
    args = ap.parse_args()

    groups = defaultdict(list)
    for d in args.results_dirs:
        _, runs = load_results(Path(d))
        for run in runs:
            e = effect_of(run, args.bootstrap)
            if e:
                groups[run.label].append(e)

    print(f"{'label':<20} {'n':>2} {'mean dW':>9} {'between':>9} {'within':>9} "
          f"{'ratio':>6} {'sign':>7} {'acc':>6} {'GB/s':>8} {'mW/GB/s':>8}")
    print("-" * 100)

    for label in sorted(groups):
        es = sorted(groups[label], key=lambda e: e["repeat"])
        diffs = np.array([e["diff"] for e in es])
        within = float(np.mean([e["half_width"] for e in es]))
        between = float(diffs.std(ddof=1)) if len(diffs) > 1 else float("nan")
        acc = float(np.mean([e["best_acc"] for e in es]))

        if len(diffs) > 1 and np.all(diffs > 0):
            sign = "all +"
        elif len(diffs) > 1 and np.all(diffs < 0):
            sign = "all -"
        elif len(diffs) > 1:
            sign = "FLIPS"
        else:
            sign = "-"

        # >1 means repeating the run moves the answer more than the run's own
        # confidence interval claims it could.
        ratio = between / within if within > 0 and len(diffs) > 1 else float("nan")
        tag = " (A/A)" if es[0]["is_aa"] else ""

        # Runs from before the driver reported throughput have no value here.
        bps = [e["bytes_per_s"] for e in es if np.isfinite(e["bytes_per_s"])]
        gbs = (float(np.mean(bps)) / 1e9) if bps else float("nan")
        per = 1000.0 * diffs.mean() / gbs if gbs > 0 else float("nan")
        print(f"{label + tag:<20} {len(diffs):>2} {diffs.mean():>+9.4f} "
              f"{between:>9.4f} {within:>9.4f} {ratio:>6.2f} {sign:>7} {acc:>6.3f} "
              f"{gbs:>8.2f} {per:>8.1f}")
        print(f"{'':<20}    per-repeat: "
              + "  ".join(f"{d:+.4f}" for d in diffs))

    if args.against:
        paired_section(groups, args.against, args.per_byte)

    print("\nmean dW  : mean of the per-repeat condition differences, in watts")
    print("between  : SD of those differences across repeats")
    print("within   : mean half-width of each run's own 95% bootstrap CI")
    print("ratio    : between/within. >1 means single-run CIs are optimistic")
    print("sign     : whether every repeat agrees on the direction of the effect")
    print("GB/s     : measured operand throughput summed over victim threads")
    print("mW/GB/s  : leakage normalised to traffic rate")


if __name__ == "__main__":
    main()
