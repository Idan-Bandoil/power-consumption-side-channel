"""Does bit placement matter at a fixed Hamming weight? (critique C5.)

    ./venv/bin/python3 -m analysis.placement results/<run_id>

Phase 1 §11 claimed a placement term from a heuristic (spread <= 2*between) that
fired at 2 of 8 weights, with no multiplicity control and only three repeats. The
exact paired permutation floor at three repeats is 0.25, so that design could not
have resolved it either way. This reads the six-repeat focused session on HW 8
and tests it properly.

The patterns were interleaved across the same repeats, so repeat index is a
session-wide thermal state shared by every pattern. The omnibus test is therefore
a *blocked* permutation: within each repeat the pattern labels are exchangeable
under the null, so permuting them within each repeat and recomputing the spread
of pattern means gives an exact test that cancels the between-repeat variance --
the same variance that swamped the three-repeat design. The pairwise
hw08_a-vs-hw08_b test (the original 0.162 W hit) is a paired sign-flip over the
six repeats, whose floor is 2/2^6 = 0.031.

Not a gate. It reports whether placement is resolved at this weight, and against
what noise floor. The effect-scale floor cited is `sham` from phase1 §8.2
(0.094 W), the between-run SD of an operand contrasted against its complement at
matched weight and distance.
"""
import argparse
import itertools
from collections import defaultdict
from pathlib import Path

import numpy as np

from .aggregate import t95
from .load import load_results
from .stats import block_means

SHAM_FLOOR = 0.094  # phase1 §8.2, effect-scale between-run SD
ANCHOR_PRIOR = "+1.133 / +1.215 / +1.228 / +1.160"


def run_dp(run):
    conds = sorted(set(run.cond.tolist()))
    m, c = block_means(run.power_w, run.block, run.cond)
    return float(m[c == conds[1]].mean() - m[c == conds[0]].mean())


def blocked_perm_p(mat, n_perm=50000, seed=0):
    """Range of column (pattern) means, permuting labels within each row (repeat).

    mat is [repeats, patterns]. Returns (observed range, p)."""
    rng = np.random.default_rng(seed)
    obs = float(np.ptp(mat.mean(axis=0)))
    count = 1
    for _ in range(n_perm):
        p = np.array([rng.permutation(row) for row in mat])
        if np.ptp(p.mean(axis=0)) >= obs - 1e-12:
            count += 1
    return obs, count / (n_perm + 1)


def signflip_p(diffs):
    """Exact two-sided paired sign-flip p for mean(diffs)."""
    d = np.asarray(diffs)
    obs = abs(d.mean())
    stats = [abs((d * np.array(s)).mean()) for s in itertools.product([1, -1], repeat=len(d))]
    return obs, float(np.mean([s >= obs - 1e-12 for s in stats]))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dirs", nargs="+",
                    help="one or more placement sessions; pooling a top-up run is "
                         "valid because each (session, repeat) is its own block")
    ap.add_argument("--permutations", type=int, default=50000)
    args = ap.parse_args()

    # label -> {block: dP}, where block = (session index, repeat) so that a run
    # split across sessions contributes distinct blocks. The blocked permutation
    # only ever exchanges labels *within* a block, so two sessions at different
    # thermal levels pool without confounding the pattern effect.
    by = defaultdict(dict)
    aa, anchor = [], []
    for si, d in enumerate(args.results_dirs):
        _, runs = load_results(Path(d))
        for r in runs:
            dp = run_dp(r)
            block = (si, int(r.meta.get("repeat", 0)))
            if r.is_aa_control:
                aa.append(dp)
            elif r.label.startswith("anchor"):
                anchor.append(dp)
            else:
                by[r.label][block] = dp

    pats = sorted(by)
    reps = sorted(set.intersection(*[set(by[p]) for p in pats])) if pats else []
    print("=" * 78)
    print("Bit placement at a fixed Hamming weight (critique C5)")
    print("=" * 78)

    print(f"\n{'pattern':<10} {'mean dP':>9} {'SD':>7} {'95% CI':>18}   per-repeat")
    print("-" * 78)
    means = {}
    for p in pats:
        v = np.array([by[p][r] for r in reps])
        means[p] = v
        m, sd = v.mean(), v.std(ddof=1)
        half = t95(len(v) - 1) * sd / np.sqrt(len(v))
        print(f"{p:<10} {m:>+9.4f} {sd:>7.4f} [{m-half:>+6.3f}, {m+half:>+6.3f}]   "
              + " ".join(f"{x:+.3f}" for x in v))

    if len(pats) >= 2:
        mat = np.array([[by[p][r] for p in pats] for r in reps])  # [repeat, pattern]
        spread = float(np.ptp(mat.mean(axis=0)))
        print(f"\nbetween-pattern spread (range of means): {spread:.4f} W")
        print(f"effect-scale noise floor (sham, §8.2):   {SHAM_FLOOR:.4f} W")

        obs, p = blocked_perm_p(mat, n_perm=args.permutations)
        print(f"\nOMNIBUS -- does placement matter at all?")
        print(f"  blocked permutation (pattern labels shuffled within each repeat)")
        print(f"  spread {obs:.4f} W, p = {p:.4f} over {args.permutations} permutations")
        verdict = ("RESOLVED: placement matters at this weight" if p < 0.05
                   else "not resolved: placement not distinguishable from zero")
        print(f"  {verdict}")

    if "hw08_a" in by and "hw08_b" in by:
        r = sorted(set(by["hw08_a"]) & set(by["hw08_b"]))
        d = np.array([by["hw08_a"][k] - by["hw08_b"][k] for k in r])
        obs, p = signflip_p(d)
        print(f"\nORIGINAL HIT -- hw08_a vs hw08_b, paired over {len(d)} repeats")
        print(f"  mean {d.mean():+.4f} W (3-repeat sweep read -0.162 W the other way)")
        print(f"  per-repeat: " + " ".join(f"{x:+.3f}" for x in d))
        print(f"  sign-flip p = {p:.4f} (floor {2/2**len(d):.3f} at {len(d)} repeats)")

    if anchor:
        a = np.array(anchor)
        print(f"\nanchor_hw16: {a.mean():+.4f} W (SD {a.std(ddof=1):.4f}); "
              f"prior sessions {ANCHOR_PRIOR}")
    if aa:
        a = np.array(aa)
        print(f"aa_l3 (A/A): {a.mean():+.4f} W (SD {a.std(ddof=1):.4f})")

    print("\nblocked permutation is exact under the null that pattern labels are")
    print("exchangeable within a repeat; it cancels the between-repeat thermal state")
    print("that the three-repeat heuristic could not, which is why six repeats can")
    print("resolve what three could not.")


if __name__ == "__main__":
    main()
