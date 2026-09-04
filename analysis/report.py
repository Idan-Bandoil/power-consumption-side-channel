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
# Every operand claim in the project assumes the selector changes *what* is
# moved and not *how much*. The victims are written to be load-bound so that
# this holds, but it was argued architecturally and never measured until the
# driver started latching bursts per block. A fractional difference in achieved
# throughput between conditions above this is a work difference masquerading as
# an operand effect. Provisional: the burst counters are exact to ~1e-4, so
# this is far above their noise and should only fire on something real.
MAX_WORK_IMBALANCE = 0.01
# Config-A's load-bearing claim is that pinning the frequency removes DVFS, so
# that a power difference is a power difference and not a frequency response to
# one. Under Config-B a frequency difference between conditions is the tier-2
# channel rather than a fault, so this is not applied there.
MAX_FREQ_IMBALANCE_A = 0.01


def hr(title=""):
    return f"\n{'=' * 78}\n{title}\n{'=' * 78}" if title else "-" * 78


def analyse_run(run, fig_dir, n_perm, n_boot, config="A"):
    out = {"label": run.label, "victim": run.victim,
           "selectors": run.selectors, "gates": {}, "pairs": []}

    print(hr(f"{run.label}  [victim={run.victim}, mode={run.sample_mode}]"))
    aa_note = ""
    if run.declared_control:
        aa_note = "   << A/A CONTROL (declared: same contents, different buffers)"
    elif run.is_aa_control:
        aa_note = "   << A/A CONTROL"
    print(f"  selectors        : {run.selectors}{aa_note}")
    print(f"  samples          : {len(run.power_w)} over {len(np.unique(run.block))} blocks")
    print(f"  RAPL period      : {run.rapl_period_ms:.3f} ms")

    zf = run.zero_tick_fraction
    zero_ok = zf <= MAX_ZERO_TICK_FRACTION
    out["gates"]["zero_ticks"] = zero_ok
    print(f"  zero-tick samples: {zf * 100:.2f}%  "
          f"[{'OK' if zero_ok else 'FAIL — sampler is aliasing'}]")

    conds = sorted(set(run.cond.tolist()))
    gbs = run.bytes_per_s_by_cond
    vfreq = run.freq_khz_by_cond

    # "MHz mon" is the monitor's own clock, from the APERF/MPERF pair the
    # sampler reads on the attacker core -- which is idling in a poll loop, so
    # it says nothing about the victims. "MHz vic" is a victim core's, read
    # once per block from scaling_cur_freq. Only the second one can check
    # Config-A's premise, and it exists because the first was being printed as
    # though it did.
    print(f"\n  {'cond':>4} {'selector':>12} {'n':>7} {'mean W':>9} {'sd W':>8}"
          f" {'MHz mon':>8} {'MHz vic':>8} {'GB/s':>9}")
    for c in conds:
        m = run.mask(c)
        p, f = run.power_w[m], run.freq_khz[m]
        vf = (f"{vfreq[c] / 1000:.0f}"
              if c < len(vfreq) and vfreq[c] else "-")
        gb = f"{gbs[c] / 1e9:.1f}" if c < len(gbs) and gbs[c] else "-"
        print(f"  {c:>4} {run.selectors[c]:>12} {m.sum():>7} "
              f"{p.mean():>9.3f} {p.std(ddof=1):>8.3f} {np.mean(f[f > 0]) / 1000:>8.0f}"
              f" {vf:>8} {gb:>9}")

    # The work-rate check. A throughput difference between conditions would
    # show up as a power difference and be attributed to the operand, and
    # nothing in the design would reveal it -- the pooled figure averages the
    # two conditions together. The burst counters are exact, so an imbalance
    # here is real rather than noise.
    if not gbs:
        print("\n  work balance     : not recorded (run predates the "
              "per-condition burst counters)")
    elif min(gbs) <= 0:
        # A register-resident victim moves no operand traffic to count.
        print("\n  work balance     : no operand traffic to balance "
              "(register-resident victim)")
    elif len(gbs) == len(conds):
        lo, hi = min(gbs), max(gbs)
        rel = (hi - lo) / ((hi + lo) / 2)
        ok = rel <= MAX_WORK_IMBALANCE
        out["gates"]["work_balance"] = ok
        out["work_imbalance"] = float(rel)
        # If every watt the package draws scaled with throughput -- it does
        # not, a large static term does not -- this is what the imbalance
        # could account for. A deliberate over-estimate, quoted so it can be
        # compared against the effect rather than taken on faith.
        bound = rel * float(np.mean(run.power_w))
        out["work_bias_bound_w"] = float(bound)
        print(f"\n  work balance     : {rel * 100:.3f}% throughput spread"
              f"  [{'OK' if ok else 'FAIL — conditions did different amounts of work'}]")
        print(f"                     bounds a work-attributable difference at "
              f"{bound * 1000:.0f} mW if all package power scaled with traffic")
    else:
        print(f"\n  work balance     : {len(gbs)} throughput figures for "
              f"{len(conds)} conditions, so not checked")

    # The Config-A check. Turbo being off is a machine state, not a per-run
    # measurement; this is the per-run measurement.
    if len(vfreq) == len(conds) and min(vfreq) > 0:
        lo, hi = min(vfreq), max(vfreq)
        rel = (hi - lo) / ((hi + lo) / 2)
        out["freq_imbalance"] = float(rel)
        if config == "A":
            ok = rel <= MAX_FREQ_IMBALANCE_A
            out["gates"]["frequency_balance"] = ok
            verdict = "OK" if ok else "FAIL — conditions sat at different victim clocks"
        else:
            verdict = "not gated under Config-B, where this is the tier-2 channel"
        print(f"  victim frequency : cpu{run.freq_core}, spread {hi - lo:.0f} kHz "
              f"({rel * 100:.3f}%)  [{verdict}]")
    else:
        print(f"  victim frequency : not recorded (run predates the "
              f"per-condition sysfs read)")

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

    # Imbalance on its own is a unitless number, and the 0.10 threshold has
    # never been converted into the thing it is supposed to bound: how many
    # watts of drift the design lets through as a condition effect. Drift over
    # a run is already measured (the decile span above), and the two multiply
    # -- a condition sitting a fraction f later in the run than the other picks
    # up f of whatever the run drifted by. This is a first-order bound, not an
    # exact bias, but it is in watts and so can be compared against the effect.
    drift_span = max(spans.values()) if spans else float("nan")
    admitted = imbalance * drift_span
    out["admitted_bias_w"] = float(admitted)
    print(f"  admitted bias    : {admitted * 1000:.1f} mW"
          f"  (imbalance {imbalance:.3f} x drift span {drift_span:.3f} W)")
    print(f"                     an effect smaller than this is not separated "
          f"from drift by the design alone")

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
    config = manifest["experiment"].get("config", "A")
    print(f"  config    : {config}")
    first = manifest["runs"][0]["state_before"] if manifest["runs"] else {}
    print(f"  no_turbo  : {first.get('no_turbo')}   governor: {first.get('governor')}")
    print(f"  pkg temp  : {first.get('package_temp_c')} C at start")

    fig_dir = None if args.no_figures else results_dir / "figures"
    report = [analyse_run(r, fig_dir, args.permutations, args.bootstrap, config)
              for r in runs]

    print(hr("Platform power state"))
    # A power measurement whose power *limit* moved underneath it. thermald
    # lowers PL1 as the die heats and does it mid-run: the corpus has sessions
    # going 200 W -> 15 -> 35 as the package crossed ~50 C. Nothing measured so
    # far was distorted -- package power exceeded the nominal limit in those
    # runs without being clamped -- but "it did not bind" is not "it cannot",
    # and a session where it did would show only as a shrinking effect.
    seen = {}
    for e in manifest["runs"]:
        for when in ("state_before", "state_after"):
            st = e.get(when) or {}
            key = (st.get("pl1_uw"), st.get("pl2_uw"),
                   st.get("ac_online"), st.get("battery_status"))
            if any(v is not None for v in key):
                seen.setdefault(key, []).append(f"{e.get('tag')}/{when}")

    def _w(uw):
        return f"{float(uw) / 1e6:.1f} W" if uw else str(uw)

    for (pl1, pl2, ac, bat), where in seen.items():
        src = ("mains" if ac == "1" else "BATTERY" if ac == "0"
               else "power source not recorded")
        print(f"  PL1 {_w(pl1):<9} PL2 {_w(pl2):<9} {src:<28} "
              f"{len(where):>4} snapshots")
    if len(seen) > 1:
        print("\n  first snapshot at each state:")
        for k, where in seen.items():
            print(f"    {where[0]}")

    platform_stable = len(seen) <= 1
    on_battery = any(k[2] == "0" for k in seen)
    print(f"\n  platform power state: "
          f"{'constant' if platform_stable else 'CHANGED DURING THE SESSION'}")
    if on_battery:
        print("  measured on battery, unlike the rest of the corpus")

    print(hr("Validity gates"))
    failed = []
    if not platform_stable:
        print(f"  {'(session)':<28} {'power_state':<16} FAIL")
        failed.append("session/power_state")
    if on_battery:
        print(f"  {'(session)':<28} {'on_mains':<16} FAIL")
        failed.append("session/on_mains")
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
