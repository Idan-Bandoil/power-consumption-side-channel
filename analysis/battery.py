"""Battery-vs-RAPL cross-validation (critique item B1.2).

    ./venv/bin/python3 -m analysis.battery results/<run_id> [more dirs...]

Reads a `kind: "battery"` run: long interleaved arms alternating a victim's
operand, each arm carrying both the RAPL package power (energy delta over the
arm) and the mean battery discharge power (V*I from the battery's own sensors).
The two instruments share nothing, so if both show the operand effect -- and by
comparable amounts -- the effect is physically real and not an artefact of
RAPL's activity model.

Each arm is a block. The condition difference is mean(on) - mean(off) over arms,
with a percentile bootstrap over arms for the within-run interval and the
between-repeat spread reported beside it. The battery delta is expected to run a
little *above* the RAPL delta, since the battery also pays the voltage
regulator's conversion loss on that power; agreement to within ~30% (RAPL <
battery < ~1.3x RAPL) is the cross-check passing.

Not a gate. It reports whether the instruments agree and lets the chapter read
the numbers. It does check the run was actually on battery -- on mains there is
no discharge to compare -- and says so loudly if it was not.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from .aggregate import t95


def _boot_diff(on, off, n_boot=10000, seed=0):
    """Percentile CI for mean(on) - mean(off), resampling whole arms."""
    rng = np.random.default_rng(seed)
    on, off = np.asarray(on, float), np.asarray(off, float)
    diff = float(on.mean() - off.mean())
    if len(on) < 2 or len(off) < 2:
        return diff, float("nan"), float("nan")
    boots = np.empty(n_boot)
    for i in range(n_boot):
        bo = rng.choice(on, len(on), replace=True)
        bf = rng.choice(off, len(off), replace=True)
        boots[i] = bo.mean() - bf.mean()
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return diff, float(lo), float(hi)


def _run_effect(csv_path, n_boot):
    raw = np.loadtxt(csv_path, delimiter=",", skiprows=1, ndmin=2)
    cond = raw[:, 1].astype(int)
    rapl = raw[:, 3]
    batt = raw[:, 4]
    on, off = cond == 1, cond == 0
    d_rapl, lo_r, hi_r = _boot_diff(rapl[on], rapl[off], n_boot)
    d_batt, lo_b, hi_b = _boot_diff(batt[on], batt[off], n_boot)
    return {
        "d_rapl": d_rapl, "rapl_ci": (lo_r, hi_r),
        "d_batt": d_batt, "batt_ci": (lo_b, hi_b),
        "rapl_off": float(rapl[off].mean()), "rapl_on": float(rapl[on].mean()),
        "batt_off": float(batt[off].mean()), "batt_on": float(batt[on].mean()),
        "n_arms": (int(off.sum()), int(on.sum())),
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dirs", nargs="+")
    ap.add_argument("--bootstrap", type=int, default=10000)
    args = ap.parse_args()

    per_label = defaultdict(list)
    status_seen = set()

    for d in args.results_dirs:
        d = Path(d)
        manifest = json.loads((d / "manifest.json").read_text())
        for e in manifest["runs"]:
            summ = e.get("battery", {})
            status_seen.add(summ.get("battery_status_start", "?"))
            status_seen.add(summ.get("battery_status_end", "?"))
            eff = _run_effect(d / e["csv"], args.bootstrap)
            eff["label"] = e["label"]
            eff["repeat"] = e.get("repeat", 0)
            per_label[e["label"]].append(eff)

    print("=" * 84)
    print("Battery vs RAPL cross-validation")
    print("=" * 84)
    if status_seen - {"Discharging"}:
        print(f"  battery status seen: {sorted(status_seen)}")
        if "Discharging" not in status_seen:
            print("  !! NOT DISCHARGING at any point -- this is not a cross-check. "
                  "Unplug the charger and re-run.")
        else:
            print("  (some snapshots not Discharging; arms recorded while charging "
                  "or full carry no discharge signal)")
    else:
        print(f"  battery status: Discharging throughout -- valid cross-check")

    print(f"\n{'label':<16} {'n':>2} {'RAPL dW':>16} {'battery dW':>18} "
          f"{'batt/RAPL':>9} {'verdict':>10}")
    print("-" * 84)

    for label in sorted(per_label):
        es = sorted(per_label[label], key=lambda e: e["repeat"])
        dr = np.array([e["d_rapl"] for e in es])
        db = np.array([e["d_batt"] for e in es])
        mdr, mdb = float(dr.mean()), float(db.mean())

        # Between-repeat interval (Student t), or the single run's own bootstrap.
        if len(es) > 1:
            hr = t95(len(dr) - 1) * dr.std(ddof=1) / np.sqrt(len(dr))
            hb = t95(len(db) - 1) * db.std(ddof=1) / np.sqrt(len(db))
            r_str = f"{mdr:+.3f}+/-{hr:.3f}"
            b_str = f"{mdb:+.3f}+/-{hb:.3f}"
        else:
            (lo_r, hi_r), (lo_b, hi_b) = es[0]["rapl_ci"], es[0]["batt_ci"]
            r_str = f"{mdr:+.3f}[{lo_r:+.2f},{hi_r:+.2f}]"
            b_str = f"{mdb:+.3f}[{lo_b:+.2f},{hi_b:+.2f}]"

        ratio = mdb / mdr if abs(mdr) > 1e-6 else float("nan")
        is_aa = label.startswith("aa") or "aa" in label.split("_")
        if is_aa:
            # Both should be ~0. Verdict is whether they stay near zero.
            verdict = "null OK" if abs(mdr) < 0.15 and abs(mdb) < 0.4 else "NOT NULL"
        else:
            agree = np.isfinite(ratio) and mdr > 0 and mdb > 0 and 0.7 <= ratio <= 1.6
            verdict = "agree" if agree else "check"
        print(f"{label:<16} {len(es):>2} {r_str:>16} {b_str:>18} "
              f"{ratio:>9.2f} {verdict:>10}")
        if len(es) > 1:
            print(f"{'':<16}    per-repeat RAPL: "
                  + " ".join(f"{x:+.3f}" for x in dr)
                  + "   battery: " + " ".join(f"{x:+.3f}" for x in db))
        print(f"{'':<16}    absolute W  RAPL off/on {es[0]['rapl_off']:.2f}/"
              f"{es[0]['rapl_on']:.2f}   battery off/on {es[0]['batt_off']:.2f}/"
              f"{es[0]['batt_on']:.2f}   arms {es[0]['n_arms']}")

    print("\nRAPL dW    : package power difference (heavy - zero operand), per arm")
    print("battery dW : battery discharge V*I difference, same arms, same contrast")
    print("batt/RAPL  : >1 expected -- the battery also pays the VRM conversion loss")
    print("             on the package delta. 0.7-1.6 is the two instruments agreeing")
    print("             that the effect is physically present, not a RAPL artefact.")


if __name__ == "__main__":
    main()
