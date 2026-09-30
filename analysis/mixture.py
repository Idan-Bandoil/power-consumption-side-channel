"""Is the cheap-zero step per-transfer or per-stream? (critique E2.)

    ./venv/bin/python3 -m analysis.mixture results/<run_id> [more dirs...]

Reads the sparsity-mixture sweep: mixtures of all-zero and 0xFFFFFFFF words at
density d (fraction non-zero), each contrasted against the all-zero buffer.

Which arm is which -- and this was first got backwards. The datapath moves 32-byte
loads and 64-byte lines, and Phase 1's ab/ab64 pair showed that toggling between
*consecutive transfers* is what costs switching power. tests/fillcheck.c measures
it with the real fill code:

  * SCATTERED (ws_sparse_l3_x8) spreads non-zero words one at a time, with a
    period of 1-8 words at every density swept, so every 32-byte load is the
    same 256-bit pattern and NOTHING toggles between transfers. It is the static
    arm: a mixture with no switching.
  * BLOCKED (ws_sparse_l3_x8_blk) groups them into 64-byte runs, so consecutive
    lines alternate between all-zero and all-ones: 128/256/512/256 bits toggle
    per line at d = 1/8, 1/4, 1/2, 3/4, and none at d = 1. It is the switching arm.

The question, on the static arm:
  per-transfer -- each zero word carries its own share of the zero discount,
      so dP(d) = d * dP(1): a line through the origin in mean Hamming weight.
  per-stream   -- the discount belongs to the all-zero stream and is lost as
      soon as any word is non-zero, so the mixture pays the full step, and
      dP follows Phase 1's uniform-word law, step + slope * mean HW.

Tested three ways: a per-repeat paired ratio (dP(d)/dP(1) against d, which
cancels the session's common scale), the static arm's own step (hwfit --axis
density reports it too), and the mixture against Phase 1's uniform-word curve at
matched mean Hamming weight, scaled to this session's d = 1 anchor.

Then: blocked minus scattered is the switching term, with d = 1 -- where the two
buffers are bit-identical -- as its noise reference; and the iid victim, whose
words genuinely differ, against the static arm plus Phase 1's switching terms.

Statistics are between-repeat with Student's t, per the project rule: at three
repeats the normal approximation is badly optimistic. Not a gate.
"""
import argparse
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from .aggregate import t95
from .load import load_results
from .stats import block_bootstrap_ci

# label suffix -> density / bit-probability
DENS = {"d125": 0.125, "d25": 0.25, "d50": 0.5, "d75": 0.75, "d100": 1.0,
        "p50": 0.5, "p75": 0.75}

# Published Phase 1 reference coefficients, cited so the comparison can be
# checked. Uniform-word law, pooled weight sweep (results/20260901-213211-
# phase1_hamming_weight + results/20260902-211017-phase1_low_end):
#   dP = 0.349 + 0.0508 * HW  (W), dP(0) = 0.
P1_STEP, P1_SLOPE = 0.349, 0.0508
# Switching terms at HD 32 per word, from ws_l3_x8_ab / _ab64
# (results/20260902-220517-phase1_hamming_distance): load path 1.139 W, line
# path 0.211 W -- an estimate from two points and an additivity assumption.
P1_LOAD_HD32, P1_LINE_HD32 = 1.139, 0.211


def _parse(label):
    if label.startswith("blk_"):
        return "blk", DENS.get(label[4:])
    if label.startswith("scat_"):
        return "scat", DENS.get(label[5:])
    if label.startswith("iid_"):
        return "iid", DENS.get(label[4:])
    return None, None


def t_two_sided_p(t, df):
    """Two-sided p for Student's t, by integrating the density (no scipy)."""
    if not np.isfinite(t) or df < 1:
        return float("nan")
    c = math.gamma((df + 1) / 2) / (math.sqrt(df * math.pi) * math.gamma(df / 2))
    x = np.linspace(abs(t), abs(t) + 2000.0, 400001)
    pdf = c * (1 + x ** 2 / df) ** (-(df + 1) / 2)
    tail = float(np.sum((pdf[1:] + pdf[:-1]) * np.diff(x)) / 2)
    return min(1.0, 2 * tail)


def _one_sample(vals, null=0.0):
    """mean, SD, SE, t, df, p for H0: mean == null."""
    v = np.array([x for x in vals if np.isfinite(x)])
    if len(v) < 2:
        return float(np.mean(v)) if len(v) else float("nan"), *(float("nan"),) * 5
    m, sd = float(v.mean()), float(v.std(ddof=1))
    se = sd / np.sqrt(len(v))
    t = (m - null) / se if se > 0 else float("nan")
    return m, sd, se, t, len(v) - 1, t_two_sided_p(t, len(v) - 1)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dirs", nargs="+")
    ap.add_argument("--bootstrap", type=int, default=2000)
    args = ap.parse_args()

    # (mode, x) -> {repeat: dP}
    eff = defaultdict(dict)
    aa = []
    for d in args.results_dirs:
        _, runs = load_results(Path(d))
        for run in runs:
            conds = sorted(set(run.cond.tolist()))
            ci = block_bootstrap_ci(run.power_w, run.block, run.cond,
                                    conds[0], conds[1], n_boot=args.bootstrap)
            rep = int(run.meta.get("repeat", 0))
            if run.is_aa_control:
                aa.append(ci["diff"])
                continue
            mode, x = _parse(run.label)
            if mode:
                eff[(mode, x)][rep] = ci["diff"]

    def mean_of(mode, x):
        v = list(eff.get((mode, x), {}).values())
        return float(np.mean(v)) if v else float("nan")

    def sd_of(mode, x):
        v = list(eff.get((mode, x), {}).values())
        return float(np.std(v, ddof=1)) if len(v) > 1 else float("nan")

    hr = "=" * 84
    print(hr + "\nSparsity mixture (critique E2): per-transfer or per-stream?\n" + hr)
    if aa:
        m, sd, *_ = _one_sample(aa)
        print(f"A/A (same density both sides): {m:+.4f} W, SD {sd:.4f} over {len(aa)}"
              f" -- the density-mode path reads {'zero' if abs(m) < 0.05 else 'NOT ZERO'}")

    dens = sorted(x for (mode, x) in eff if mode == "scat")
    F = mean_of("scat", 1.0)

    # ---- 1. static arm against both models --------------------------------
    # Phase 1's uniform-word law, rescaled so HW 32 matches this session's own
    # d = 1 point: sessions differ in overall scale by ~0.1 W, and it is the
    # shape that distinguishes the models, not the level.
    scale = F / (P1_STEP + P1_SLOPE * 32) if np.isfinite(F) else float("nan")
    print(f"\n1. STATIC arm (scattered, nothing toggles between transfers)")
    print(f"   anchor dP(d=1) = {F:+.3f} W; Phase 1 law rescaled by {scale:.3f}")
    print(f"   {'d':>6} {'mean HW':>8} {'dP W':>8} {'SD':>6} {'per-transfer':>13} "
          f"{'per-stream':>11} {'resid PT':>9} {'resid PS':>9}")
    print("   " + "-" * 77)
    for x in dens:
        h = 32 * x
        m, sd = mean_of("scat", x), sd_of("scat", x)
        pt = x * F
        ps = scale * (P1_STEP + P1_SLOPE * h)
        print(f"   {x:>6.3f} {h:>8.1f} {m:>+8.3f} {sd:>6.3f} {pt:>13.3f} "
              f"{ps:>11.3f} {m - pt:>+9.3f} {m - ps:>+9.3f}")
    print("   per-transfer: d * dP(1), through the origin. per-stream: Phase 1's")
    print("   step + slope * mean HW -- the mixture pays the whole step.")

    # ---- 2. paired per-repeat test of per-transfer ------------------------
    # Per-transfer says dP_r(d) / dP_r(1) = d in every repeat r. Dividing by the
    # repeat's own d = 1 cancels the between-run scale that dominates the
    # unpaired spread; averaging a repeat's deviations over d < 1 then gives one
    # independent number per repeat.
    per_rep = defaultdict(list)
    full = eff.get(("scat", 1.0), {})
    for x in dens:
        if x >= 1.0:
            continue
        for rep, v in eff[("scat", x)].items():
            if rep in full and abs(full[rep]) > 1e-6:
                per_rep[rep].append(v / full[rep] - x)
    rep_means = [np.mean(v) for _, v in sorted(per_rep.items())]
    m, sd, se, t, df, p = _one_sample(rep_means)
    print(f"\n2. PAIRED test of per-transfer on the static arm")
    print(f"   per repeat, mean over d<1 of [dP(d)/dP(1) - d]: "
          + "  ".join(f"{v:+.3f}" for v in rep_means))
    print(f"   mean {m:+.3f} (SD {sd:.3f}); per-transfer predicts 0.000")
    print(f"   t = {t:.2f} on {df} df, two-sided p = {p:.3f}"
          f"  (95% needs |t| > {t95(df):.2f})")
    ps_dev = [ (scale * (P1_STEP + P1_SLOPE * 32 * x)) / F - x
               for x in dens if x < 1.0 ]
    print(f"   per-stream predicts {np.mean(ps_dev):+.3f} from the rescaled Phase 1 law")

    # ---- 3. switching arm --------------------------------------------------
    print(f"\n3. SWITCHING term = blocked - scattered, same density")
    print(f"   {'d':>6} {'line toggle':>12} {'blocked W':>10} {'scattered W':>12} "
          f"{'switch W':>9} {'Phase1 line+load':>17}")
    print("   " + "-" * 73)
    # Line toggle fraction for a 16-word Bresenham block pattern at density d:
    # 2 boundaries per period when 0 < d < 1 (measured in fillcheck).
    frac = {0.125: 0.25, 0.25: 0.5, 0.5: 1.0, 0.75: 0.5, 1.0: 0.0}
    for x in dens:
        b, s = mean_of("blk", x), mean_of("scat", x)
        # Each toggling line flips all 32 bits of every word (0 <-> 0xFFFFFFFF),
        # i.e. HD 32; loads toggle at half the line rate (2 loads per line).
        pred = frac.get(x, float("nan")) * (P1_LINE_HD32 + 0.5 * P1_LOAD_HD32)
        print(f"   {x:>6.3f} {frac.get(x, float('nan')):>12.2f} {b:>+10.3f} {s:>+12.3f} "
              f"{b - s:>+9.3f} {pred:>17.3f}")
    print("   d = 1 holds bit-identical buffers, so its blocked - scattered is pure")
    print("   run-to-run and placement noise: the floor the other rows sit on.")

    # ---- 4. iid ------------------------------------------------------------
    iid = sorted(x for (mode, x) in eff if mode == "iid")
    if iid:
        print(f"\n4. IID words against the static arm plus Phase 1's switching")
        print(f"   {'p':>6} {'iid dP W':>9} {'SD':>6} {'static':>8} {'+switch':>8} "
              f"{'model':>7} {'excess W':>9}")
        print("   " + "-" * 60)
        for x in iid:
            m_i, sd_i = mean_of("iid", x), sd_of("iid", x)
            static = mean_of("scat", x)
            hd = 32 * 2 * x * (1 - x)          # mean bits flipped per word
            sw = (hd / 32) * (P1_LOAD_HD32 + P1_LINE_HD32)
            model = static + sw
            print(f"   {x:>6.3f} {m_i:>+9.3f} {sd_i:>6.3f} {static:>+8.3f} "
                  f"{sw:>+8.3f} {model:>+7.3f} {m_i - model:>+9.3f}")
        print("   static: this session's scattered arm at the same mean HW (32p).")
        print("   switch: Phase 1's load+line terms scaled to iid's mean HD, 64p(1-p).")

    print("\nVerdict rule (pre-committed in the spec, read on the static arm):")
    print("  per-transfer -> dP(d)/dP(1) = d;  per-stream -> the mixture carries the")
    print("  whole step and follows the uniform-word law. Either way, density is")
    print("  recoverable if dP rises monotonically with d; what changes is whether")
    print("  the cheap zero or the per-bit weight term is what carries it.")


if __name__ == "__main__":
    main()
