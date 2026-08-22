"""Validity report for a results directory.

    ./venv/bin/python3 -m analysis.report results/<run_id>

Prints per-run statistics and applies the validity gates from the plan. No
result should be quoted in the thesis unless its gates pass.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

from . import plots
from .load import load_results
from .stats import (accuracy_vs_n, block_bootstrap_ci, block_permutation_test,
                    cohens_d, drift_table, samples_for_accuracy,
                    temporal_balance)

# Above this, the sampler is aliasing against the RAPL update interval.
MAX_ZERO_TICK_FRACTION = 0.01
# An A/A control must not beat this with the longest integration available.
AA_ACCURACY_CEILING = 0.60
# Conditions must sit at similar mean positions in the run; above this they are
# temporally separated enough for drift to be read as a condition effect.
MAX_TEMPORAL_IMBALANCE = 0.10


def hr(title=""):
    return f"\n{'=' * 78}\n{title}\n{'=' * 78}" if title else "-" * 78


def analyse_run(run, fig_dir, n_perm, n_boot):
    out = {"label": run.label, "victim": run.victim,
           "selectors": run.selectors, "gates": {}, "pairs": []}

    print(hr(f"{run.label}  [victim={run.victim}, mode={run.sample_mode}]"))
    print(f"  selectors        : {run.selectors}"
          f"{'   << A/A CONTROL' if run.is_aa_control else ''}")
    print(f"  samples          : {len(run.power_w)} over {len(np.unique(run.block))} blocks")
    print(f"  RAPL period      : {run.rapl_period_ms:.3f} ms")

    zf = run.zero_tick_fraction
    zero_ok = zf <= MAX_ZERO_TICK_FRACTION
    out["gates"]["zero_ticks"] = zero_ok
    print(f"  zero-tick samples: {zf * 100:.2f}%  "
          f"[{'OK' if zero_ok else 'FAIL — sampler is aliasing'}]")

    print(f"\n  {'cond':>4} {'selector':>12} {'n':>7} {'mean W':>9} {'sd W':>8} {'MHz':>8}")
    for c in sorted(set(run.cond.tolist())):
        m = run.mask(c)
        p, f = run.power_w[m], run.freq_khz[m]
        print(f"  {c:>4} {run.selectors[c]:>12} {m.sum():>7} "
              f"{p.mean():>9.3f} {p.std(ddof=1):>8.3f} {np.mean(f[f > 0]) / 1000:>8.0f}")

    print("\n  drift check (mean W per decile of chronological block order):")
    dt = drift_table(run.power_w, run.block, run.cond)
    for c, rows in dt.items():
        print(f"    cond {c}: " + " ".join(f"{v:6.2f}" for v in rows))
    spans = {c: (np.nanmax(r) - np.nanmin(r)) for c, r in dt.items()}
    print("    within-condition span: "
          + ", ".join(f"cond {c} {v:.3f} W" for c, v in spans.items()))
    out["drift"] = {str(c): r for c, r in dt.items()}

    imbalance, pos = temporal_balance(run.block, run.cond)
    balanced = imbalance <= MAX_TEMPORAL_IMBALANCE
    out["gates"]["interleaving"] = balanced
    out["temporal_balance"] = {"imbalance": imbalance,
                               "positions": {str(k): v for k, v in pos.items()}}
    print(f"\n  interleaving     : imbalance {imbalance:.3f}  "
          f"(mean position " + ", ".join(f"cond {c} {p:.2f}" for c, p in pos.items())
          + f")  [{'OK' if balanced else 'FAIL — conditions are temporally separated'}]")

    conds = sorted(set(run.cond.tolist()))
    for b in conds[1:]:
        a = conds[0]
        pa = run.power_w[run.mask(a)]
        pb = run.power_w[run.mask(b)]

        ci = block_bootstrap_ci(run.power_w, run.block, run.cond, a, b,
                                n_boot=n_boot)
        perm = block_permutation_test(run.power_w, run.block, run.cond, a, b,
                                      n_perm=n_perm)
        d = cohens_d(pa, pb)
        curve, info = accuracy_vs_n(run.power_w, run.block, run.cond, a, b)
        n95 = samples_for_accuracy(curve, 0.95)
        n99 = samples_for_accuracy(curve, 0.99)
        best = max(curve.values()) if curve else float("nan")

        print(f"\n  --- condition {a} vs {b} ---")
        print(f"  mean difference  : {ci['diff']:+.4f} W  "
              f"95% CI [{ci['lo']:+.4f}, {ci['hi']:+.4f}]  "
              f"({ci['diff'] / max(pa.mean(), 1e-9) * 100:+.2f}% of baseline)")
        print(f"  Cohen's d        : {d:+.4f}  (per sample)")
        print(f"  permutation p    : {perm['p']:.5f}  ({n_perm} block relabelings)")
        print(f"  detector         : best accuracy {best:.3f} at n<={info['block_len']}")
        for n in sorted(curve):
            bar = "#" * int((curve[n] - 0.5) * 80) if curve[n] > 0.5 else ""
            print(f"      n={n:>4}  acc={curve[n]:.3f}  {bar}")

        rate95 = 1000.0 / (n95 * run.rapl_period_ms) if n95 else None
        rate99 = 1000.0 / (n99 * run.rapl_period_ms) if n99 else None
        print(f"  n for 95%        : {n95}"
              + (f"  ->  {rate95:.1f} bit/s raw" if rate95 else "  (not reached)"))
        print(f"  n for 99%        : {n99}"
              + (f"  ->  {rate99:.1f} bit/s raw" if rate99 else "  (not reached)"))

        ci_excludes_zero = (ci["lo"] > 0) or (ci["hi"] < 0)
        if run.is_aa_control:
            ok = (not ci_excludes_zero) and best <= AA_ACCURACY_CEILING
            print(f"  A/A GATE         : {'PASS' if ok else 'FAIL'} "
                  f"(CI must contain 0, accuracy must stay near chance)")
            out["gates"][f"aa_{a}v{b}"] = ok
        else:
            print(f"  effect is {'SIGNIFICANT' if ci_excludes_zero else 'not resolved'} "
                  f"(bootstrap CI {'excludes' if ci_excludes_zero else 'contains'} zero)")

        out["pairs"].append(dict(a=a, b=b, ci=ci, cohens_d=d, permutation=perm,
                                 accuracy=curve, detector=info,
                                 n95=n95, n99=n99,
                                 bits_per_s_95=rate95, bits_per_s_99=rate99))

    if fig_dir:
        fig_dir.mkdir(parents=True, exist_ok=True)
        plots.density(run, fig_dir / f"{run.label}-density.png")
        plots.drift(run, fig_dir / f"{run.label}-drift.png")
        if out["pairs"]:
            plots.accuracy(out["pairs"][0]["accuracy"],
                           fig_dir / f"{run.label}-accuracy.png",
                           run.label, run.rapl_period_ms)
        print(f"\n  figures -> {fig_dir}")

    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dir")
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--permutations", type=int, default=10000)
    ap.add_argument("--bootstrap", type=int, default=10000)
    ap.add_argument("--json", help="write the full report to this path")
    args = ap.parse_args()

    results_dir = Path(args.results_dir)
    manifest, runs = load_results(results_dir)

    print(hr(f"Run {manifest['run_id']}"))
    print(f"  cpu       : {manifest.get('cpu_model')}")
    print(f"  commit    : {manifest['git']['commit']}"
          f"{' (DIRTY)' if manifest['git']['dirty'] else ''}")
    print(f"  config    : {manifest['experiment'].get('config')}")
    first = manifest["runs"][0]["state_before"] if manifest["runs"] else {}
    print(f"  no_turbo  : {first.get('no_turbo')}   governor: {first.get('governor')}")
    print(f"  pkg temp  : {first.get('package_temp_c')} C at start")

    fig_dir = None if args.no_figures else results_dir / "figures"
    report = [analyse_run(r, fig_dir, args.permutations, args.bootstrap) for r in runs]

    print(hr("Validity gates"))
    failed = []
    for r in report:
        for gate, ok in r["gates"].items():
            print(f"  {r['label']:<28} {gate:<16} {'PASS' if ok else 'FAIL'}")
            if not ok:
                failed.append(f"{r['label']}/{gate}")
    if not any(r["gates"] for r in report):
        print("  (no gates applicable to this run)")

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, default=str))
        print(f"\nreport -> {args.json}")

    if failed:
        print(f"\nFAILED GATES: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
