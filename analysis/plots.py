"""Figures. Uses the Agg backend so it works headless and under sudo."""
import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

from .stats import gaussian_kde


def _cond_label(run, c):
    sel = run.selectors[c]
    return f"cond {c}: {sel} (HW {bin(sel & 0xFFFFFFFF).count('1')}/32)"


def density(run, out_path, title=None):
    """Power distribution per condition -- the proposal's Figure 1 view."""
    p = run.power_w
    lo, hi = np.percentile(p, [0.5, 99.5])
    grid = np.linspace(lo, hi, 512)

    fig, ax = plt.subplots(figsize=(9, 5))
    for c in sorted(set(run.cond.tolist())):
        v = p[run.cond == c]
        v = v[(v >= lo) & (v <= hi)]
        ax.plot(grid, gaussian_kde(v, grid), lw=2, label=_cond_label(run, c))
        ax.fill_between(grid, gaussian_kde(v, grid), alpha=0.18)

    ax.set_xlabel("Package power (W)")
    ax.set_ylabel("Density")
    ax.set_title(title or f"{run.label}: power distribution by condition")
    ax.legend(title="Operand")
    ax.grid(alpha=0.3, ls="--")
    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)


def drift(run, out_path):
    """Per-block mean power against chronological block index.

    Interleaving means both conditions are scattered across the whole run; if
    the two colours overlap in time but separate in power, the effect cannot
    be thermal drift."""
    from .stats import block_means
    means, conds = block_means(run.power_w, run.block, run.cond)
    ids = np.unique(run.block)

    fig, ax = plt.subplots(figsize=(10, 4.5))
    for c in sorted(set(conds.tolist())):
        m = conds == c
        ax.plot(ids[m], means[m], ".", ms=5, alpha=0.75, label=_cond_label(run, c))

    ax.set_xlabel("Block index (chronological)")
    ax.set_ylabel("Mean package power (W)")
    ax.set_title(f"{run.label}: per-block power over the run")
    ax.legend()
    ax.grid(alpha=0.3, ls="--")
    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)


def accuracy(curve, out_path, run_label, period_ms):
    """Detector accuracy vs integration length, with the implied bit rate."""
    ns = sorted(curve)
    acc = [curve[n] for n in ns]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.semilogx(ns, acc, "o-", lw=2)
    ax.axhline(0.5, color="gray", ls=":", label="chance")
    ax.axhline(0.99, color="crimson", ls="--", lw=1, label="99%")
    ax.set_xlabel("Samples per decision (n)")
    ax.set_ylabel("Held-out accuracy")
    ax.set_ylim(0.4, 1.02)
    ax.set_title(f"{run_label}: detector accuracy "
                 f"(1 sample ≈ {period_ms:.2f} ms)")
    ax.legend()
    ax.grid(alpha=0.3, ls="--", which="both")

    sec = ax.secondary_xaxis(
        "top", functions=(lambda n: 1000.0 / np.maximum(n, 1e-9) / period_ms,
                          lambda r: 1000.0 / np.maximum(r, 1e-9) / period_ms))
    sec.set_xlabel("Implied raw bit rate (bit/s)")

    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
