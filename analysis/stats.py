"""Statistics for blocked, interleaved side-channel measurements.

Everything here respects the block structure. Samples inside one block share a
thermal and frequency state, so treating them as independent would inflate
significance enormously (300k correlated samples will "prove" anything). The
bootstrap and the permutation test therefore both resample *blocks*.
"""
import numpy as np


def block_means(values, block, cond):
    """Collapse each block to its mean. Returns (means, cond_per_block)."""
    ids = np.unique(block)
    means = np.empty(len(ids), dtype=float)
    conds = np.empty(len(ids), dtype=np.int64)
    for i, b in enumerate(ids):
        m = block == b
        means[i] = values[m].mean()
        conds[i] = cond[m][0]
    return means, conds


def cohens_d(a, b):
    """Per-sample standardised effect size; this is what sets detector cost."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return float("nan")
    pooled = np.sqrt(((na - 1) * a.var(ddof=1) + (nb - 1) * b.var(ddof=1)) / (na + nb - 2))
    return float((b.mean() - a.mean()) / pooled) if pooled > 0 else float("nan")


def block_bootstrap_ci(values, block, cond, a, b, n_boot=10000, seed=0, alpha=0.05):
    """Percentile CI for mean(cond b) - mean(cond a), resampling whole blocks."""
    rng = np.random.default_rng(seed)
    means, conds = block_means(values, block, cond)
    ba, bb = means[conds == a], means[conds == b]
    if len(ba) < 2 or len(bb) < 2:
        return dict(diff=float("nan"), lo=float("nan"), hi=float("nan"), n_blocks=(len(ba), len(bb)))

    obs = bb.mean() - ba.mean()
    draws = (rng.choice(bb, (n_boot, len(bb)), replace=True).mean(axis=1)
             - rng.choice(ba, (n_boot, len(ba)), replace=True).mean(axis=1))
    lo, hi = np.percentile(draws, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return dict(diff=float(obs), lo=float(lo), hi=float(hi),
                n_blocks=(int(len(ba)), int(len(bb))))


def block_permutation_test(values, block, cond, a, b, n_perm=10000, seed=0):
    """Exchange condition labels between blocks under H0.

    This is the test that the pre-2026 sequential design could not support:
    with one long block per condition there is nothing to permute, so drift
    and condition are inseparable."""
    rng = np.random.default_rng(seed)
    means, conds = block_means(values, block, cond)
    sel = np.isin(conds, [a, b])
    means, conds = means[sel], conds[sel]

    obs = abs(means[conds == b].mean() - means[conds == a].mean())
    labels = (conds == b).astype(bool)
    na = int((~labels).sum())
    count = 0
    for _ in range(n_perm):
        perm = rng.permutation(labels)
        stat = abs(means[perm].mean() - means[~perm].mean())
        if stat >= obs:
            count += 1
    # +1/+1 keeps the estimate unbiased and never reports p == 0
    return dict(observed=float(obs), p=(count + 1) / (n_perm + 1), n_a=na)


def drift_table(values, block, cond, nbins=10):
    """Mean value per condition across bins of chronological block order.

    Blocks are emitted in time order, so a flat row per condition is direct
    evidence that thermal drift is not being read as a condition effect."""
    ids = np.unique(block)
    out = {}
    for c in sorted(set(cond.tolist())):
        rows = []
        cblocks = [b for b in ids if cond[block == b][0] == c]
        edges = np.linspace(0, len(cblocks), nbins + 1).astype(int)
        for i in range(nbins):
            chunk = cblocks[edges[i]:edges[i + 1]]
            if not chunk:
                rows.append(float("nan"))
                continue
            m = np.isin(block, chunk)
            rows.append(float(values[m].mean()))
        out[c] = rows
    return out


def temporal_balance(block, cond):
    """How evenly each condition is spread across the run, in [0, 1].

    Blocks are numbered chronologically, so the mean block index of a
    condition says when it was measured. Proper interleaving puts every
    condition's mean near the middle of the run; the old sequential design put
    them at 1/4 and 3/4, which is precisely what let thermal drift masquerade
    as a condition effect in out-1207-2115.

    Returns (imbalance, {cond: normalised mean position}). Imbalance is the
    spread of those positions: ~0 is interleaved, 0.5 is fully sequential.
    """
    ids = np.unique(block)
    pos = {}
    n = len(ids)
    index = {b: i for i, b in enumerate(ids)}
    for c in sorted(set(cond.tolist())):
        cb = [index[b] for b in ids if cond[block == b][0] == c]
        pos[c] = float(np.mean(cb) / max(n - 1, 1))
    imbalance = max(pos.values()) - min(pos.values()) if len(pos) > 1 else 0.0
    return float(imbalance), pos


def accuracy_vs_n(values, block, cond, a, b, ns=None, seed=0, trials=4000,
                  train_frac=0.5):
    """Accuracy of a mean-threshold detector given n samples per decision.

    Blocks (not samples) are split into train and test, so the threshold never
    sees the data it is scored on. Windows are drawn inside a single block,
    which is the constraint a real receiver faces: one symbol is one block.

    Returns (curve {n: accuracy}, info).
    """
    rng = np.random.default_rng(seed)
    ids = np.unique(block)
    ids = ids[np.isin([cond[block == i][0] for i in ids], [a, b])]
    rng.shuffle(ids)
    split = int(len(ids) * train_frac)
    train_ids, test_ids = ids[:split], ids[split:]

    tr = np.isin(block, train_ids)
    mu_a = values[tr & (cond == a)].mean()
    mu_b = values[tr & (cond == b)].mean()
    threshold = (mu_a + mu_b) / 2.0
    b_is_high = mu_b > mu_a

    # Group test samples by block, preserving order within a block.
    per_block = {}
    for i in test_ids:
        m = block == i
        per_block[i] = (values[m], int(cond[m][0]))
    block_len = min(len(v) for v, _ in per_block.values())

    if ns is None:
        ns = [n for n in (1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144) if n <= block_len]
        if block_len not in ns:
            ns.append(block_len)

    a_blocks = [i for i in test_ids if per_block[i][1] == a]
    b_blocks = [i for i in test_ids if per_block[i][1] == b]

    curve = {}
    for n in ns:
        if n > block_len:
            continue
        correct = 0
        for _ in range(trials):
            want_b = rng.random() < 0.5
            pool = b_blocks if want_b else a_blocks
            if not pool:
                continue
            vals, _ = per_block[pool[rng.integers(len(pool))]]
            start = rng.integers(0, len(vals) - n + 1)
            m = vals[start:start + n].mean()
            guess_b = (m > threshold) if b_is_high else (m < threshold)
            correct += int(guess_b == want_b)
        curve[int(n)] = correct / trials

    return curve, dict(threshold=float(threshold), b_is_high=bool(b_is_high),
                       block_len=int(block_len), n_train=len(train_ids),
                       n_test=len(test_ids))


def samples_for_accuracy(curve, target=0.99):
    """Smallest tested n reaching `target`, or None. Also the covert-channel
    symbol cost: bits/s is roughly 1 / (n * RAPL period)."""
    for n in sorted(curve):
        if curve[n] >= target:
            return n
    return None


def gaussian_kde(x, grid, bw=None):
    """Scott's-rule Gaussian KDE. Written out because this venv has no scipy."""
    x = np.asarray(x, dtype=float)
    if bw is None:
        bw = 1.06 * x.std(ddof=1) * len(x) ** (-1 / 5)
    if not np.isfinite(bw) or bw <= 0:
        bw = 1e-6
    z = (grid[:, None] - x[None, :]) / bw
    return np.exp(-0.5 * z ** 2).sum(axis=1) / (len(x) * bw * np.sqrt(2 * np.pi))
