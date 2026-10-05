#!/usr/bin/env python3
"""Pin the two detector decision rules to theory on synthetic blocked data.

    ./venv/bin/python3 tests/test_detector_rule.py

`stats.accuracy_vs_n` scores an absolute mean-threshold detector (on-off keying
with a trained threshold); `stats.paired_accuracy_vs_n`, added for critique E4,
scores the covert receiver's actual rule (a paired, training-free Manchester
sign decision). On white within-block noise the two have closed-form accuracies
-- Phi(d'/2) for the threshold rule, Phi(d'/sqrt2) for the paired one, with
d' = Delta*sqrt(n)/sigma -- so a synthetic run with known Delta and sigma has a
right answer for both. This checks:

  * both rules match their closed form (which also confirms accuracy_vs_n still
    computes the absolute rule -- the do-not-degrade check, since that curve
    feeds the committed `detector` column and the A/A gate);
  * the paired rule is at least as accurate as the threshold rule at every n
    (sqrt2 < 2), which is why it needs fewer samples per chip;
  * an A/A (Delta = 0) decodes at chance under the paired rule.

Needs numpy, so it runs in the venv like test_covert_decode.py.
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.stats import accuracy_vs_n, paired_accuracy_vs_n  # noqa: E402

_fails = []


def check(name, cond, detail=""):
    print(f"  {name:<50} {'PASS' if cond else 'FAIL'}  {detail}")
    if not cond:
        _fails.append(name)


def Phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def synth(delta, sigma=1.0, n_blocks=60, block_len=200, seed=1):
    """Interleaved blocks, each holding one condition of i.i.d. N(mu, sigma)
    samples. Condition 1 sits delta above condition 0 -- white noise, so the
    closed-form accuracies apply."""
    rng = np.random.default_rng(seed)
    vals, blk, cnd = [], [], []
    for b in range(n_blocks):
        c = b % 2
        mu = delta if c == 1 else 0.0
        vals.append(rng.normal(mu, sigma, block_len))
        blk.append(np.full(block_len, b))
        cnd.append(np.full(block_len, c))
    return (np.concatenate(vals), np.concatenate(blk).astype(np.int64),
            np.concatenate(cnd).astype(np.int64))


def main():
    print("detector-rule checks (synthetic, white noise):")

    delta, sigma = 0.5, 1.0
    vals, blk, cnd = synth(delta, sigma)
    ns = [4, 8, 16, 32]
    ca, _ = accuracy_vs_n(vals, blk, cnd, 0, 1, ns=ns, trials=8000)
    cp, _ = paired_accuracy_vs_n(vals, blk, cnd, 0, 1, ns=ns, trials=8000)

    for n in ns:
        dprime = delta * math.sqrt(n) / sigma
        exp_abs = Phi(dprime / 2.0)
        exp_pair = Phi(dprime / math.sqrt(2.0))
        check(f"absolute n={n} ~ Phi(d'/2)={exp_abs:.3f}",
              abs(ca[n] - exp_abs) < 0.03, f"got {ca[n]:.3f}")
        check(f"paired   n={n} ~ Phi(d'/sqrt2)={exp_pair:.3f}",
              abs(cp[n] - exp_pair) < 0.03, f"got {cp[n]:.3f}")
        check(f"paired >= absolute at n={n}",
              cp[n] >= ca[n] - 0.01, f"{cp[n]:.3f} vs {ca[n]:.3f}")

    # A/A: no separation. A single A/A scatters (the finite test blocks have a
    # realized mean offset the large-n window cannot average away -- which is why
    # the project's A/A gate uses a 0.60 ceiling, not exact 0.5), so pin the
    # expectation over several seeds and the ceiling per seed.
    accs = []
    for s in range(6):
        va, ba, na = synth(0.0, sigma, seed=10 + s)
        cpa, _ = paired_accuracy_vs_n(va, ba, na, 0, 1, ns=[8, 32],
                                      trials=4000, seed=s)
        accs.append([cpa[8], cpa[32]])
    accs = np.array(accs)
    check("A/A paired unbiased in expectation (mean over 6 seeds)",
          abs(accs.mean() - 0.5) < 0.02, f"mean {accs.mean():.3f}")
    check("A/A paired under the 0.60 detection ceiling every seed",
          bool((accs < 0.60).all()), f"max {accs.max():.3f}")

    # The paired rule reaches a given accuracy at n <= the absolute rule's n:
    # on white noise it needs about half, which is why the receiver-rule rate is
    # not simply the absolute rate halved.
    cabig, _ = accuracy_vs_n(vals, blk, cnd, 0, 1,
                             ns=[2, 4, 8, 16, 32, 64], trials=8000)
    cpbig, _ = paired_accuracy_vs_n(vals, blk, cnd, 0, 1,
                                    ns=[2, 4, 8, 16, 32, 64], trials=8000)

    def first_at(curve, tgt):
        return next((n for n in sorted(curve) if curve[n] >= tgt), None)
    na85, np85 = first_at(cabig, 0.85), first_at(cpbig, 0.85)
    check("paired reaches 0.85 at n <= absolute's n",
          na85 and np85 and np85 <= na85, f"abs n={na85}, paired n={np85}")

    print()
    if _fails:
        print(f"{len(_fails)} FAILED: {', '.join(_fails)}")
        sys.exit(1)
    print("all detector-rule checks passed")


if __name__ == "__main__":
    main()
