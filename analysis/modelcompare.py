"""Compare functional forms for leakage against operand Hamming weight.

    ./venv/bin/python3 -m analysis.modelcompare \
        results/20260901-213211-phase1_hamming_weight \
        results/20260902-211017-phase1_low_end

`analysis.hwfit` fits one shape -- a straight line dP = a + b*HW -- and reports
its slope and R^2. Chapter section 6 rejects three alternatives (sqrt(HW),
log(1+HW), a power law through the origin) against it on raw R^2. That
comparison has two problems this module fixes:

1. **Reproducibility.** `hwfit` only fits the line; nothing in the repo ever
   fitted the alternatives, so the rejection had no artifact behind it. This
   does, on exactly the data `hwfit --labels 'hw*'` uses (reusing its
   `contrast`), and it reproduces the published line as a self-check.

2. **Fairness.** Comparing raw R^2 across models with different intercept
   freedom penalises a model for honouring the origin. dP(0) = 0 holds by
   construction (the A/A row confirms it), and it is the one point known
   exactly -- yet the winning line misses it by ~23 SE. So every candidate here
   is given a **free intercept**, put on an equal footing, and compared on
   information criteria that charge for the extra parameters a curved form
   spends, not on raw R^2:

     - AIC / AICc / BIC, computed per repeat (one fit per repeat, the spread
       across repeats as the error bar -- `hwfit`'s rule), reported as a delta
       against the best model so the Gaussian constant cancels. AICc is the
       small-sample form; with n~20 points and up to 4 parameters it is the
       one to read.
     - leave-one-weight-out cross-validation: hold out every operand at one
       Hamming weight, fit on the rest, predict the held-out weight. This is
       assumption-free about the origin -- it asks only which shape predicts a
       weight it never saw -- and it is the check section 6 should have used.

No scipy: the two linear-in-parameters forms are closed-form least squares, and
the two with a shape parameter (power-law exponent, saturating tau) are a 1-D
grid search over that parameter with closed-form least squares at each node.

Reads `--axis hw` (default) exactly as `hwfit` does; `--labels` defaults to
'hw*' so the session anchor is kept out of the fit (letting it in quadruples
the x-range and changes which model looks best -- see CLAUDE.md).
"""
import argparse
from collections import defaultdict
from fnmatch import fnmatch
from pathlib import Path

import numpy as np

from .load import load_results
from .hwfit import AXES, contrast


# ---------------------------------------------------------------------------
# Model library. Each model maps a Hamming-weight vector x to a design matrix
# of basis columns (the intercept column is added by the fitter), plus a shape
# parameter it may grid-search over. A model is (name, n_regression_params,
# basis_fn, shape_grid). basis_fn(x, shape) returns the NON-intercept columns.
# ---------------------------------------------------------------------------

def _basis_linear(x, _s):
    return x[:, None]


def _basis_sqrt(x, _s):
    return np.sqrt(x)[:, None]


def _basis_log(x, _s):
    return np.log1p(x)[:, None]


def _basis_power(x, s):          # free-intercept power law: a + b*HW^s
    return (x ** s)[:, None]


def _basis_sat(x, s):            # saturating: a + b*(1 - exp(-HW/s))
    return (1.0 - np.exp(-x / s))[:, None]


# shape grids for the nonlinear forms (None => no shape parameter)
_POWER_GRID = np.arange(0.20, 2.5001, 0.01)
_SAT_GRID = np.geomspace(0.5, 128.0, 400)

MODELS = [
    # name              p_reg  basis          shape_grid    free_intercept
    ("linear",            2, _basis_linear, None,          True),
    ("sqrt",              2, _basis_sqrt,   None,          True),
    ("log1p",             2, _basis_log,    None,          True),
    ("power(free a)",     3, _basis_power,  _POWER_GRID,   True),
    ("saturating",        3, _basis_sat,    _SAT_GRID,     True),
    # the one section 6 actually used, kept so the table shows the contrast:
    # a power law *through the origin*, no intercept.
    ("power(origin)",     2, _basis_power,  _POWER_GRID,   False),
]


def _fit_fixed_shape(x, y, basis, shape, free_intercept):
    """Closed-form least squares for a given shape parameter. Returns
    (sse, beta) where beta packs intercept (if any) then the basis coefs."""
    cols = basis(x, shape)
    if free_intercept:
        design = np.column_stack([np.ones(len(x)), cols])
    else:
        design = cols
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = y - design @ beta
    return float(resid @ resid), beta


def fit_model(x, y, model):
    """Fit one model to (x, y). Grid-searches the shape parameter if the model
    has one. Returns dict with sse, r2, the chosen shape, and the parameter
    count p (regression params, excluding the noise variance)."""
    _, p_reg, basis, grid, free_int = model
    if grid is None:
        sse, beta = _fit_fixed_shape(x, y, basis, None, free_int)
        shape = None
    else:
        best = None
        for s in grid:
            sse_s, beta_s = _fit_fixed_shape(x, y, basis, s, free_int)
            if best is None or sse_s < best[0]:
                best = (sse_s, beta_s, s)
        sse, beta, shape = best
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - sse / ss_tot if ss_tot > 0 else float("nan")
    return {"sse": sse, "r2": r2, "shape": shape, "p": p_reg, "beta": beta}


def info_criteria(sse, n, p):
    """AIC, AICc, BIC for a Gaussian model with p regression parameters.

    k = p + 1 counts the estimated noise variance. The additive Gaussian
    constant n*(ln(2*pi)+1) is dropped; it is identical across models at fixed
    n, so it cancels in every delta reported here."""
    k = p + 1
    ll_term = n * np.log(sse / n)
    aic = ll_term + 2 * k
    denom = n - k - 1
    aicc = aic + (2 * k * (k + 1) / denom if denom > 0 else np.inf)
    bic = ll_term + k * np.log(n)
    return aic, aicc, bic


def loocv_by_weight(points, model):
    """Leave-one-weight-out CV. Hold out every operand at one Hamming weight,
    fit on the rest (pooled across repeats), predict the held-out points.
    Returns RMSE over all held-out predictions. Assumption-free about the
    origin: it never uses dP(0) = 0, only predictive accuracy at unseen
    weights."""
    x = np.array([p["x"] for p in points], float)
    y = np.array([p["diff"] for p in points], float)
    _, p_reg, basis, grid, free_int = model
    weights = sorted(set(x.tolist()))
    sq = []
    for w in weights:
        tr = x != w
        te = x == w
        if tr.sum() < p_reg + 1:
            continue
        xt, yt = x[tr], y[tr]
        if grid is None:
            _, beta = _fit_fixed_shape(xt, yt, basis, None, free_int)
            shape = None
        else:
            best = None
            for s in grid:
                sse_s, beta_s = _fit_fixed_shape(xt, yt, basis, s, free_int)
                if best is None or sse_s < best[0]:
                    best = (sse_s, beta_s, s)
            _, beta, shape = best
        cols = basis(x[te], shape)
        design = (np.column_stack([np.ones(te.sum()), cols]) if free_int
                  else cols)
        pred = design @ beta
        sq.extend(((y[te] - pred) ** 2).tolist())
    return float(np.sqrt(np.mean(sq))) if sq else float("nan")


def load_points(dirs, labels, axis):
    pts = []
    for d in dirs:
        _, runs = load_results(Path(d))
        for run in runs:
            if labels and not run.is_aa_control and not fnmatch(run.label, labels):
                continue
            # contrast's `diff` (the point estimate we fit on) is deterministic;
            # a small n_boot just satisfies its CI path, whose width we ignore.
            c = contrast(run, 256, axis)
            if c:
                pts.append(c)
    return pts


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dirs", nargs="+")
    ap.add_argument("--axis", choices=sorted(AXES), default="hw")
    ap.add_argument("--labels", default="hw*",
                    help="glob restricting which runs are fitted (default hw*, "
                         "which keeps the session anchor out of the fit)")
    args = ap.parse_args()
    axis = AXES[args.axis]

    pts = load_points(args.results_dirs, args.labels, axis)
    fit_pts = [p for p in pts if not p["is_aa"]]
    aa = [p for p in pts if p["is_aa"]]
    if not fit_pts:
        raise SystemExit("no usable runs")

    by_repeat = defaultdict(list)
    for p in fit_pts:
        by_repeat[p["repeat"]].append(p)
    repeats = sorted(by_repeat)
    n_labels = len(set(p["label"] for p in fit_pts))

    print(f"Model comparison for dP vs {axis.short}, free intercept on every "
          f"candidate.")
    print(f"{n_labels} operand labels, {len(fit_pts)} contrasts over "
          f"{len(repeats)} repeats; {len(by_repeat[repeats[0]])} points per "
          f"repeat fit.")
    if aa:
        print(f"A/A control: {1000*np.mean([p['diff'] for p in aa]):+.1f} mW "
              f"over {len(aa)} repeats (dP(0)=0 by construction).")
    print()

    # ---- per-repeat fits, then aggregate ----------------------------------
    per_rep = {m[0]: {"r2": [], "aic": [], "aicc": [], "bic": [], "shape": []}
               for m in MODELS}
    for rep in repeats:
        ps = by_repeat[rep]
        x = np.array([p["x"] for p in ps], float)
        y = np.array([p["diff"] for p in ps], float)
        n = len(x)
        for m in MODELS:
            f = fit_model(x, y, m)
            aic, aicc, bic = info_criteria(f["sse"], n, f["p"])
            per_rep[m[0]]["r2"].append(f["r2"])
            per_rep[m[0]]["aic"].append(aic)
            per_rep[m[0]]["aicc"].append(aicc)
            per_rep[m[0]]["bic"].append(bic)
            if f["shape"] is not None:
                per_rep[m[0]]["shape"].append(f["shape"])

    # deltas within each repeat, against that repeat's best AICc
    daicc = {m[0]: [] for m in MODELS}
    daic = {m[0]: [] for m in MODELS}
    dbic = {m[0]: [] for m in MODELS}
    for i, rep in enumerate(repeats):
        best_aicc = min(per_rep[m[0]]["aicc"][i] for m in MODELS)
        best_aic = min(per_rep[m[0]]["aic"][i] for m in MODELS)
        best_bic = min(per_rep[m[0]]["bic"][i] for m in MODELS)
        for m in MODELS:
            daicc[m[0]].append(per_rep[m[0]]["aicc"][i] - best_aicc)
            daic[m[0]].append(per_rep[m[0]]["aic"][i] - best_aic)
            dbic[m[0]].append(per_rep[m[0]]["bic"][i] - best_bic)

    # ---- LOOCV over weights (pooled) --------------------------------------
    loocv = {m[0]: loocv_by_weight(fit_pts, m) for m in MODELS}

    def ms(v):
        v = np.array(v, float)
        if len(v) > 1:
            return f"{v.mean():.2f} ±{v.std(ddof=1):.2f}"
        return f"{v.mean():.2f}"

    order = sorted(MODELS, key=lambda m: np.mean(daicc[m[0]]))
    print(f"{'model':<15} {'par':>3} {'R^2':>6} {'shape':>6} {'dAIC':>13} "
          f"{'dAICc':>13} {'dBIC':>13} {'LOOCV':>9}")
    print("-" * 85)
    for m in order:
        name = m[0]
        r2 = np.mean(per_rep[name]["r2"])
        sh = per_rep[name]["shape"]
        shape_s = f"{np.mean(sh):.2f}" if sh else "-"
        print(f"{name:<15} {m[1]:>3} {r2:>6.3f} {shape_s:>6} "
              f"{ms(daic[name]):>13} {ms(daicc[name]):>13} {ms(dbic[name]):>13} "
              f"{1000*loocv[name]:>7.1f}m")

    best = order[0][0]
    print()
    print(f"Best by mean dAICc: {best}.  Deltas are per-repeat, each against "
          f"that repeat's best, then meaned.")
    print("AICc (small-sample) is the one to read at n~20 with up to 4 params. "
          "A dAICc >~10")
    print("is decisive against a model; 4-7 is substantial. LOOCV RMSE is the "
          "held-out error")
    print("predicting an entire Hamming weight the fit never saw, and uses "
          "dP(0)=0 nowhere.")


if __name__ == "__main__":
    main()
