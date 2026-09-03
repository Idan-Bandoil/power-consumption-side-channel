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
# Share of RAPL edges observed more than 1.5 periods late. Phase 0 established
# that overshoots are harmless to a *mean* difference -- within a run they are
# balanced across conditions, so they cancel. A per-symbol decision has no such
# protection: one overshoot is a single RAPL sample spanning two chips, which
# smears them together and costs those bits outright. The sampler is bimodal,
# sitting at either ~0.1% or ~4% for a whole run, so this threshold separates
# the two regimes rather than cutting through a distribution.
MAX_OVERSHOOT_FRACTION = 0.01


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

    @classmethod
    def from_levels(cls, tsc, value, tsc_hz):
        """A trace of an instantaneous *level* rather than an accumulator.

        The tier-2 receiver polls scaling_cur_freq, which reports a level: it
        has no counter edge to lock onto and no energy increment to integrate.
        Holding each reading until the next one and integrating gives the same
        cumulative function the energy path builds, so every window query
        downstream is identical. The last sample is held for one median
        interval, since nothing says when it stopped being true.
        """
        self = cls.__new__(cls)
        self.tsc_hz = tsc_hz
        tsc = np.asarray(tsc, dtype=np.float64)
        value = np.asarray(value, dtype=np.float64)
        step = float(np.median(np.diff(tsc))) if len(tsc) > 1 else 1.0
        self.t = np.empty(len(tsc) + 1, dtype=np.float64)
        self.t[:-1] = tsc
        self.t[-1] = tsc[-1] + step
        self.e = np.empty(len(tsc) + 1, dtype=np.float64)
        self.e[0] = 0.0
        # Integrate against *seconds*, matching the energy path: window_power
        # divides by seconds, so accumulating against raw TSC cycles here
        # would scale every reported level by tsc_hz.
        np.cumsum(value * np.diff(self.t) / tsc_hz, out=self.e[1:])
        return self

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


def demodulate(chips, code, nrz_window=16, polarity=1.0):
    """Chip levels -> bits.

    `polarity` is +1 when the ON operand reads high and -1 when it reads low.
    Tier 2 is the inverted case: the heavier operand draws more power, the
    part throttles, and the reported frequency *falls*. The receiver is not
    told which it is looking at -- see resolve_polarity.
    """
    chips = chips * polarity
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


def decode_run(entry, csv_path, sync_span=None, freq_column=0):
    tx, rx = entry["tx"], entry["rx"]
    raw = np.loadtxt(csv_path, delimiter=",", skiprows=1, dtype=np.float64, ndmin=2)
    if raw.size == 0:
        raise ValueError(f"{csv_path} contains no samples")

    # Tier 1 records an energy accumulator, tier 2 a frequency level; the
    # cumulative-integral form makes every window query below identical.
    if rx.get("receiver", "rapl") == "freq":
        # A tier-2 receiver may watch several CPUs, since an attacker need not
        # know which cores the victim occupies. Column 0 is the first --watch
        # entry; which one decodes best is an experimental question.
        watched = rx.get("watch", [None])
        if freq_column >= len(watched):
            raise ValueError(f"{csv_path}: --freq-column {freq_column} but only "
                             f"{len(watched)} CPU(s) watched")
        trace = Trace.from_levels(raw[:, 0], raw[:, 1 + freq_column], rx["tsc_hz"])
        unit = "kHz"
    else:
        trace = Trace(tsc=raw[:, 0], ticks=raw[:, 1], dtsc=raw[:, 2],
                      energy_unit_j=rx["energy_unit_j"], tsc_hz=rx["tsc_hz"])
        unit = "W"

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
    # Peak on |correlation|, then read the polarity off its sign. A receiver
    # knows the preamble, so it can resolve which way round the channel is
    # without being told -- which matters because tier 2 is inverted relative
    # to tier 1. This is the usual resolution of a BPSK phase ambiguity.
    best = int(np.argmax(np.abs(scores)))
    t_sync = offsets[best]
    polarity = 1.0 if scores[best] >= 0 else -1.0

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
        sc = sync_score(trace, cand, chip_tsc, pattern, frame_tsc, 1) * polarity
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
        bits = demodulate(chip_power[f], code, polarity=polarity)
        pre_err += int(np.sum(bits[:n_pre] != truth_pre))
        pay = bits[n_pre:]
        pay_err += int(np.sum(pay != truth_pay))
        decoded_frames.append(pay)

    n_pay = len(truth_pay)
    ber = pay_err / (n_pay * frames)

    # Per-chip separation, which is what actually sets the error rate. A
    # Manchester decision compares two chips, so with per-chip noise sd the
    # difference carries sd*sqrt(2) and BER should track Q(d_prime/sqrt(2)).
    #
    # Measured on the *true* chip grid rather than the recovered one. Sync
    # failure would otherwise misalign every window and collapse the
    # separation to zero, reporting a dead channel where there is a live one
    # the receiver merely failed to find -- which is precisely the
    # distinction these numbers exist to draw. Like sync_error_chips this
    # reads ground truth, so it is a diagnostic and not something a real
    # receiver could compute.
    truth_chips = np.tile(bits_to_chips(preamble + payload, code), frames)
    true_edges = tx["tsc_start"] + np.arange(len(truth_chips) + 1) * chip_tsc
    if true_edges[0] < trace.start or true_edges[-1] > trace.end:
        raise ValueError(f"{csv_path}: trace does not cover the transmission")
    true_power = trace.window_power(true_edges)
    on, off = true_power[truth_chips > 0], true_power[truth_chips < 0]
    noise_sd = float(np.sqrt(0.5 * (on.var() + off.var())))
    # Signed: negative is the tier-2 direction, where the ON operand reads as
    # a *lower* frequency. d' is taken on the magnitude, since which way round
    # the channel sits does not change how separable its two states are.
    delta_w = float(on.mean() - off.mean())
    d_prime = abs(delta_w) / noise_sd if noise_sd > 0 else float("nan")

    # The statistic the decision is actually made on. A Manchester bit is
    # decided by differencing two adjacent chips, so what matters is the
    # spread of that difference, not the marginal spread of a chip. When chip
    # noise is white the two agree up to sqrt(2) -- which is why Q(d'/sqrt2)
    # fitted tier 1. They part company when the noise is dominated by drift
    # slower than a symbol: differencing cancels it, and the marginal figure
    # then understates the channel badly. Tier 2 is that case, reading a
    # marginal d' of 0.08 on a run that decodes at BER 0.09.
    if code == "manchester":
        signed = true_power[0::2] - true_power[1::2]
        # +1 where the transmitted bit was 1, so the statistic is positive
        # when the channel is working, whatever its polarity.
        signed = signed * truth_chips[0::2]
        pair_sd = float(signed.std())
        d_prime_paired = abs(float(signed.mean())) / pair_sd if pair_sd > 0 else float("nan")
    else:
        d_prime_paired = d_prime / math.sqrt(2)

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
        "p_below_chance": binom_p_below_chance(pay_err, n_pay * frames),
        "voted_errors": voted_err,
        "voted_ber": voted_err / n_pay,
        "capacity_bps": raw_bps * bsc_capacity(ber),
        "sync_corr": float(np.nanmean(frame_corr)),
        "sync_peak": float(scores[best]),
        "sync_runner_up": runner_up(scores, best, SYNC_OVERSAMPLE),
        # Truth, used for reporting only -- the sync above never saw it.
        "sync_error_chips": (t_sync - tx["tsc_start"]) / chip_tsc,
        "receiver": rx.get("receiver", "rapl"),
        "watched_cpu": (rx.get("watch", [None])[freq_column]
                        if rx.get("receiver") == "freq" else None),
        "unit": unit,
        "polarity": polarity,
        "delta_w": delta_w,
        "noise_sd_w": noise_sd,
        "d_prime": d_prime,
        "d_prime_paired": d_prime_paired,
        "ber_predicted": 0.5 * math.erfc(d_prime_paired / math.sqrt(2)),
        # Tier 2 polls a level on a fixed grid, so it has neither an
        # overshoot regime nor a zero-tick failure mode; its analogue is
        # late_polls, which the runner records in the manifest. Reporting 0
        # here rather than omitting the keys keeps one table for both tiers.
        "overshoot_fraction": (rx["rapl_overshoots"] / max(rx["samples_written"], 1)
                               if "rapl_overshoots" in rx else 0.0),
        "rapl_period_ms": rx.get("rapl_period_ms", float("nan")),
        "samples_per_chip": chip_tsc / (rx.get("rapl_period_tsc")
                                        or rx.get("interval_tsc") or float("nan")),
        "zero_tick_fraction": (rx["zero_tick_samples"] / max(rx["samples_written"], 1)
                               if "zero_tick_samples" in rx else 0.0),
        "message": tx.get("message"),
        "decoded_message": bits_to_text(voted) if tx.get("message") else None,
    }


def binom_p_below_chance(errors, n):
    """One-sided exact p that `errors` or fewer arose from coin flips.

    The tier-1 channel is far enough from chance that this adds nothing. Tier 2
    is not: a run there can sit at BER 0.19 over 32 bits, which looks weak and
    is in fact p < 1e-3. Exact rather than normal-approximated because the bit
    counts are small and the interesting region is the tail.
    """
    if n <= 0:
        return float("nan")
    errors = min(errors, n)
    # Integer division throughout: a run can carry a couple of thousand bits,
    # and 2.0**2048 overflows a float long before the ratio does.
    return sum(math.comb(n, k) for k in range(errors + 1)) / (1 << n)


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
    ap.add_argument("--freq-column", type=int, default=0,
                    help="for tier-2 runs watching several CPUs, which one to "
                         "decode (index into the receiver's --watch list)")
    ap.add_argument("--json", metavar="PATH", help="also write the rows as JSON")
    args = ap.parse_args()

    rows, failures, warnings = [], [], []
    for d in args.results:
        d = Path(d)
        manifest = json.loads((d / "manifest.json").read_text())
        for entry in manifest["runs"]:
            if "tx" not in entry:
                continue
            span = None
            if args.sync_span_symbols is not None:
                span = args.sync_span_symbols * float(entry["tx"]["symbol_tsc"])
            rows.append(decode_run(entry, d / entry["csv"], sync_span=span,
                                   freq_column=args.freq_column))

    if not rows:
        raise SystemExit("no covert runs found (is this a driver experiment?)")

    for r in rows:
        r["settled"] = r["overshoot_fraction"] <= MAX_OVERSHOOT_FRACTION

    # Tier 1 reports watts, tier 2 kHz. Printing kHz raw put a 660 MHz noise
    # figure on the page as "660223", which reads as gibberish; scale it.
    freq = any(r["receiver"] == "freq" for r in rows)
    scale, unit = (1e-3, "MHz") if freq else (1.0, "W")

    print(hr("per-run decode"))
    print(f"  {'run':>22} {'sym us':>7} {'bit/s':>7} {'BER':>7} {'pre':>6} "
          f"{'vote':>6} {'d' + unit:>8} {'sd ' + unit:>8} {'d-marg':>7} "
          f"{'d-pair':>7} {'Q(pair)':>8} {'ovr%':>5} {'sync':>7} {'late':>5}")
    for r in sorted(rows, key=lambda r: (r["label"], r["repeat"])):
        print(f"  {r['tag']:>22} {r['symbol_us']:>7.0f} {r['raw_bps']:>7.1f} "
              f"{r['ber']:>7.4f} {r['preamble_ber']:>6.3f} "
              f"{r['voted_ber']:>6.3f} {r['delta_w'] * scale:>8.3f} "
              f"{r['noise_sd_w'] * scale:>8.3f} {r['d_prime']:>7.2f} "
              f"{r['d_prime_paired']:>7.2f} {r['ber_predicted']:>8.4f} "
              f"{r['overshoot_fraction'] * 100:>5.2f} "
              f"{r['sync_error_chips']:>+7.2f} {r['late_chips']:>5}"
              f"{'' if r['settled'] else '   << unsettled sampler'}")
    print(f"\n  BER over payload bits; pre = preamble BER (a sync check);")
    print(f"  vote = BER after majority vote across frames; d{unit} and sd are")
    print(f"  the per-chip separation and noise, d-marg their ratio. d-pair is")
    print(f"  the same for the within-symbol difference the decision actually")
    print(f"  uses, and Q(pair) is the error rate it predicts -- compare that")
    print(f"  with BER, not d-marg, which ignores noise shared between chips.")
    print(f"  ovr% is the share of RAPL edges seen late; sync = recovered")
    print(f"  start minus true start, in chips.")
    if freq:
        cpus = sorted({r["watched_cpu"] for r in rows if r["watched_cpu"] is not None})
        print(f"  Tier 2: decoding cpu{cpus} frequency. A negative separation is")
        print(f"  the expected direction -- the heavier operand throttles the part.")

    print(hr("by condition (between-repeat spread is the error bar)"))
    print(f"  {'label':>18} {'n':>3} {'bit/s':>7} {'BER':>8} {'sd':>8} "
          f"{'capacity b/s':>13} {'vote BER':>9} {'p<chance':>10}  settled-only")
    for label, group in sorted(aggregate(rows).items()):
        bers = np.array([g["ber"] for g in group])
        caps = np.array([g["capacity_bps"] for g in group])
        votes = np.array([g["voted_ber"] for g in group])
        sd = bers.std(ddof=1) if len(bers) > 1 else float("nan")
        # Pooled over repeats: the bits are independent across runs, so the
        # exact test applies to their sum and is far stronger than any one run.
        tot_bits = sum(g["payload_bits"] * g["frames"] for g in group)
        tot_err = sum(int(round(g["ber"] * g["payload_bits"] * g["frames"]))
                      for g in group)
        pval = binom_p_below_chance(tot_err, tot_bits)
        # Both columns, always. Restricting to settled runs is a defensible
        # instrument-quality criterion -- the overshoot rate is measured by the
        # receiver and knows nothing about the decode -- but a filtered number
        # quoted on its own is how selection bias gets in, so the unfiltered
        # one stays next to it.
        keep = [g["ber"] for g in group if g["settled"]]
        note = (f"{np.mean(keep):.4f} (n={len(keep)})" if keep else "none settled")
        print(f"  {label:>18} {len(group):>3} {group[0]['raw_bps']:>7.1f} "
              f"{bers.mean():>8.4f} {sd:>8.4f} {caps.mean():>13.1f} "
              f"{votes.mean():>9.4f} {pval:>10.2e}  {note}")

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
        if not r["settled"]:
            warnings.append(f"{r['tag']}: {r['overshoot_fraction']:.1%} of RAPL "
                            f"edges seen late -- per-chip noise is "
                            f"{r['noise_sd_w']:.3f} W and the decode is degraded "
                            f"by the instrument, not by the channel")
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

    for w in warnings:
        print(f"  WARN  {w}")
    if failures:
        for f in failures:
            print(f"  FAIL  {f}")
    else:
        print("  all gates pass" + (" (with warnings)" if warnings else ""))

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2))

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
