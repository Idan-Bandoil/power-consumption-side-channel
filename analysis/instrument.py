"""Cross-run checks on the instrument itself, from committed manifests alone.

    ./venv/bin/python3 -m analysis.instrument results/

Everything else in this package analyses one experiment. This asks questions
that only a whole corpus of runs can answer, and it needs no CSV -- every field
it reads is in `manifest.json`, which is tracked in git.

Two checks so far.

`period` tests whether the sampler's reported RAPL update period is a property
of the part or of the sampler. util/sampler.c flags an edge as an overshoot
when it arrives more than 1.5 periods late, and then feeds that same interval
into the EWMA that estimates the period -- so at overshoot fraction p the
estimate should converge on (1+p)*T rather than T. If that is what is
happening, the reported period is an artifact and the "1.000 ms, not 2^-10 s"
finding does not survive; the signature is a strong positive correlation
between the estimate and the overshoot rate, with the fitted intercept landing
near the datasheet value.

`throughput` reports the spread of achieved victim bandwidth per victim across
the corpus, which is what says whether a run's operating point matched the one
its victim was characterised at.

`bias` converts the interleaving gate into watts. The gate fails a run whose
conditions sit more than 0.10 apart in mean chronological position, but that
threshold was never tied to the quantity it exists to bound: how much of the
run's own drift can reach the difference. Both halves are measured per run --
imbalance, and the within-condition span across deciles -- and their product is
a first-order bound on the bias the design admits. Reported against the effect
each run actually claims, so the margin is visible rather than implied. Unlike
the other two checks this one reads the CSVs, so it only works where the raw
data is still on disk.
"""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

# 2^-10 s: the interval implied by the RAPL time unit on this part, and the
# value the measured estimate is being compared against.
DATASHEET_PERIOD_MS = 1000.0 / 1024.0


def driver_runs(results_dir):
    """Every driver run in every manifest under `results_dir`, as dicts."""
    out = []
    for man in sorted(Path(results_dir).glob("*/manifest.json")):
        try:
            m = json.loads(man.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for entry in m.get("runs", []):
            # Covert runs carry the sampler fields under "rx" instead, and only
            # the tier-1 receiver has them at all.
            d = entry.get("driver") or entry.get("rx") or {}
            if "rapl_period_ms" not in d or "rapl_overshoots" not in d:
                continue
            n = d.get("samples_written", 0)
            if not n:
                continue
            out.append({
                "run_id": m["run_id"],
                "tag": entry.get("tag", "?"),
                "victim": entry.get("victim") or (entry.get("tx") or {}).get("victim"),
                "period_ms": float(d["rapl_period_ms"]),
                "overshoot": float(d["rapl_overshoots"]) / float(n),
                "samples": int(n),
                "bytes_per_s": float(d.get("victim_bytes_per_s", float("nan"))),
                "mode": d.get("sample_mode", "?"),
            })
    return out


def ols(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return slope, intercept, (1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan"))


def check_period(runs):
    runs = [r for r in runs if r["mode"] == "edge"]
    if len(runs) < 3:
        print("  not enough edge-sampled runs")
        return
    p = np.array([r["period_ms"] for r in runs])
    f = np.array([r["overshoot"] for r in runs])

    print(f"  runs                : {len(runs)} edge-sampled")
    print(f"  reported period     : {p.mean():.4f} ms  "
          f"(SD {p.std(ddof=1):.4f}, range {p.min():.4f}-{p.max():.4f})")
    print(f"  datasheet 2^-10 s   : {DATASHEET_PERIOD_MS:.4f} ms  "
          f"({100 * (p.mean() / DATASHEET_PERIOD_MS - 1):+.2f}% vs reported)")
    print(f"  overshoot fraction  : {f.mean():.4f}  "
          f"(range {f.min():.4f}-{f.max():.4f})")

    r = float(np.corrcoef(f, p)[0, 1])
    slope, intercept, r2 = ols(f, p)
    print(f"\n  corr(overshoot, period) = {r:+.3f}   R^2 = {r2:.3f}")
    print(f"  period_ms ~ {intercept:.4f} + {slope:.4f} * overshoot_fraction")
    print(f"    intercept {intercept:.4f} ms is the period extrapolated to a")
    print(f"    zero-overshoot sampler; datasheet is {DATASHEET_PERIOD_MS:.4f} ms")
    print(f"    ({100 * (intercept / DATASHEET_PERIOD_MS - 1):+.2f}% from it, against "
          f"{100 * (p.mean() / DATASHEET_PERIOD_MS - 1):+.2f}% for the raw mean)")

    # The contamination model is period = T * (1 + overshoot), so a fit of the
    # estimate against the fraction should have slope ~ T if that is the whole
    # story. Reporting the ratio makes the test explicit rather than implied.
    print(f"\n  contamination model : estimate = T * (1 + overshoot) predicts")
    print(f"                        slope/intercept = 1.00; measured "
          f"{slope / intercept:.2f}")
    if r > 0.5:
        print("\n  VERDICT: the reported period tracks the sampler's own overshoot")
        print("  rate. It is a property of the estimator, not of the part, and the")
        print("  chapter's period finding needs restating from a robust estimator.")
    elif r < 0.2:
        print("\n  VERDICT: no material dependence on overshoot rate; the reported")
        print("  period is not explained by estimator contamination.")
    else:
        print("\n  VERDICT: partial dependence -- neither reading is clean.")


def check_throughput(runs):
    by_victim = defaultdict(list)
    for r in runs:
        if math.isfinite(r["bytes_per_s"]) and r["bytes_per_s"] > 0 and r["victim"]:
            by_victim[r["victim"]].append(r["bytes_per_s"] / 1e9)
    if not by_victim:
        print("  no runs report throughput")
        return
    print(f"  {'victim':<16} {'n':>4} {'mean GB/s':>10} {'SD':>8} "
          f"{'min':>8} {'max':>8}")
    for v in sorted(by_victim, key=lambda v: -np.mean(by_victim[v])):
        g = np.array(by_victim[v])
        sd = g.std(ddof=1) if len(g) > 1 else float("nan")
        print(f"  {v:<16} {len(g):>4} {g.mean():>10.1f} {sd:>8.1f} "
              f"{g.min():>8.1f} {g.max():>8.1f}")
    print("\n  A victim whose spread is wide across the corpus was not run at one")
    print("  operating point, so a characterisation from one session does not")
    print("  necessarily transfer to another that used the same name.")


def check_bias(results_dir, top=15):
    """Admitted bias -- imbalance x drift span -- against the effect claimed.

    Needs the CSVs, which are not tracked, so this reports on whatever raw data
    is still present rather than on the whole committed corpus.
    """
    from .load import load_results
    from .stats import block_bootstrap_ci, drift_table, temporal_balance

    rows = []
    for man in sorted(Path(results_dir).glob("*/manifest.json")):
        try:
            _, runs = load_results(man.parent)
        except Exception:
            continue
        for r in runs:
            conds = sorted(set(r.cond.tolist()))
            if len(conds) < 2:
                continue
            imb, _ = temporal_balance(r.block, r.cond)
            dt = drift_table(r.power_w, r.block, r.cond)
            span = max(np.nanmax(v) - np.nanmin(v) for v in dt.values())
            eff = abs(block_bootstrap_ci(r.power_w, r.block, r.cond,
                                         conds[0], conds[1], n_boot=200)["diff"])
            rows.append((imb * span, imb, span, eff, man.parent.name, r.label,
                         r.is_aa_control))
    if not rows:
        print("  no CSVs on disk under this directory")
        return

    rows.sort(reverse=True)
    print(f"  {len(rows)} runs with raw data on disk\n")
    print(f"  {'bias mW':>8} {'imbal':>6} {'span W':>7} {'|eff| mW':>9} {'x':>6}  run")
    for b, i, s, e, run_id, label, is_aa in rows[:top]:
        ratio = e / b if b > 0 else float("inf")
        tag = " (A/A)" if is_aa else ""
        print(f"  {b * 1000:>8.1f} {i:>6.3f} {s:>7.3f} {e * 1000:>9.1f} "
              f"{ratio:>6.1f}  {run_id.split('-', 2)[-1]}/{label}{tag}")

    b = np.array([r[0] for r in rows])
    ratios = np.array([r[3] / r[0] for r in rows if r[0] > 0 and not r[6]])
    print(f"\n  admitted bias : median {np.median(b) * 1000:.1f} mW, "
          f"max {b.max() * 1000:.1f} mW")
    print(f"  effect / bias : median {np.median(ratios):.0f}x, "
          f"min {ratios.min():.1f}x  (A/A runs excluded, they have no effect)")
    print("\n  An effect smaller than its run's admitted bias is not separated from")
    print("  drift by the design alone. The gate passes at imbalance <= 0.10, which")
    print("  is loose; what saves the corpus is that achieved imbalance is far")
    print("  inside it, not that the threshold is well chosen.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dir", nargs="?", default="results")
    ap.add_argument("--check", choices=("period", "throughput", "bias", "all"),
                    default="all")
    args = ap.parse_args()

    runs = driver_runs(args.results_dir)
    if not runs:
        raise SystemExit(f"no runs with sampler fields under {args.results_dir}")

    if args.check in ("period", "all"):
        print("=" * 78)
        print("RAPL update period: the part, or the estimator?")
        print("=" * 78)
        check_period(runs)
    if args.check in ("throughput", "all"):
        print("\n" + "=" * 78)
        print("Achieved victim throughput across the corpus")
        print("=" * 78)
        check_throughput(runs)
    if args.check in ("bias", "all"):
        print("\n" + "=" * 78)
        print("Bias admitted by the interleaving gate, in watts")
        print("=" * 78)
        check_bias(args.results_dir)


if __name__ == "__main__":
    main()
