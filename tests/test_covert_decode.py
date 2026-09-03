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
              overshoots=0):
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


def write_and_decode(entry, rows, tmp):
    csv = Path(tmp) / entry["csv"]
    with open(csv, "w") as f:
        f.write("tsc,ticks,dtsc,daperf,dmperf\n")
        for tsc, ticks, dtsc in rows:
            f.write(f"{tsc},{ticks},{dtsc},0,0\n")
    return decode_run(entry, csv)


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

        print("\novershoot bookkeeping")
        e, r = synth_run(payload, symbol_us=8000, delta_w=2.0)
        e["rx"]["rapl_overshoots"] = int(0.04 * e["rx"]["samples_written"])
        d = write_and_decode(e, r, tmp)
        check("overshoot fraction is carried through for the gate",
              abs(d["overshoot_fraction"] - 0.04) < 0.005,
              f"{d['overshoot_fraction']:.3f}")

        print("\nA/A control: nothing transmitted")
        e, r = synth_run(payload, symbol_us=8000, delta_w=0.0, noise_w=0.3)
        d = write_and_decode(e, r, tmp)
        check("A/A decodes at chance", 0.25 < d["ber"] < 0.75, f"BER {d['ber']:.4f}")
        check("A/A sync correlation is weak", abs(d["sync_corr"]) < 0.5,
              f"corr {d['sync_corr']:.3f}")

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
