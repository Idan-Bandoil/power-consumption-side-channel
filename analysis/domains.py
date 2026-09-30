"""Split an operand effect across the RAPL package sub-domains.

    ./venv/bin/python3 -m analysis.domains results/<run_id> [more dirs...]

Phase 0/1 measure the *package* energy counter, and an examiner's first
objection is that the effect lives in RAPL's activity model rather than in the
die. One corroboration is internal: the package is the sum of independently
metered sub-domains, and this part exposes two of them -- PP0 (the cores and
their caches) and PP1 (client graphics). If a package effect also appears,
cleanly and consistently, in a sub-counter the package model does not itself
produce, that is evidence the counter is structured rather than a flat
package-level estimate -- and it says *where* the operand effect sits.

For each non-A/A run this reports the condition difference in three domains:

    pkg      the package counter, MSR_PKG_ENERGY_STATUS -- the headline number
    core     PP0, MSR_PP0_ENERGY_STATUS -- cores + caches on this client part
    uncore   pkg - core -- everything else the package meters (PP1 is folded
             in, but it is idle here, so this is uncore to the graphics floor)

and the core share of the package effect. Deltas are between-repeat means with
a Student-t interval, matching analysis.aggregate; each repeat's own delta uses
a block bootstrap so within- and between-run uncertainty are both visible.

This is a cross-check and a mechanism result, not a gate. It does not decide
whether a run is valid -- analysis.report does that -- so it never fails; it
prints what the sub-domains say and lets the chapter read it.
"""
import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np

from .aggregate import t95
from .load import load_results
from .stats import block_bootstrap_ci


def _delta(values, run, n_boot):
    conds = sorted(set(run.cond.tolist()))
    a, b = conds[0], conds[1]
    ci = block_bootstrap_ci(values, run.block, run.cond, a, b, n_boot=n_boot)
    return ci["diff"], (ci["hi"] - ci["lo"]) / 2.0


def domain_effect(run, n_boot):
    """Per-repeat condition difference in each sub-domain, or None."""
    if len(set(run.cond.tolist())) < 2:
        return None
    if not run.has_domains:
        return {"missing": True, "repeat": int(run.meta.get("repeat", 0)),
                "is_aa": run.is_aa_control}
    d_pkg, w_pkg = _delta(run.power_w, run, n_boot)
    d_core, w_core = _delta(run.core_power_w, run, n_boot)
    d_unc, w_unc = _delta(run.uncore_power_w, run, n_boot)
    a = sorted(set(run.cond.tolist()))[0]
    return {
        "missing": False,
        "repeat": int(run.meta.get("repeat", 0)),
        "is_aa": run.is_aa_control,
        "d_pkg": d_pkg, "w_pkg": w_pkg,
        "d_core": d_core, "w_core": w_core,
        "d_unc": d_unc, "w_unc": w_unc,
        "pkg_base": float(run.power_w[run.mask(a)].mean()),
        "core_base": float(run.core_power_w[run.mask(a)].mean()),
        "unc_base": float(run.uncore_power_w[run.mask(a)].mean()),
        "pp1_base": float(run.pp1_power_w[run.mask(a)].mean()),
    }


def _agg(vals):
    """(mean, between-run SD, t-based 95% half-width) over repeats."""
    v = np.array([x for x in vals if np.isfinite(x)])
    if len(v) == 0:
        return float("nan"), float("nan"), float("nan")
    mean = float(v.mean())
    if len(v) < 2:
        return mean, float("nan"), float("nan")
    sd = float(v.std(ddof=1))
    half = t95(len(v) - 1) * sd / np.sqrt(len(v))
    return mean, sd, half


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dirs", nargs="+")
    ap.add_argument("--bootstrap", type=int, default=4000)
    args = ap.parse_args()

    groups = defaultdict(list)
    have_domains = False
    for d in args.results_dirs:
        _, runs = load_results(Path(d))
        for run in runs:
            e = domain_effect(run, args.bootstrap)
            if e:
                groups[run.label].append(e)
                have_domains = have_domains or not e["missing"]

    if not have_domains:
        print("None of these runs recorded the core/uncore split "
              "(they predate it). Nothing to report.")
        return

    print(f"{'label':<18} {'n':>2} {'dPKG W':>9} {'dCORE W':>9} {'dUNCORE W':>10} "
          f"{'core%':>6} {'baseW pkg/core/unc':>22}")
    print("-" * 92)

    for label in sorted(groups):
        es = [e for e in groups[label] if not e["missing"]]
        if not es:
            continue
        es.sort(key=lambda e: e["repeat"])
        aa = " (A/A)" if es[0]["is_aa"] else ""

        m_pkg, sd_pkg, h_pkg = _agg([e["d_pkg"] for e in es])
        m_core, sd_core, h_core = _agg([e["d_core"] for e in es])
        m_unc, sd_unc, h_unc = _agg([e["d_unc"] for e in es])
        # Core share of the package effect. Only meaningful when the package
        # effect is real and positive; a near-zero denominator (an A/A) makes
        # it noise, so it is suppressed there.
        share = 100.0 * m_core / m_pkg if abs(m_pkg) > 0.05 else float("nan")
        base = (f"{np.mean([e['pkg_base'] for e in es]):.1f}/"
                f"{np.mean([e['core_base'] for e in es]):.1f}/"
                f"{np.mean([e['unc_base'] for e in es]):.2f}")

        print(f"{label + aa:<18} {len(es):>2} {m_pkg:>+9.4f} {m_core:>+9.4f} "
              f"{m_unc:>+10.4f} {share:>5.0f}% {base:>22}")
        print(f"{'':<18}    between-run SD  pkg {sd_pkg:.4f}  core {sd_core:.4f}  "
              f"unc {sd_unc:.4f}   (per-repeat dPKG: "
              + " ".join(f"{e['d_pkg']:+.3f}" for e in es) + ")")

    print("\ndPKG/dCORE/dUNCORE : cond-difference in each domain, mean over repeats")
    print("core%    : core's share of the package effect (blank when pkg ~ 0)")
    print("uncore   : package minus core; PP1 (graphics) is folded in but idle")
    print("A run splitting cleanly and reproducibly across independently metered")
    print("sub-domains is evidence the package counter is structured, not a flat")
    print("activity estimate -- the RAPL-internal half of the cross-validation.")


if __name__ == "__main__":
    main()
