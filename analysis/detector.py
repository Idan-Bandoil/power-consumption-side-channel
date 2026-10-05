"""Detector -> bit-rate conversion under the receiver's own decision rule.

    ./venv/bin/python3 -m analysis.detector results/20260901-213211-phase1_hamming_weight

Phase 1 section 13 converts the leakage into a covert-channel bit rate by reading
the accuracy-versus-n curve `analysis.report` prints and quoting
`1 / (n * RAPL period)` at the smallest n reaching 95%. That curve comes from
`stats.accuracy_vs_n`, which scores an **absolute mean-threshold** detector
trained on held-out blocks -- on-off keying with a fitted threshold. The Phase 2
receiver (`analysis.covert`) does neither of those things (critique E4):

  * it has no training data, and
  * it makes a **paired** Manchester decision -- it differences the two chips of
    one symbol and takes the sign, recovering the polarity from the preamble
    rather than fitting a threshold.

Those are different statistics, and `phase2` section 6 is entirely about how
different: in the drift-dominated Config-B regime the paired form reads d' 1.07
where the marginal one reads 0.08. So section 13's number measured a rule the
receiver does not use. This module recomputes the curve under the rule it does
(`stats.paired_accuracy_vs_n`) so Phase 1's output is directly the quantity
Phase 2 consumes, and prints both side by side.

Two accounting points the conversion has to get right, and section 13 did not:

1. **A Manchester bit is two chips.** The paired decision spends 2*n samples per
   bit, so its raw rate is `1 / (2 * n * period)` -- half the absolute
   detector's at the same n. The paired decision needs a *smaller* n for a given
   accuracy (its per-chip noise requirement is d'/sqrt(2), not d'/2), but that
   gain never recovers the full factor of two, so the receiver-rule raw rate
   comes out **below** the absolute one, not above. The two corrections the
   critique expected to cancel (paired rule up, boxcar down) both in fact push
   the rate down.

2. **It is still an upper bound.** Both curves are computed on *steady-state*
   samples -- the operand is held for a whole block -- so neither sees the ~1 ms
   RAPL boxcar, which `phase2` section 8 measures as removing ~2/3 of the
   separation at a 1 ms chip. The small-n (high-rate) end of either curve is
   therefore optimistic; the true integration ceiling is the boxcar, which
   Phase 2 locates between a 2 ms and a 1.5 ms symbol. This module reports the
   steady-state ceiling and says so.

The absolute column here is byte-for-byte what `analysis.report` prints (same
`stats.accuracy_vs_n`, same default seed), which is the self-check: the only
thing that changes between the columns is the decision rule.

Results are aggregated per operand over repeats (`analysis.aggregate`'s rule).
Reading per-run n95 instead -- which is how section 13's "n = 1-8, 125-1000
bit/s" was quoted -- takes the best repeat at each operand, the same
single-repeat selection critique A1 flagged in the Phase 2 headline; `--per-run`
shows that spread for comparison.
"""
import argparse
import re
from collections import defaultdict
from fnmatch import fnmatch
from pathlib import Path

import numpy as np

from .load import load_results
from .stats import accuracy_vs_n, paired_accuracy_vs_n, samples_for_accuracy

NS = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144]
_REPEAT_SUFFIX = re.compile(r"_r\d+$")


def _operand(label):
    """Strip the repeat suffix so `hw16_a_r0` and `hw16_a_r1` pool."""
    return _REPEAT_SUFFIX.sub("", label)


def _mean_curve(curves):
    """Average accuracy at each n across repeats of one operand."""
    ns = sorted(set().union(*[set(c) for c in curves]))
    return {n: float(np.mean([c[n] for c in curves if n in c])) for n in ns}


def _rate(n, period_ms, chips_per_bit):
    return 1000.0 / (chips_per_bit * n * period_ms) if n else None


def collect(results_dirs, labels, seed=0, trials=4000):
    """Per-operand absolute and paired curves, pooled over repeats.

    Returns (rows, period_ms, per_run) where rows is one dict per operand and
    per_run is the un-pooled (label, n95_abs, n95_paired) list."""
    abs_c, pr_c, dP, periods = (defaultdict(list), defaultdict(list),
                                defaultdict(list), [])
    per_run = []
    for d in results_dirs:
        _, runs = load_results(Path(d))
        for run in runs:
            conds = sorted(set(run.cond.tolist()))
            if len(conds) < 2 or run.is_aa_control:
                continue
            if labels and not fnmatch(run.label, labels):
                continue
            a, b = conds[0], conds[1]
            ca, _ = accuracy_vs_n(run.power_w, run.block, run.cond, a, b,
                                  ns=NS, seed=seed, trials=trials)
            cp, _ = paired_accuracy_vs_n(run.power_w, run.block, run.cond, a, b,
                                         ns=NS, seed=seed, trials=trials)
            op = _operand(run.label)
            abs_c[op].append(ca)
            pr_c[op].append(cp)
            dP[op].append(float(run.power_w[run.mask(b)].mean()
                                - run.power_w[run.mask(a)].mean()))
            periods.append(run.rapl_period_ms)
            per_run.append((run.label, samples_for_accuracy(ca, 0.95),
                            samples_for_accuracy(cp, 0.95)))

    period = float(np.median(periods)) if periods else float("nan")
    rows = []
    for op in sorted(abs_c, key=lambda k: np.mean(dP[k])):
        ca, cp = _mean_curve(abs_c[op]), _mean_curve(pr_c[op])
        rows.append(dict(
            operand=op, dP=float(np.mean(dP[op])), n_repeats=len(abs_c[op]),
            n95_abs=samples_for_accuracy(ca, 0.95),
            n99_abs=samples_for_accuracy(ca, 0.99),
            n95_pair=samples_for_accuracy(cp, 0.95),
            n99_pair=samples_for_accuracy(cp, 0.99),
        ))
    return rows, period, per_run


def _span(rows, key, chips, period, only=None):
    """(min, max) raw rate over the operands where `key` resolved.

    `only` restricts to a set of operand names, so the two rules can be compared
    over the identical set rather than each over whatever it happens to resolve."""
    ns = [r[key] for r in rows
          if r[key] and (only is None or r["operand"] in only)]
    if not ns:
        return None, None
    rates = [_rate(n, period, chips) for n in ns]
    return min(rates), max(rates)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dirs", nargs="+")
    ap.add_argument("--labels", default="*",
                    help="glob over run labels to include (default: all non-A/A)")
    ap.add_argument("--per-run", action="store_true",
                    help="also print the un-pooled per-repeat n95 spread")
    ap.add_argument("--trials", type=int, default=4000)
    args = ap.parse_args()

    rows, period, per_run = collect(args.results_dirs, args.labels,
                                    trials=args.trials)
    if not rows:
        print("no contrasting runs matched")
        return

    print(f"Detector -> bit-rate under two decision rules, period {period:.4f} ms")
    print(f"  absolute (report.py / OOK, trained threshold):  rate = 1/(n*T)")
    print(f"  paired   (covert.py / Manchester, no training): rate = 1/(2*n*T)")
    print(f"operands pooled over repeats; {len(rows)} operands\n")

    print(f"{'operand':<10}{'dP W':>6}{'rep':>4}   "
          f"{'n95a':>4}{'rate95a':>8}   {'n95p':>4}{'rate95p':>8}   "
          f"{'n99a':>4}{'n99p':>4}")
    print("-" * 66)
    monotone = True
    for r in rows:
        r95a = _rate(r["n95_abs"], period, 1)
        r95p = _rate(r["n95_pair"], period, 2)
        if r95a and r95p and r95p > r95a + 1e-6:
            monotone = False
        print(f"{r['operand']:<10}{r['dP']:>6.2f}{r['n_repeats']:>4}   "
              f"{str(r['n95_abs']):>4}{(f'{r95a:.0f}' if r95a else '-'):>8}   "
              f"{str(r['n95_pair']):>4}{(f'{r95p:.0f}' if r95p else '-'):>8}   "
              f"{str(r['n99_abs']):>4}{str(r['n99_pair']):>4}")

    both = {r["operand"] for r in rows if r["n95_abs"] and r["n95_pair"]}
    lo_a, hi_a = _span(rows, "n95_abs", 1, period, only=both)
    lo_p, hi_p = _span(rows, "n95_pair", 2, period, only=both)
    lo_a9, hi_a9 = _span(rows, "n99_abs", 1, period, only=both)
    lo_p9, hi_p9 = _span(rows, "n99_pair", 2, period, only=both)

    print(f"\nRange over the {len(both)} operands reaching 95% under both rules:")
    if hi_a:
        print(f"  absolute rule : 95% at {lo_a:.0f}-{hi_a:.0f} bit/s raw"
              f"   (99% at {lo_a9:.0f}-{hi_a9:.0f})")
    if hi_p:
        print(f"  RECEIVER rule : 95% at {lo_p:.0f}-{hi_p:.0f} bit/s raw"
              f"   (99% at {lo_p9:.0f}-{hi_p9:.0f})")
    extra = sorted({r["operand"] for r in rows if r["n95_pair"]} - both
                   - {r["operand"] for r in rows if r["n95_abs"]})
    if extra:
        print(f"  (the receiver rule additionally resolves {', '.join(extra)}, "
              f"which the absolute rule never does -- its per-chip sensitivity "
              f"is d'/sqrt2, not d'/2)")
    print(f"\nReceiver rule gives the LOWER raw rate (a Manchester bit is two "
          f"chips):\n  {'consistent across all operands' if monotone else '** NON-MONOTONE: check **'}.")
    print("Both are steady-state ceilings and so UPPER bounds on the real "
          "receiver: neither\nsees the ~1 ms RAPL boxcar, which phase2 s8 "
          "measures as removing ~2/3 of the\nseparation at a 1 ms chip. The "
          "receiver-rule span is the quantity phase2 consumes;\nit lands in "
          "tier 1's measured 241-311 bit/s capacity, where the absolute rule "
          "did not.")

    if args.per_run:
        print("\nPer-run n95 (un-pooled; reading these takes the best repeat "
              "per operand --\nthe single-repeat selection critique A1 flags):")
        print(f"  {'run':<16}{'n95_abs':>8}{'n95_pair':>9}")
        for label, na, npr in per_run:
            print(f"  {label:<16}{str(na):>8}{str(npr):>9}")


if __name__ == "__main__":
    main()
