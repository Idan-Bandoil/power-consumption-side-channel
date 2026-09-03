"""Decode a covert-channel recording and score it against the transmitted bits.

    ./venv/bin/python3 -m analysis.covert results/<run_id>

The receiver process (src/covert/rx_rapl.c) records a power trace and nothing
else. Everything here works from that trace plus what a receiver legitimately
knows about the protocol -- symbol period, line code, preamble, frame length.
It does *not* use the transmitter's tsc_start to find the frame: sync comes
from sliding the preamble along the trace. The recorded tsc_start is used only
afterwards, to report how far the recovered sync landed from the truth.

Both processes read the same invariant TSC, so the trace and the transmitter's
schedule share a timebase and no clock recovery is needed. That is a
simplification worth stating: a receiver with an independent clock would have
to track symbol timing as well as phase.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

# Candidate sync offsets per chip. The peak is a chip wide, so eight puts the
# quantisation error well under the noise.
SYNC_OVERSAMPLE = 8
# Half-width of the per-frame refinement search, in chips.
REFINE_CHIPS = 0.5
# An A/A run (on == off) must not decode better than this.
AA_BER_FLOOR = 0.40


class Trace:
    """A power trace as a cumulative-energy function of TSC.

    Each row covers (tsc - dtsc, tsc] and carries the energy actually
    accumulated over it, so treating power as constant within a row and
    integrating is exact under the sampler's own assumption. Cumulative
    energy at the row boundaries plus linear interpolation inside them then
    gives the energy in an arbitrary window in one np.interp -- which is what
    the decoder needs, because symbol boundaries never line up with RAPL
    update instants.
    """

    def __init__(self, tsc, ticks, dtsc, energy_unit_j, tsc_hz):
        self.tsc_hz = tsc_hz
        joules = ticks * energy_unit_j
        # Breakpoints: the start of the first interval, then every row end.
        self.t = np.empty(len(tsc) + 1, dtype=np.float64)
        self.t[0] = tsc[0] - dtsc[0]
        self.t[1:] = tsc
        self.e = np.empty(len(tsc) + 1, dtype=np.float64)
        self.e[0] = 0.0
        np.cumsum(joules, out=self.e[1:])

    @property
    def start(self):
        return self.t[0]

    @property
    def end(self):
        return self.t[-1]

    def energy(self, a, b):
        """Joules in [a, b), for scalars or arrays."""
        return np.interp(b, self.t, self.e) - np.interp(a, self.t, self.e)

    def window_power(self, edges):
        """Mean watts in each window between consecutive `edges` (TSC).

        `edges` may be an (..., n+1) array; the result is (..., n).
        """
        edges = np.asarray(edges, dtype=np.float64)
        cum = np.interp(edges, self.t, self.e)
        joules = np.diff(cum, axis=-1)
        secs = np.diff(edges, axis=-1) / self.tsc_hz
        return joules / secs


def bits_to_chips(bits, code):
    """Chip pattern for a bit string, as +1/-1 (see tx.c for the convention)."""
    out = []
    for b in bits:
        if code == "manchester":
            out += [1, -1] if b == "1" else [-1, 1]
        else:
            out.append(1 if b == "1" else -1)
    return np.array(out, dtype=np.float64)


def sync_score(trace, offsets, chip_tsc, pattern, frame_tsc, frames):
    """Normalised preamble correlation, summed over every frame.

    Frame length is part of the protocol, so a receiver may add the
    correlation from all F preambles at a single candidate offset. That turns
    F weak peaks into one strong one and is what makes sync work at symbol
    periods where a single preamble would not be enough.
    """
    n = len(pattern)
    rel = np.arange(n + 1, dtype=np.float64) * chip_tsc
    c = pattern - pattern.mean()
    cnorm = np.linalg.norm(c)

    total = np.zeros(len(offsets), dtype=np.float64)
    for f in range(frames):
        edges = offsets[:, None] + f * frame_tsc + rel[None, :]
        x = trace.window_power(edges)
        x = x - x.mean(axis=-1, keepdims=True)
        norm = np.linalg.norm(x, axis=-1)
        with np.errstate(invalid="ignore", divide="ignore"):
            total += np.where(norm > 0, (x @ c) / (norm * cnorm), 0.0)
    return total / frames


def demodulate(chips, code, nrz_window=16):
    """Chip powers -> bits."""
    if code == "manchester":
        pairs = chips.reshape(-1, 2)
        return (pairs[:, 0] > pairs[:, 1]).astype(np.int8)
    # NRZ has no in-symbol reference, so it needs a running baseline. A
    # centred moving average is the cheapest one that tracks drift slower
    # than the window without tracking the symbols themselves.
    k = min(nrz_window, len(chips))
    pad = np.pad(chips, (k // 2, k - k // 2 - 1), mode="edge")
    baseline = np.convolve(pad, np.ones(k) / k, mode="valid")
    return (chips > baseline).astype(np.int8)


def decode_run(entry, csv_path, sync_span=None):
    tx, rx = entry["tx"], entry["rx"]
    raw = np.loadtxt(csv_path, delimiter=",", skiprows=1, dtype=np.float64, ndmin=2)
    if raw.size == 0:
        raise ValueError(f"{csv_path} contains no samples")

    trace = Trace(tsc=raw[:, 0], ticks=raw[:, 1], dtsc=raw[:, 2],
                  energy_unit_j=rx["energy_unit_j"], tsc_hz=rx["tsc_hz"])

    code = tx["code"]
    chip_tsc = float(tx["chip_tsc"])
    chips_per_bit = int(tx["chips_per_bit"])
    preamble, payload = tx["preamble_bits"], tx["payload_bits"]
    frames, bits_per_frame = int(tx["frames"]), int(tx["bits_per_frame"])
    frame_chips = bits_per_frame * chips_per_bit
    frame_tsc = frame_chips * chip_tsc

    # --- sync: slide the preamble along the trace -------------------------
    pattern = bits_to_chips(preamble, code)
    span = frames * frame_tsc
    lo, hi = trace.start, trace.end - span
    if hi <= lo:
        raise ValueError(f"{csv_path}: trace is shorter than one transmission")
    if sync_span is not None:
        lo = max(lo, tx["tsc_start"] - sync_span)
        hi = min(hi, tx["tsc_start"] + sync_span)

    step = chip_tsc / SYNC_OVERSAMPLE
    offsets = np.arange(lo, hi, step)
    scores = sync_score(trace, offsets, chip_tsc, pattern, frame_tsc, frames)
    best = int(np.argmax(scores))
    t_sync = offsets[best]

    # --- per-frame refinement --------------------------------------------
    # Each frame carries its own preamble, so phase is re-acquired per frame
    # rather than extrapolated from the first one.
    refine = np.arange(-REFINE_CHIPS, REFINE_CHIPS + 1e-9,
                       1.0 / SYNC_OVERSAMPLE) * chip_tsc
    frame_starts, frame_corr = [], []
    for f in range(frames):
        cand = t_sync + f * frame_tsc + refine
        cand = cand[(cand >= trace.start) & (cand + frame_tsc <= trace.end)]
        if len(cand) == 0:
            frame_starts.append(t_sync + f * frame_tsc)
            frame_corr.append(float("nan"))
            continue
        sc = sync_score(trace, cand, chip_tsc, pattern, frame_tsc, 1)
        j = int(np.argmax(sc))
        frame_starts.append(float(cand[j]))
        frame_corr.append(float(sc[j]))

    # --- demodulate -------------------------------------------------------
    rel = np.arange(frame_chips + 1, dtype=np.float64) * chip_tsc
    edges = np.array(frame_starts)[:, None] + rel[None, :]
    chip_power = trace.window_power(edges)

    truth_pre = np.array([int(b) for b in preamble], dtype=np.int8)
    truth_pay = np.array([int(b) for b in payload], dtype=np.int8)
    n_pre = len(truth_pre)

    pre_err = pay_err = 0
    decoded_frames = []
    for f in range(frames):
        bits = demodulate(chip_power[f], code)
        pre_err += int(np.sum(bits[:n_pre] != truth_pre))
        pay = bits[n_pre:]
        pay_err += int(np.sum(pay != truth_pay))
        decoded_frames.append(pay)

    n_pay = len(truth_pay)
    ber = pay_err / (n_pay * frames)

    # Majority vote across the repeated frames: the cheapest possible ECC,
    # and the honest way to show what repetition buys against the raw BER.
    stacked = np.stack(decoded_frames)
    voted = (stacked.mean(axis=0) > 0.5).astype(np.int8)
    voted_err = int(np.sum(voted != truth_pay))

    symbol_s = float(tx["symbol_us"]) / 1e6
    raw_bps = 1.0 / symbol_s

    return {
        "label": entry.get("label", entry["tag"]),
        "tag": entry["tag"],
        "repeat": entry.get("repeat", 0),
        "victim": tx["victim"],
        "code": code,
        "symbol_us": float(tx["symbol_us"]),
        "raw_bps": raw_bps,
        "aa_control": tx["on_selector"] == tx["off_selector"],
        "late_chips": int(tx["late_chips"]),
        "frames": frames,
        "payload_bits": n_pay,
        "preamble_ber": pre_err / (n_pre * frames),
        "ber": ber,
        "voted_errors": voted_err,
        "voted_ber": voted_err / n_pay,
        "capacity_bps": raw_bps * bsc_capacity(ber),
        "sync_corr": float(np.nanmean(frame_corr)),
        "sync_peak": float(scores[best]),
        "sync_runner_up": runner_up(scores, best, SYNC_OVERSAMPLE),
        # Truth, used for reporting only -- the sync above never saw it.
        "sync_error_chips": (t_sync - tx["tsc_start"]) / chip_tsc,
        "rapl_period_ms": rx["rapl_period_ms"],
        "samples_per_chip": chip_tsc / (rx["rapl_period_tsc"] or float("nan")),
        "zero_tick_fraction": rx["zero_tick_samples"] / max(rx["samples_written"], 1),
        "message": tx.get("message"),
        "decoded_message": bits_to_text(voted) if tx.get("message") else None,
    }


def bsc_capacity(p):
    """Capacity of a binary symmetric channel with error probability p."""
    p = min(max(p, 0.0), 1.0)
    if p in (0.0, 1.0):
        return 1.0
    return 1.0 + p * math.log2(p) + (1 - p) * math.log2(1 - p)


def runner_up(scores, best, exclude):
    """Best correlation outside the winning peak, as a sidelobe check."""
    mask = np.ones(len(scores), dtype=bool)
    mask[max(0, best - exclude):best + exclude + 1] = False
    return float(np.max(scores[mask])) if mask.any() else float("nan")


def bits_to_text(bits):
    out = []
    for i in range(0, len(bits) - 7, 8):
        out.append(chr(int("".join(str(int(b)) for b in bits[i:i + 8]), 2)))
    return "".join(out)


def hr(title=""):
    return f"\n{'=' * 78}\n{title}\n{'=' * 78}" if title else "-" * 78


def aggregate(rows):
    """Group repeats of the same label; between-run spread is the error bar.

    Same rule as analysis.aggregate: a single run's numbers are optimistic,
    so nothing is quoted without the spread across repeats.
    """
    by_label = {}
    for r in rows:
        by_label.setdefault(r["label"], []).append(r)
    return by_label


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", nargs="+", help="results directories to decode")
    ap.add_argument("--sync-span-symbols", type=float, default=None,
                    help="restrict the sync search to +/- N symbol periods "
                         "around the true start (diagnostic only -- the "
                         "default searches the whole trace)")
    ap.add_argument("--json", metavar="PATH", help="also write the rows as JSON")
    args = ap.parse_args()

    rows, failures = [], []
    for d in args.results:
        d = Path(d)
        manifest = json.loads((d / "manifest.json").read_text())
        for entry in manifest["runs"]:
            if "tx" not in entry:
                continue
            span = None
            if args.sync_span_symbols is not None:
                span = args.sync_span_symbols * float(entry["tx"]["symbol_tsc"])
            rows.append(decode_run(entry, d / entry["csv"], sync_span=span))

    if not rows:
        raise SystemExit("no covert runs found (is this a driver experiment?)")

    print(hr("per-run decode"))
    print(f"  {'run':>22} {'sym us':>7} {'bit/s':>7} {'BER':>7} {'pre':>6} "
          f"{'vote':>6} {'corr':>6} {'2nd':>6} {'sync':>7} {'late':>5}")
    for r in sorted(rows, key=lambda r: (r["label"], r["repeat"])):
        print(f"  {r['tag']:>22} {r['symbol_us']:>7.0f} {r['raw_bps']:>7.1f} "
              f"{r['ber']:>7.4f} {r['preamble_ber']:>6.3f} "
              f"{r['voted_ber']:>6.3f} {r['sync_corr']:>6.3f} "
              f"{r['sync_runner_up']:>6.3f} {r['sync_error_chips']:>+7.2f} "
              f"{r['late_chips']:>5}")
    print("\n  BER over payload bits; pre = preamble BER (a sync check);")
    print("  vote = BER after majority vote across frames; corr = mean per-frame")
    print("  preamble correlation, 2nd = best correlation outside that peak;")
    print("  sync = recovered start minus true start, in chips.")

    print(hr("by condition (between-repeat spread is the error bar)"))
    print(f"  {'label':>18} {'n':>3} {'bit/s':>7} {'BER':>8} {'sd':>8} "
          f"{'capacity b/s':>13} {'vote BER':>9}")
    for label, group in sorted(aggregate(rows).items()):
        bers = np.array([g["ber"] for g in group])
        caps = np.array([g["capacity_bps"] for g in group])
        votes = np.array([g["voted_ber"] for g in group])
        sd = bers.std(ddof=1) if len(bers) > 1 else float("nan")
        print(f"  {label:>18} {len(group):>3} {group[0]['raw_bps']:>7.1f} "
              f"{bers.mean():>8.4f} {sd:>8.4f} {caps.mean():>13.1f} "
              f"{votes.mean():>9.4f}")

    # --- gates ------------------------------------------------------------
    print(hr("gates"))
    for r in rows:
        if r["late_chips"]:
            failures.append(f"{r['tag']}: {r['late_chips']} late chips -- the "
                            f"transmitter could not hold its schedule, so this "
                            f"run's BER is not a property of the channel")
        if r["zero_tick_fraction"] > 0.01:
            failures.append(f"{r['tag']}: {r['zero_tick_fraction']:.1%} zero-tick "
                            f"samples -- the sampler is aliasing")
        # An A/A transmission carries nothing, so the decoder must fail on it.
        # This is the same negative control every A/B claim in the project
        # ships with, in the form the channel takes.
        if r["aa_control"] and r["ber"] < AA_BER_FLOOR:
            failures.append(f"{r['tag']}: A/A control decoded at BER "
                            f"{r['ber']:.3f} -- the decoder is finding "
                            f"structure in a transmission that has none")

    for r in rows:
        if r["message"] is not None:
            ok = "OK" if r["decoded_message"] == r["message"] else "MISMATCH"
            print(f"  {r['tag']}: message {r['decoded_message']!r} [{ok}]")

    if failures:
        for f in failures:
            print(f"  FAIL  {f}")
    else:
        print("  all gates pass")

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2))

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
