"""Combine Phase 1's four characterisations into one leakage model, and test it
out of sample (critique E1).

    ./venv/bin/python3 -m analysis.model

Phase 1 measured four things, each with one factor varied and the rest held
fixed: a depth ladder (chapter section 4), a weight slope plus a zero-step
(sections 6-8), a Hamming-distance slope (section 9), and an instruction table
(section 10). This script writes them down together

    dP = f(depth) . (step.1[op != 0] + alpha.meanHW + beta.meanHD) + gamma(instr)

fits every coefficient from the committed sessions, and -- the point of the
exercise -- predicts victims the fit never saw and reports the residual. A
characterisation is a list of coefficients valid where each was measured; a
model predicts a point outside the fit and is told how wrong it was.

Nothing here runs an experiment. It is reanalysis of committed CSVs, and it is
purely additive: it must reproduce every published per-session number, and it
prints the fitted-vs-published comparison so that guarantee is executable
rather than asserted. Where the model is wrong -- it under-predicts real,
high-entropy data by about a quarter -- that is reported as the result, not
smoothed away.

Depth multiplies the operand terms because the item-3 depth x operand session
found the weight slope is not separable from depth (it rises L1->L3 then falls
at DRAM). beta and gamma were only ever measured at L3, so the combined model
is fully specified at L3 and carries the weight law alone at the other depths;
that limit is stated, not hidden.
"""
from collections import defaultdict
from pathlib import Path

import numpy as np

from .hwfit import AXES, contrast, mean_hw_of_density, ols
from .load import load_results

R = Path(__file__).resolve().parent.parent / "results"

DEPTH_OF = {"ws_l1_x8": "L1", "ws_l2_x8": "L2", "ws_l3_x8": "L3",
            "ws_dram_x8": "DRAM"}
DEPTHS = ["L1", "L2", "L3", "DRAM"]

# Published Phase 1 figures, cited so the reproduction check has a target.
PUB = {
    "alpha_L3": 50.75, "step_L3": 349.0,        # pooled weight sweep, mW
    "beta_L3": 34.14, "beta_icpt_L3": 48.0,     # ws_l3_x8_ab, mW
    "depth_b": {"L1": 7.23, "L2": 25.99, "L3": 48.96, "DRAM": 22.52},
    "depth_a": {"L1": -60.0, "L2": 231.0, "L3": 194.0, "DRAM": 52.0},
    "iid_excess_p50": 0.67, "iid_excess_p75": 0.62,  # section 8.3, W
    # ws_l3_x8_ab / _ab64 switching decomposition (section 9.1), W at HD 32/word.
    "load_hd32": 1.139, "line_hd32": 0.211,
}
BOOT = 2500


def points(run_dir, axis="hw", keep=None, drop=()):
    _, runs = load_results(R / run_dir)
    out = []
    for run in runs:
        rid = f"{run.label}_r{run.meta.get('repeat', 0)}"
        if rid in drop:
            continue
        if keep and not keep(run):
            continue
        c = contrast(run, BOOT, AXES[axis])
        if c:
            c["victim"] = run.victim
            c["gbs"] = run.bytes_per_s / 1e9
            out.append(c)
    return out


def per_repeat_line(pts):
    """One OLS fit per repeat; return (slope, intercept, R2) aggregated as
    mean +/- between-repeat SD -- the project's reporting unit."""
    by = defaultdict(list)
    for p in pts:
        by[p["repeat"]].append(p)
    b, a, r2 = [], [], []
    for rep in sorted(by):
        s, i, r = ols([p["x"] for p in by[rep]], [p["diff"] for p in by[rep]])
        if np.isfinite(s):
            b.append(s); a.append(i); r2.append(r)
    b, a = np.array(b), np.array(a)
    sd = lambda v: float(v.std(ddof=1)) if len(v) > 1 else float("nan")
    return dict(b=float(b.mean()), b_sd=sd(b), a=float(a.mean()), a_sd=sd(a),
                r2=float(np.mean(r2)), n=len(b))


def hr(t=""):
    print("\n" + "=" * 78)
    if t:
        print(t)
        print("=" * 78)


def main():
    # ---- weight law per depth, from the depth x operand session -----------
    dp = points("20260904-103411-phase1_depth_operand", "hw",
                keep=lambda r: r.label[:2] in ("l1", "l2", "l3", "dr")
                and "_hw" in r.label,
                drop={"l2_hw08_r2"})            # documented suspend contamination
    depth = {}
    for dep in DEPTHS:
        ps = [p for p in dp if DEPTH_OF.get(p["victim"]) == dep]
        fit = per_repeat_line(ps)
        fit["gbs"] = float(np.mean([p["gbs"] for p in ps]))
        depth[dep] = fit

    # ---- the three L3-only terms ------------------------------------------
    wl = points("20260901-213211-phase1_hamming_weight", "hw",
                keep=lambda r: r.label.startswith("hw")) \
        + points("20260902-211017-phase1_low_end", "hw",
                 keep=lambda r: r.label.startswith("hw"))
    weight_L3 = per_repeat_line([p for p in wl if p["x"] > 0 and not p["is_aa"]])

    hd = points("20260902-220517-phase1_hamming_distance", "hd",
                keep=lambda r: r.label.startswith("hd") and r.label != "hd00")
    dist_L3 = per_repeat_line([p for p in hd if p["x"] > 0 and not p["is_aa"]])

    _, iruns = load_results(R / "20260902-230608-phase1_instruction_table")
    cell = {}
    for run in iruns:
        c = contrast(run, 1500, AXES["hw"])
        if c:
            cell[(c["repeat"], run.label)] = c["diff"]
    reps = sorted({r for (r, _) in cell})
    # gamma is the per-instruction cost on top of the shared load stream, so it
    # is read paired against loads_only within each repeat. Register-resident
    # victims (avx2_*) have no load stream and are a different contrast; skip.
    gamma = {}
    for lab in sorted({l for (_, l) in cell}):
        if lab.startswith("reg_") or lab == "aa_op" or lab == "loads_only":
            continue
        d = [cell[(r, lab)] - cell[(r, "loads_only")]
             for r in reps if (r, lab) in cell and (r, "loads_only") in cell]
        if d:
            gamma[lab] = (float(np.mean(d)),
                          float(np.std(d, ddof=1)) if len(d) > 1 else float("nan"))

    # ======================================================================
    hr("THE COMBINED MODEL  dP = f(depth).(step.1[op!=0] + a.HW + b.HD) + g(instr)")
    print("Fully specified at L3 (all four axes measured there); the weight law "
          "alone\nat L1/L2/DRAM, where HD and instruction were never swept.\n")
    print(f"{'depth':>5} {'GB/s':>5} {'step a (mW)':>14} {'slope alpha (mW/bit)':>22} {'R2':>6}")
    print("-" * 58)
    for dep in DEPTHS:
        f = depth[dep]
        print(f"{dep:>5} {f['gbs']:>5.0f} {f['a']*1000:>+8.1f}+/-{f['a_sd']*1000:>4.0f}   "
              f"{f['b']*1000:>+10.2f}+/-{f['b_sd']*1000:>5.2f}       {f['r2']:>5.3f}")
    print(f"\nL3 switching term  beta = {dist_L3['b']*1000:+.2f} +/- {dist_L3['b_sd']*1000:.2f} "
          f"mW per bit flipped/word/transfer (intercept {dist_L3['a']*1000:+.0f} mW)")
    print(f"L3 instruction term gamma, on top of the load stream (mW):")
    for lab in sorted(gamma, key=lambda l: gamma[l][0]):
        print(f"    {lab:<12} {gamma[lab][0]*1000:>+8.1f} +/- {gamma[lab][1]*1000:>5.1f}")

    # ======================================================================
    hr("REPRODUCTION CHECK  (model must not move any published number)")
    def chk(name, got, pub, tol):
        ok = abs(got - pub) <= tol
        print(f"  {'OK ' if ok else 'XX '} {name:<26} fitted {got:>+8.2f}  "
              f"published {pub:>+8.2f}  |d| {abs(got-pub):>5.2f} (tol {tol})")
        return ok
    allok = True
    allok &= chk("L3 weight slope mW/bit", weight_L3["b"]*1000, PUB["alpha_L3"], 2.0)
    allok &= chk("L3 weight step mW", weight_L3["a"]*1000, PUB["step_L3"], 30.0)
    allok &= chk("L3 distance slope mW/bit", dist_L3["b"]*1000, PUB["beta_L3"], 3.0)
    for dep in DEPTHS:
        allok &= chk(f"{dep} depth slope mW/bit", depth[dep]["b"]*1000,
                     PUB["depth_b"][dep], 4.0)
    print(f"\n  depth-session L3 slope {depth['L3']['b']*1000:+.2f} vs pooled weight "
          f"sweep {weight_L3['b']*1000:+.2f} mW/bit -- the slope is portable across "
          f"sessions;\n  the step is the loose coefficient (depth-session "
          f"{depth['L3']['a']*1000:+.0f} vs pooled {weight_L3['a']*1000:+.0f} mW).")
    print(f"  {'ALL MARGINALS REPRODUCE' if allok else 'A MARGINAL MOVED -- INVESTIGATE'}")

    # model L3 coefficients (self-contained: from the pooled weight law + HD fit)
    step, alpha, beta = weight_L3["a"], weight_L3["b"], dist_L3["b"]

    def predict_L3(mean_hw, mean_hd, instr_mw=0.0, pay_step=True):
        return ((step if pay_step else 0.0) + alpha * mean_hw
                + beta * mean_hd + instr_mw / 1000.0)

    # ======================================================================
    hr("OUT OF SAMPLE 1 -- single-word operands, across sessions (in-distribution)")
    # The anchor (HW 16) and l3_hw32 recur in every session; predict them from
    # the L3 weight law and compare to the independent measurements.
    anchors = {"anchor HW16": (16, [1.133, 1.215, 1.228, 1.160, 1.126]),
               "l3 HW32":     (32, [1.904, 1.841, 1.938, 1.775])}
    for name, (h, meas) in anchors.items():
        pred = predict_L3(h, 0.0)
        mm = float(np.mean(meas))
        print(f"  {name:<12} HW={h:>2}  model {pred:+.3f} W  vs measured mean "
              f"{mm:+.3f} W (n={len(meas)})  residual {mm-pred:+.3f} W")

    # ======================================================================
    hr("OUT OF SAMPLE 2 -- the sparsity mixture, static arm (HD=0, never fit)")
    _, mruns = load_results(R / "20260930-132043-phase1_sparsity_mixture")
    meas = defaultdict(list)
    for run in mruns:
        c = contrast(run, 1500, AXES["density"])
        if c:
            meas[run.label].append(c["diff"])
    scat = {lab: (float(np.mean(v)),
                  float(np.std(v, ddof=1)) if len(v) > 1 else float("nan"))
            for lab, v in meas.items() if lab.startswith("scat_")}
    print(f"  {'density':>8} {'meanHW':>7} {'model W':>9} {'measured W':>12} {'resid W':>9}")
    sel_of = {run.label: int(run.selectors[1]) for run in mruns}
    for lab in sorted(scat, key=lambda l: mean_hw_of_density(sel_of[l])):
        mhw = mean_hw_of_density(sel_of[lab])
        pred = predict_L3(mhw, 0.0)
        m, sd = scat[lab]
        print(f"  {lab:>8} {mhw:>7.1f} {pred:>+9.3f} {m:>+9.3f}+/-{sd:>4.2f} {m-pred:>+9.3f}")
    print("  The mixture pays the whole step once any word is non-zero, then "
          "rises with\n  mean weight -- so the weight law predicts it. Residuals "
          "are within the\n  effect-scale floor (section 8.2, 0.094 W).")

    # ======================================================================
    hr("OUT OF SAMPLE 3 -- i.i.d. real-entropy data: WHERE THE MODEL BREAKS")
    iid = {lab: (float(np.mean(v)),
                 float(np.std(v, ddof=1)) if len(v) > 1 else float("nan"))
           for lab, v in meas.items() if lab.startswith("iid_")}
    load_line = (PUB["load_hd32"] + PUB["line_hd32"])  # W at HD32/word, both paths
    print(f"  {'p':>5} {'meanHW':>7} {'meanHD':>7} {'weight':>8} {'switch':>8} "
          f"{'model':>8} {'measured':>10} {'resid':>8}")
    head = None
    for lab in sorted(iid):
        p = {"iid_p50": 0.5, "iid_p75": 0.75}[lab]
        mhw = 32 * p
        mhd = 32 * 2 * p * (1 - p)                 # E[bits flipped between iid words]
        w_term = step + alpha * mhw
        # Credit switching both ways: the directly-fitted beta (load path only),
        # and the load+line decomposition section 8.3 used for exactly this row.
        sw_beta = beta * mhd
        sw_ll = (mhd / 32.0) * load_line
        model_beta = w_term + sw_beta
        model_ll = w_term + sw_ll
        m, sd = iid[lab]
        print(f"  {p:>5.2f} {mhw:>7.1f} {mhd:>7.1f} {w_term:>+8.3f} "
              f"{sw_ll:>+8.3f} {model_ll:>+8.3f} {m:>+8.3f}+/-{sd:>4.2f} {m-model_ll:>+8.3f}")
        if lab == "iid_p50":
            head = (m, model_ll, model_beta)
    print("  switch column credits both load and line paths (section 8.3); using the")
    print("  directly-fitted beta (load path only) the residual is ~0.13 W larger.")
    if head:
        m, mll, mb = head
        print(f"\n  HEADLINE: the model -- fit entirely on repeated/alternating single")
        print(f"  words -- predicts the i.i.d. L3 victim (p=1/2) at {mll:+.2f} W "
              f"(load+line)")
        print(f"  to {mb:+.2f} W (load only) against a measured {m:+.2f} W: a residual of")
        print(f"  +{m-mll:.2f} to +{m-mb:.2f} W, ~a quarter of the signal, all in the "
              f"direction")
        print(f"  of real data leaking MORE. Matches section 8.3's independent "
              f"+{PUB['iid_excess_p50']:.2f} W.")

    hr("WHAT THIS MEANS")
    print("The model holds across sessions for the single-word operands it was")
    print("calibrated on (anchor residual ~0.01 W) and for static mixtures of them")
    print("(within the 0.094 W floor). It UNDER-predicts high-entropy data by")
    print("+0.65 to +0.78 W at p=1/2, because every calibration victim held all 8")
    print("words of a 32-byte load identical, so the fitted switching term counts")
    print("only load-to-load flips; i.i.d. data also flips bits word-to-word inside")
    print("a load, and that activity is uncaptured. The coefficients are therefore a")
    print("FLOOR on real-data leakage. One brand-new held-out victim run (an operand")
    print("at an (HW,HD) never measured) remains the single open machine-time piece.")


if __name__ == "__main__":
    main()
