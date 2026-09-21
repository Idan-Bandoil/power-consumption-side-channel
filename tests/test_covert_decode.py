#!/usr/bin/env python3
"""Decoder checks against synthetic traces, so the framing maths is testable
without hardware.

    ./venv/bin/python3 tests/test_covert_decode.py

Needs numpy (it exercises analysis/covert.py), so it runs in the venv rather
than as the stdlib-only runner tests do. What it pins down is the part that
silently produces plausible-but-wrong numbers on real data: the Manchester
convention, frame indexing, and the fact that sync is recovered rather than
assumed. A synthetic trace has a known answer; a real one does not.
"""
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.covert import Trace, bits_to_chips, decode_run  # noqa: E402

TSC_HZ = 2.7e9
ENERGY_UNIT = 1.0 / (1 << 14)
RAPL_MS = 0.976
PREAMBLE = "1111100110101"

_fails = []


def check(name, cond, detail=""):
    print(f"  {name:<52} {'PASS' if cond else 'FAIL'}  {detail}")
    if not cond:
        _fails.append(name)


def synth_run(payload, symbol_us, delta_w, base_w=20.0, noise_w=0.0,
              frames=4, code="manchester", lead_ms=300.0, seed=1,
              overshoots=0, late_frac=0.0):
    """A transmission and the trace a receiver would have recorded of it.

    Power follows the chip pattern exactly; the receiver samples it on a
    ~1 ms grid with jitter, quantised to whole RAPL energy units. Sample
    boundaries therefore straddle chip boundaries, which is the property the
    decoder's windowed integration exists to handle.
    """
    rng = np.random.default_rng(seed)
    chips_per_bit = 2 if code == "manchester" else 1
    bits = PREAMBLE + payload
    pattern = np.tile(bits_to_chips(bits, code), frames)  # +1 / -1 per chip

    chip_tsc = int(symbol_us * 1e-6 * TSC_HZ) // chips_per_bit
    t0 = int(5.0 * TSC_HZ) + int(lead_ms * 1e-3 * TSC_HZ)
    t_end = t0 + len(pattern) * chip_tsc
    trace_start = t0 - int(lead_ms * 1e-3 * TSC_HZ)
    trace_stop = t_end + int(0.3 * TSC_HZ)

    # Sample boundaries: a jittered ~1 ms grid, as the RAPL update looks.
    period = RAPL_MS * 1e-3 * TSC_HZ
    edges = [float(trace_start)]
    while edges[-1] < trace_stop:
        edges.append(edges[-1] + period * (1.0 + rng.normal(0, 0.02)))
    edges = np.array(edges)

    # Energy per sample = integral of the piecewise-constant chip power.
    fine = 64
    rows = []
    for i in range(len(edges) - 1):
        a, b = edges[i], edges[i + 1]
        ts = np.linspace(a, b, fine, endpoint=False)
        idx = np.floor((ts - t0) / chip_tsc).astype(int)
        on = np.zeros(fine)
        live = (idx >= 0) & (idx < len(pattern))
        on[live] = (pattern[idx[live]] > 0).astype(float)
        p = base_w + delta_w * on
        if noise_w:
            p = p + rng.normal(0, noise_w, fine)
        joules = float(np.mean(p) * (b - a) / TSC_HZ)
        ticks = max(int(round(joules / ENERGY_UNIT)), 0)
        rows.append((int(b), ticks, int(b - a)))

    # An edge seen late is one RAPL sample spanning two update periods, so
    # make them the way the hardware would: merge adjacent samples, summing
    # both the energy and the interval. Nothing else about the trace changes,
    # which is what lets the decoder still find the frame through them.
    if late_frac:
        step = max(int(round(1.0 / late_frac)), 2)
        merged, i, n = [], 0, 0
        while i < len(rows):
            if i + 1 < len(rows) and n % step == 0:
                (_, k1, d1), (t2, k2, d2) = rows[i], rows[i + 1]
                merged.append((t2, k1 + k2, d1 + d2))
                i += 2
            else:
                merged.append(rows[i])
                i += 1
            n += 1
        rows = merged

    entry = {
        "tag": "synth", "label": "synth", "repeat": 0, "csv": "synth.rx.csv",
        "tx": {
            "victim": "synthetic", "on_selector": 1 if delta_w else 0,
            "off_selector": 0, "code": code, "chips_per_bit": chips_per_bit,
            "symbol_us": symbol_us, "symbol_tsc": chip_tsc * chips_per_bit,
            "chip_tsc": chip_tsc, "tsc_hz": TSC_HZ, "tsc_start": t0,
            "tsc_end": t_end, "frames": frames,
            "bits_per_frame": len(bits), "preamble_bits": PREAMBLE,
            "payload_bits": payload, "message": None, "late_chips": 0,
        },
        "rx": {
            "energy_unit_j": ENERGY_UNIT, "tsc_hz": TSC_HZ,
            "rapl_period_ms": RAPL_MS,
            "rapl_period_tsc": int(period), "samples_written": len(rows),
            "zero_tick_samples": sum(1 for r in rows if r[1] == 0),
            # The synthetic sampler keeps to its grid, so no edge is ever late.
            "rapl_overshoots": overshoots,
        },
    }
    return entry, rows


def synth_freq_run(payload, symbol_us, delta_khz, base_khz=3600.0,
                   noise_khz=0.0, frames=8, interval_us=200.0, seed=5):
    """A tier-2 recording: a frequency level polled on a fixed grid.

    Unlike the RAPL trace this is sampled, not integrated -- each row is the
    value at an instant, held until the next poll. delta_khz is negative for
    the real channel, since the heavier operand throttles the part.
    """
    rng = np.random.default_rng(seed)
    bits = PREAMBLE + payload
    pattern = np.tile(bits_to_chips(bits, "manchester"), frames)

    chip_tsc = int(symbol_us * 1e-6 * TSC_HZ) // 2
    t0 = int(5.0 * TSC_HZ) + int(0.3 * TSC_HZ)
    t_end = t0 + len(pattern) * chip_tsc
    step = interval_us * 1e-6 * TSC_HZ

    ts = np.arange(t0 - 0.3 * TSC_HZ, t_end + 0.3 * TSC_HZ, step)
    idx = np.floor((ts - t0) / chip_tsc).astype(int)
    on = np.zeros(len(ts))
    live = (idx >= 0) & (idx < len(pattern))
    on[live] = (pattern[idx[live]] > 0).astype(float)
    khz = base_khz + delta_khz * on
    if noise_khz:
        khz = khz + rng.normal(0, noise_khz, len(ts))
    rows = [(int(t), int(round(v)), 0) for t, v in zip(ts, khz)]

    entry = {
        "tag": "synth_freq", "label": "synth_freq", "repeat": 0,
        "csv": "synth_freq.rx.csv",
        "tx": {
            "victim": "synthetic", "on_selector": 1, "off_selector": 0,
            "code": "manchester", "chips_per_bit": 2, "symbol_us": symbol_us,
            "symbol_tsc": chip_tsc * 2, "chip_tsc": chip_tsc, "tsc_hz": TSC_HZ,
            "tsc_start": t0, "tsc_end": t_end, "frames": frames,
            "bits_per_frame": len(bits), "preamble_bits": PREAMBLE,
            "payload_bits": payload, "message": None, "late_chips": 0,
        },
        "rx": {
            "receiver": "freq", "tsc_hz": TSC_HZ,
            "interval_us": interval_us, "interval_tsc": int(step),
            "samples_written": len(rows), "late_polls": 0,
        },
    }
    return entry, rows


def write_and_decode(entry, rows, tmp, **kw):
    csv = Path(tmp) / entry["csv"]
    with open(csv, "w") as f:
        f.write("tsc,ticks,dtsc,daperf,dmperf\n")
        for tsc, ticks, dtsc in rows:
            f.write(f"{tsc},{ticks},{dtsc},0,0\n")
    return decode_run(entry, csv, **kw)


def main():
    rng = np.random.default_rng(7)
    payload = "".join(rng.integers(0, 2, 32).astype(str))

    with tempfile.TemporaryDirectory() as tmp:
        print("clean channel, 8 ms symbols (8 RAPL samples per chip)")
        e, r = synth_run(payload, symbol_us=8000, delta_w=2.0)
        d = write_and_decode(e, r, tmp)
        check("payload decodes exactly", d["ber"] == 0.0, f"BER {d['ber']:.4f}")
        check("preamble decodes exactly", d["preamble_ber"] == 0.0)
        check("sync recovered to within half a chip",
              abs(d["sync_error_chips"]) < 0.5, f"{d['sync_error_chips']:+.3f} chips")
        check("sync peak beats its sidelobes",
              d["sync_peak"] > d["sync_runner_up"] + 0.2,
              f"{d['sync_peak']:.3f} vs {d['sync_runner_up']:.3f}")

        print("\nnoisy channel, 4 ms symbols, 0.5 W effect in 0.4 W noise")
        e, r = synth_run(payload, symbol_us=4000, delta_w=0.5, noise_w=0.4, frames=8)
        d = write_and_decode(e, r, tmp)
        check("BER stays low", d["ber"] < 0.05, f"BER {d['ber']:.4f}")
        check("majority vote over frames is at least as good",
              d["voted_ber"] <= d["ber"], f"vote {d['voted_ber']:.4f}")

        print("\nper-chip separation, in a regime that actually makes errors")
        # Noise chosen to land d' near 2-3, where Q(d'/sqrt2) is a percent or
        # two: high enough that the comparison is not 0 against 0, low enough
        # that sync still holds.
        e, r = synth_run(payload, symbol_us=8000, delta_w=1.0, noise_w=6.0,
                         frames=8, seed=3)
        d = write_and_decode(e, r, tmp)
        # A Manchester decision differences two chips, so BER should track
        # Q(d'/sqrt(2)). This is the relation the overshoot gate leans on: a
        # run whose noise rose is a run whose d' fell, and the errors follow.
        q = 0.5 * math.erfc(d["d_prime"] / 2.0)
        check("the regime is one where errors happen",
              1.0 < d["d_prime"] < 5.0, f"d' {d['d_prime']:.2f}, Q {q:.4f}")
        check("BER is within a factor of 3 of Q(d'/sqrt2)",
              d["ber"] <= max(3 * q, 0.01), f"BER {d['ber']:.4f} vs Q {q:.4f}")
        # With white chip noise the paired statistic is the marginal one over
        # sqrt(2), which is why Q(d'/sqrt2) fitted tier 1 at all.
        check("paired d-prime is marginal/sqrt2 under white noise",
              abs(d["d_prime_paired"] - d["d_prime"] / math.sqrt(2)) < 0.25,
              f"{d['d_prime_paired']:.2f} vs {d['d_prime']/math.sqrt(2):.2f}")
        check("delta and noise are reported separately",
              d["delta_w"] > 0 and d["noise_sd_w"] > 0,
              f"dW {d['delta_w']:.3f}, sd {d['noise_sd_w']:.3f}")

        # Same signal, more noise: d' must fall and the errors must follow it.
        e2, r2 = synth_run(payload, symbol_us=8000, delta_w=1.0, noise_w=12.0,
                           frames=8, seed=3)
        d2 = write_and_decode(e2, r2, tmp)
        check("doubling the noise lowers d-prime",
              d2["d_prime"] < d["d_prime"],
              f"{d['d_prime']:.2f} -> {d2['d_prime']:.2f}")
        check("and raises the error rate",
              d2["ber"] > d["ber"], f"{d['ber']:.4f} -> {d2['ber']:.4f}")

        print("\ndrifting baseline: the case the marginal d-prime misreads")
        # A slow wander added to the level, larger than the signal. Manchester
        # differences two adjacent chips and cancels it, so the channel still
        # decodes -- but the marginal per-chip spread is dominated by the
        # drift and reports a dead channel. This is what tier 2 actually
        # looks like, and it is why the paired statistic is the primary one.
        e, r = synth_freq_run(payload, symbol_us=8000, delta_khz=-60.0,
                              base_khz=3600.0, noise_khz=15.0, frames=8, seed=11)
        rows = []
        for i, (tsc, khz, dt) in enumerate(r):
            drift = 900.0 * math.sin(2 * math.pi * i / (len(r) / 3.0))
            rows.append((tsc, int(khz + drift), dt))
        d = write_and_decode(e, rows, tmp)
        check("still decodes through a drift bigger than the signal",
              d["ber"] < 0.10, f"BER {d['ber']:.4f}")
        check("marginal d-prime is fooled by the drift", d["d_prime"] < 0.5,
              f"d-marg {d['d_prime']:.2f}")
        check("paired d-prime is not", d["d_prime_paired"] > 2.0,
              f"d-pair {d['d_prime_paired']:.2f}")
        check("and it is the paired one that predicts the BER",
              abs(d["ber"] - d["ber_predicted"]) < 0.10,
              f"BER {d['ber']:.4f} vs predicted {d['ber_predicted']:.4f}")

        print("\novershoot bookkeeping, measured rather than taken on trust")
        # The gate used to read the sampler's own count. That count is computed
        # against a running estimate of the update period, and the estimate can
        # be wrong about itself -- it latched at a quarter of the truth and
        # called every edge late (util/sampler.c). So the trace is the source
        # of truth now, and these two cases are the ones that told them apart.
        e, r = synth_run(payload, symbol_us=8000, delta_w=2.0, late_frac=0.04)
        e["rx"]["rapl_overshoots"] = 0          # a sampler that noticed nothing
        d = write_and_decode(e, r, tmp)
        check("late edges are found in the trace the sampler called clean",
              abs(d["overshoot_fraction"] - 0.04) < 0.015,
              f"measured {d['overshoot_fraction']:.3f}, sampler said 0.000")
        check("and the sampler's own claim is kept beside it",
              d["overshoot_reported"] == 0.0,
              f"{d['overshoot_reported']:.3f}")

        e, r = synth_run(payload, symbol_us=8000, delta_w=2.0)
        e["rx"]["rapl_period_ms"] = RAPL_MS / 4
        e["rx"]["rapl_overshoots"] = e["rx"]["samples_written"]
        d = write_and_decode(e, r, tmp)
        check("a latched period estimate does not become the gate's opinion",
              abs(d["period_robust_ms"] - RAPL_MS) < 0.05 * RAPL_MS
              and d["overshoot_fraction"] < 0.01,
              f"trace says {d['period_robust_ms']:.3f} ms against a claimed "
              f"{RAPL_MS / 4:.3f}; overshoot {d['overshoot_fraction']:.3f} "
              f"against a claimed 1.000")

        print("\nA/A control: nothing transmitted")
        e, r = synth_run(payload, symbol_us=8000, delta_w=0.0, noise_w=0.3)
        d = write_and_decode(e, r, tmp)
        check("A/A decodes at chance", 0.25 < d["ber"] < 0.75, f"BER {d['ber']:.4f}")
        check("A/A sync correlation is weak", abs(d["sync_corr"]) < 0.5,
              f"corr {d['sync_corr']:.3f}")

        print("\nthe differential sync correlator, which is not the default")
        # Kept as an option because it was measured, and the measurement is
        # the point: correlating against a Manchester pattern already is a
        # within-symbol difference, since the pattern is pair-antisymmetric,
        # so differencing first cannot add drift rejection to the numerator.
        # What it changes is the normalisation, and that lifts every peak --
        # including the ones that are pure noise. On the real sweeps it buys
        # one tier-1 acquisition at 4 ms and costs the tier-2 headline at
        # 2 bit/s; see sync_score for the numbers.
        ec, rc = synth_run(payload, symbol_us=8000, delta_w=2.0)
        c_raw = write_and_decode(ec, rc, tmp, sync_mode="raw")
        c_dif = write_and_decode(ec, rc, tmp, sync_mode="diff")
        check("on a clean channel both modes find the same frame",
              abs(c_raw["sync_error_chips"] - c_dif["sync_error_chips"]) < 0.5,
              f"{c_raw['sync_error_chips']:+.3f} vs "
              f"{c_dif['sync_error_chips']:+.3f} chips")
        ea, ra = synth_run(payload, symbol_us=8000, delta_w=0.0, noise_w=0.3)
        a_raw = write_and_decode(ea, ra, tmp, sync_mode="raw")
        a_dif = write_and_decode(ea, ra, tmp, sync_mode="diff")
        check("but it lifts the peak on a trace carrying nothing",
              abs(a_dif["sync_peak"]) > abs(a_raw["sync_peak"]),
              f"A/A |pk| {abs(a_raw['sync_peak']):.3f} -> "
              f"{abs(a_dif['sync_peak']):.3f}")

        print("\ntier 2: a frequency trace, inverted and polled on a grid")
        # The tier-2 channel runs the other way round -- the heavier operand
        # throttles the part, so the ON state reads as a *lower* frequency --
        # and the receiver polls a level rather than integrating a counter.
        # Nothing tells the decoder either fact; it resolves the sign from the
        # preamble it already knows.
        e, r = synth_freq_run(payload, symbol_us=8000, delta_khz=-250.0,
                              base_khz=3600.0, noise_khz=40.0, frames=8)
        d = write_and_decode(e, r, tmp)
        check("inverted channel still decodes", d["ber"] < 0.05, f"BER {d['ber']:.4f}")
        check("polarity is recovered, not supplied", d["polarity"] == -1.0,
              f"polarity {d['polarity']:+.0f}")
        # Magnitude, not just sign: an integration that accumulated against
        # TSC cycles instead of seconds passes a sign check while reporting
        # levels scaled by tsc_hz.
        check("the separation is signed and in the right units",
              -300 < d["delta_w"] < -200, f"{d['delta_w']:.1f} kHz (want ~-250)")
        check("d-prime stays positive on an inverted channel",
              d["d_prime"] > 0, f"d' {d['d_prime']:.2f}")
        check("the trace is labelled as a frequency receiver",
              d["receiver"] == "freq" and d["unit"] == "kHz")

        print("\nNRZ line code")
        e, r = synth_run(payload, symbol_us=8000, delta_w=2.0, code="nrz")
        d = write_and_decode(e, r, tmp)
        check("NRZ payload decodes exactly", d["ber"] == 0.0, f"BER {d['ber']:.4f}")

        print("\nmessage round trip")
        msg = "OK!"
        bits = "".join(f"{ord(ch):08b}" for ch in msg)
        e, r = synth_run(bits, symbol_us=8000, delta_w=2.0)
        e["tx"]["message"] = msg
        d = write_and_decode(e, r, tmp)
        check("ASCII round-trips", d["decoded_message"] == msg,
              repr(d["decoded_message"]))

        print("\nwindowed integration")
        t = Trace(tsc=np.array([100.0, 200.0, 300.0]),
                  ticks=np.array([1.0, 1.0, 1.0]),
                  dtsc=np.array([100.0, 100.0, 100.0]),
                  energy_unit_j=1.0, tsc_hz=1.0)
        check("energy over a whole trace is the sum of its samples",
              abs(t.energy(0, 300) - 3.0) < 1e-9)
        check("a window inside one sample gets its fraction",
              abs(t.energy(100, 150) - 0.5) < 1e-9)
        check("a window straddling two samples splits correctly",
              abs(t.energy(150, 250) - 1.0) < 1e-9)

    print()
    if _fails:
        print(f"FAILED: {', '.join(_fails)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
