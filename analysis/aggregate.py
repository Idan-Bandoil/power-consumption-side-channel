"""Aggregate repeated runs of the same condition into between-run statistics.

    ./venv/bin/python3 -m analysis.aggregate results/<run_id> [more dirs...]

`analysis.report` quantifies uncertainty *within* a run. That understates the
real error bar: repeating an identical configuration on a different thermal
state moves the answer by more than the within-run CI allows for. Nothing
should be quoted from a single run, so this reports the per-repeat effects
side by side and puts the between-run spread next to the within-run CI.
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
        "repeat": run.meta.get("repeat", 0),
        "is_aa": run.is_aa_control,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dirs", nargs="+")
    ap.add_argument("--bootstrap", type=int, default=4000)
    args = ap.parse_args()

    groups = defaultdict(list)
    for d in args.results_dirs:
        _, runs = load_results(Path(d))
        for run in runs:
            e = effect_of(run, args.bootstrap)
            if e:
                groups[run.label].append(e)

    print(f"{'label':<20} {'n':>2} {'mean dW':>9} {'between':>9} {'within':>9} "
          f"{'ratio':>6} {'sign':>10} {'acc':>6}")
    print("-" * 82)

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

        print(f"{label + tag:<20} {len(diffs):>2} {diffs.mean():>+9.4f} "
              f"{between:>9.4f} {within:>9.4f} {ratio:>6.2f} {sign:>10} {acc:>6.3f}")
        print(f"{'':<20}    per-repeat: "
              + "  ".join(f"{d:+.4f}" for d in diffs))

    print("\nmean dW  : mean of the per-repeat condition differences, in watts")
    print("between  : SD of those differences across repeats")
    print("within   : mean half-width of each run's own 95% bootstrap CI")
    print("ratio    : between/within. >1 means single-run CIs are optimistic")
    print("sign     : whether every repeat agrees on the direction of the effect")


if __name__ == "__main__":
    main()
