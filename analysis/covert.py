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
# Distinct values a receiver trace must take before its BER means anything. A
# CPU parked at a fixed frequency yields a handful; the runner already warns on
# exactly zero changes, which is one value short of catching this.
MIN_TRACE_VALUES = 8
# Spread of the receiver's own level, (p95 - p5) / p95, below which a Config-B
# run had no throttling to modulate. Tiers 2 and 3 exist only while the part is
# throttling -- that is a precondition of the attack, not a tuning detail -- and
# a run where it did not carries no channel for reasons that have nothing to do
# with the rate being tested. Measured: the one run in the tier-2 sweep that
# behaves this way reads 0.041 against 0.28-0.36 for every other, and tier 3
# spans 0.40-0.48, so this sits an order of magnitude clear of both.
MIN_LEVEL_RANGE = 0.10


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


def sync_score(trace, offsets, chip_tsc, pattern, frame_tsc, frames,
               mode="raw", code="manchester"):
    """Normalised preamble correlation, summed over every frame.

    Frame length is part of the protocol, so a receiver may add the
    correlation from all F preambles at a single candidate offset. That turns
    F weak peaks into one strong one and is what makes sync work at symbol
    periods where a single preamble would not be enough.

    `mode` selects what the correlation runs on. "raw" is the default and is
    what every published summary was decoded with; "diff" is kept because it
    was measured, and the measurement is worth having on record.

    "raw"  correlates the chip levels, with the mean over the preamble window
           removed.

    "diff" differences the two chips of each Manchester symbol first, the way
           the demodulator does, then correlates against the differenced
           pattern.

    The motivating idea was that "diff" would add the demodulator's drift
    rejection to acquisition -- d-pair runs 5-30x d-marg in the tier-2 tables,
    so the demodulator was plainly getting something the correlator was not.
    It does not, and the reason is that the correlator was never missing it: a
    Manchester chip pattern is pair-antisymmetric, so correlating against it
    *is* a within-symbol difference. Any drift constant across a symbol
    already cancels in the numerator. On a clean tier-1 trace the two
    numerators correlate at 0.93, and they differ at all only because the
    13-bit Barker preamble is unbalanced (nine ones to four zeros), which
    gives the differenced pattern a DC component that the raw one does not
    have.

    What "diff" really changes is the denominator: it normalises by the energy
    of the 13 differenced values rather than of all 26 chips. Every peak rises
    -- median ratio 1.33 over a tier-1 trace -- and the noise peaks rise with
    the signal ones, which is the whole problem. Measured over the tier-1 and
    tier-2 sweeps (`results/20260903-115606-phase2_tier1_rate`,
    `results/20260903-143109-phase2_tier2_covert`):

      - tier 1 gains one run. `sym_04ms` goes from 1/3 to 2/3 acquired, BER
        0.339 -> 0.197 and capacity 78 -> 125 bit/s, as `sym_04ms_r1` finally
        lands on the frame and decodes at its own oracle value of 0.087.
      - every other tier-1 rate pays a little: 0.048 -> 0.060 at 3 ms,
        0.060 -> 0.066 at 6 ms, 0.016 -> 0.022 at 24 ms.
      - tier 2 loses the headline. At 2 bit/s acquisition falls from 3/3 to
        1/3 (the voted BER stays 0), because the flatter peak moves the
        recovered sync from +0.1/+0.2 chips to +0.6 and past the half-chip
        criterion.
      - the A/A controls rise, which is what settles it: tier-1 A/A peaks go
        0.26-0.28 -> 0.35-0.38 and tier-2 0.20-0.26 -> 0.38-0.55. On tier 2
        that destroys the |pk| acquisition statistic of thesis section 6.1 --
        failed runs reach 0.80 against 0.79 for the one acquired run.

    A correlator whose floor on a transmission carrying nothing nearly doubles
    is a worse instrument than one that finds an extra frame, so "raw" stays
    the default. The lever acquisition actually needs is a longer preamble,
    not a different normalisation of this one.

    Only Manchester has an in-symbol reference to difference against, so for
    NRZ both modes are the same thing.
    """
    n = len(pattern)
    rel = np.arange(n + 1, dtype=np.float64) * chip_tsc
    pair = mode == "diff" and code == "manchester" and n % 2 == 0
    c = pattern[0::2] - pattern[1::2] if pair else pattern
    c = c - c.mean()
    cnorm = np.linalg.norm(c)

    total = np.zeros(len(offsets), dtype=np.float64)
    for f in range(frames):
        edges = offsets[:, None] + f * frame_tsc + rel[None, :]
        x = trace.window_power(edges)
        if pair:
            x = x[..., 0::2] - x[..., 1::2]
        x = x - x.mean(axis=-1, keepdims=True)
        norm = np.linalg.norm(x, axis=-1)
        with np.errstate(invalid="ignore", divide="ignore"):
            total += np.where(norm > 0, (x @ c) / (norm * cnorm), 0.0)
    return total / frames


def soft_margin(chips, code, nrz_window=16, polarity=1.0):
    """Chip levels -> the signed decision statistic, one per bit.

    Positive means the bit decodes as 1. The hard decision is its sign; the
    magnitude is how far from the threshold it landed, which is what lets a
    majority vote break an exact tie on something better than a coin.

    `polarity` is +1 when the ON operand reads high and -1 when it reads low.
    Tier 2 is the inverted case: the heavier operand draws more power, the
    part throttles, and the reported frequency *falls*. The receiver is not
    told which it is looking at -- see resolve_polarity.
    """
    chips = chips * polarity
    if code == "manchester":
        pairs = chips.reshape(-1, 2)
        return pairs[:, 0] - pairs[:, 1]
    # NRZ has no in-symbol reference, so it needs a running baseline. A
    # centred moving average is the cheapest one that tracks drift slower
    # than the window without tracking the symbols themselves.
    k = min(nrz_window, len(chips))
    pad = np.pad(chips, (k // 2, k - k // 2 - 1), mode="edge")
    baseline = np.convolve(pad, np.ones(k) / k, mode="valid")
    return chips - baseline


def demodulate(chips, code, nrz_window=16, polarity=1.0):
    """Chip levels -> bits."""
    return (soft_margin(chips, code, nrz_window, polarity) > 0).astype(np.int8)


def decode_run(entry, csv_path, sync_span=None, freq_column=0, sync_mode="raw"):
    tx, rx = entry["tx"], entry["rx"]
    raw = np.loadtxt(csv_path, delimiter=",", skiprows=1, dtype=np.float64, ndmin=2)
    if raw.size == 0:
        raise ValueError(f"{csv_path} contains no samples")

    # Tier 1 records an energy accumulator, tier 2 a frequency level; the
    # cumulative-integral form makes every window query below identical.
    receiver = rx.get("receiver", "rapl")
    if receiver in ("freq", "timing"):
        # A tier-2 receiver may watch several CPUs, since an attacker need not
        # know which cores the victim occupies. Column 0 is the first --watch
        # entry; which one decodes best is an experimental question. Tier 3
        # has a single column and ignores this.
        watched = rx.get("watch", [None])
        if freq_column >= len(watched):
            raise ValueError(f"{csv_path}: --freq-column {freq_column} but only "
                             f"{len(watched)} CPU(s) watched")
        level = raw[:, 1 + freq_column]
        trace = Trace.from_levels(raw[:, 0], level, rx["tsc_hz"])
        # Tier 3's level is the workload's own duration, not a frequency, and
        # runs the other way up: throttling makes it larger.
        unit = "kHz" if receiver == "freq" else "TSC"
    else:
        trace = Trace(tsc=raw[:, 0], ticks=raw[:, 1], dtsc=raw[:, 2],
                      energy_unit_j=rx["energy_unit_j"], tsc_hz=rx["tsc_hz"])
        level = raw[:, 1]
        unit = "W"

    trace_sd = float(np.std(level))
    trace_unique = int(np.unique(level).size)

    code = tx["code"]
    chip_tsc = float(tx["chip_tsc"])
    chips_per_bit = int(tx["chips_per_bit"])
    preamble, payload = tx["preamble_bits"], tx["payload_bits"]
    frames, bits_per_frame = int(tx["frames"]), int(tx["bits_per_frame"])
    frame_chips = bits_per_frame * chips_per_bit
    frame_tsc = frame_chips * chip_tsc

    # How far the receiver's own level moved, over the transmission itself.
    # Tiers 2 and 3 read a throttling response, so a run in which the part
    # never throttled carries no channel whatever the symbol rate -- and still
    # produces a BER that lands in the table beside runs that measured
    # something.
    #
    # Restricted to the transmission window because the recording is not: it
    # opens before the transmitter starts and closes after it stops, and a
    # *watched victim core* is idle at 400 MHz outside it. Over the whole
    # trace that idle stretch dominates the 5th percentile and every watched
    # core looks lively.
    #
    # Percentiles rather than an SD, because the two are not interchangeable
    # here. sym_256ms_r0 in the tier-2 sweep has a per-chip SD of 7.87 MHz
    # against 200-700 for every other run, which reads like a parked CPU. It
    # is not one: its raw trace takes 2064 distinct values with a lag-1
    # autocorrelation of 0.993. Its band is 3740-3900 MHz where every other
    # run's is 2400-3900 -- the part stayed near its ceiling and never
    # throttled, so there was nothing to modulate. Its chip SD is small
    # because that noise is white and averages down over a 256 ms chip, where
    # the other runs' governor wander does not. A dispersion statistic
    # confuses "nothing moved" with "what moved averaged away"; a band does not.
    tx_lo = float(tx["tsc_start"])
    tx_hi = tx_lo + frames * frame_tsc
    in_tx = (raw[:, 0] >= tx_lo) & (raw[:, 0] <= tx_hi)
    active = level[in_tx] if in_tx.sum() >= 20 else level
    p5, p95 = float(np.percentile(active, 5)), float(np.percentile(active, 95))
    level_range = (p95 - p5) / abs(p95) if p95 else 0.0

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
    scores = sync_score(trace, offsets, chip_tsc, pattern, frame_tsc, frames,
                        mode=sync_mode, code=code)
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
        sc = sync_score(trace, cand, chip_tsc, pattern, frame_tsc, 1,
                        mode=sync_mode, code=code) * polarity
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
    decoded_frames, soft_frames = [], []
    for f in range(frames):
        margin = soft_margin(chip_power[f], code, polarity=polarity)
        bits = (margin > 0).astype(np.int8)
        pre_err += int(np.sum(bits[:n_pre] != truth_pre))
        pay = bits[n_pre:]
        pay_err += int(np.sum(pay != truth_pay))
        decoded_frames.append(pay)
        soft_frames.append(margin[n_pre:])

    n_pay = len(truth_pay)
    ber = pay_err / (n_pay * frames)

    # Share of decoded payload bits that came out 1, and the same for the
    # transmitted payload. A decoder fed a featureless trace still emits bits,
    # and if those are skewed the same way the payload happens to be, the BER
    # flatters it. Balanced payloads (see tx.c) make that impossible in
    # expectation; these two columns are how you check the payload really was
    # balanced and see the decode's own bias.
    decoded_ones = float(np.mean(np.concatenate(decoded_frames)))
    truth_ones = float(np.mean(truth_pay))

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

    # --- acquisition versus demodulation ----------------------------------
    #
    # A decode can fail two ways and they need different fixes: the receiver
    # never found the frame (acquisition), or it found it and could not tell
    # the chips apart (demodulation). The free-running BER above conflates
    # them, and in this sweep they are not evenly mixed -- runs land either
    # within a chip of the truth or hundreds of chips away, with nothing in
    # between, so a failed acquisition contributes a BER of exactly chance and
    # says nothing about whether the channel carried the bits.
    #
    # Demodulating on the *true* chip grid separates them at no extra cost:
    # true_power is already computed above for the d' diagnostics. Like those,
    # it reads ground truth, so it is a diagnostic and not something a real
    # receiver could do -- it answers "would the bits have been there if sync
    # had landed", which is exactly what the free-running BER cannot.
    oracle_pol = 1.0 if float(np.dot(true_power - true_power.mean(),
                                     truth_chips)) >= 0 else -1.0
    oracle_err = 0
    for f in range(frames):
        chunk = true_power[f * frame_chips:(f + 1) * frame_chips]
        bits = demodulate(chunk, code, polarity=oracle_pol)
        oracle_err += int(np.sum(bits[n_pre:] != truth_pay))
    ber_oracle = oracle_err / (n_pay * frames)

    # Whether acquisition actually succeeded (ground truth), and the statistic
    # a receiver could use to decide that for itself. A real attacker has no
    # sync_error_chips; what it has is the height of the peak it found.
    #
    # The obvious candidate -- peak over best sidelobe -- does not work, and
    # that is worth recording rather than dropping. Over this sweep it reads
    # 1.00-1.19 for failed acquisitions and 1.04-1.43 for successful ones,
    # completely overlapping, because with ~10^5 candidate offsets the largest
    # noise peak sits just under the largest peak of any kind. The *absolute*
    # normalised correlation does separate them (0.26-0.71 failed against
    # 0.64-1.00 acquired), so that is the one reported.
    acquired = abs((t_sync - tx["tsc_start"]) / chip_tsc) <= 0.5
    runner = runner_up(scores, best, SYNC_OVERSAMPLE)
    sync_margin = (abs(scores[best]) / abs(runner)
                   if runner and np.isfinite(runner) and abs(runner) > 0
                   else float("inf"))

    # Majority vote across the repeated frames: the cheapest possible ECC,
    # and the honest way to show what repetition buys against the raw BER.
    #
    # An even frame count -- 4 for tiers 2 and 3, 8 for tier 1 -- can split
    # exactly, and `mean > 0.5` resolves every such split to 0. That is a
    # systematic bias toward zero bits in precisely the bits the vote was
    # least sure about, and on a balanced payload it costs half of them.
    # Ties go to the summed decision statistic instead, which is free: the
    # frames that voted 1 did so by some margin and the frames that voted 0
    # by some other, and the larger total is the better guess.
    stacked = np.stack(decoded_frames)
    votes = stacked.mean(axis=0)
    soft_sum = np.sum(np.stack(soft_frames), axis=0)
    voted = np.where(votes == 0.5, soft_sum > 0, votes > 0.5).astype(np.int8)
    voted_err = int(np.sum(voted != truth_pay))
    voted_ties = int(np.sum(votes == 0.5))

    symbol_s = float(tx["symbol_us"]) / 1e6
    raw_bps = 1.0 / symbol_s
    # A majority vote across F frames costs a factor F in rate, because the F
    # frames carry one payload between them. Quoting "zero errors after the
    # vote" against the raw rate charges nothing for the repetition; this is
    # what the vote actually delivers.
    voted_bps = raw_bps / frames

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
        # The check late_chips cannot make. late_chips asks whether the
        # transmitter's control thread hit its deadlines -- a loop doing one
        # store and a fence, which essentially cannot miss. missed_chips asks
        # whether the victims observed the change, which is what actually
        # modulates the die. None for runs written before tx counted it.
        "missed_chips": (int(tx["missed_chips"])
                         if "missed_chips" in tx else None),
        "trace_sd": trace_sd,
        "trace_unique": trace_unique,
        "trace_unit": unit,
        "level_range": level_range,
        "level_p5": p5,
        "level_p95": p95,
        "voted_ties": voted_ties,
        "frames": frames,
        # payload_bits is the number of *distinct* bits the message carries.
        # Each is transmitted `frames` times, so the two counts differ by that
        # factor and a bound quoted over the larger one is not a bound over
        # independent truths -- see the bit-count note in main().
        "payload_bits": n_pay,
        "transmitted_bits": n_pay * frames,
        "preamble_ber": pre_err / (n_pre * frames),
        "ber": ber,
        "decoded_ones": decoded_ones,
        "truth_ones": truth_ones,
        "p_below_chance": binom_p_below_chance(pay_err, n_pay * frames),
        "voted_errors": voted_err,
        "voted_ber": voted_err / n_pay,
        "voted_bps": voted_bps,
        "capacity_bps": raw_bps * bsc_capacity(ber),
        "ber_oracle_sync": ber_oracle,
        "acquired": bool(acquired),
        "sync_margin": float(sync_margin),
        "sync_corr": float(np.nanmean(frame_corr)),
        "sync_peak": float(scores[best]),
        "sync_runner_up": runner_up(scores, best, SYNC_OVERSAMPLE),
        # Truth, used for reporting only -- the sync above never saw it.
        "sync_error_chips": (t_sync - tx["tsc_start"]) / chip_tsc,
        "receiver": receiver,
        "watched_cpu": (rx.get("watch", [None])[freq_column]
                        if receiver == "freq" else None),
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
    ap.add_argument("--sync-mode", choices=("raw", "diff"), default="raw",
                    help="what the preamble correlation runs on: 'raw' "
                         "correlates the chip levels and is what every "
                         "published summary used; 'diff' differences each "
                         "Manchester symbol's two chips first, which changes "
                         "the normalisation and raises the noise floor with "
                         "it -- see sync_score for what it measured")
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
                                   sync_mode=args.sync_mode,
                                   freq_column=args.freq_column))

    if not rows:
        raise SystemExit("no covert runs found (is this a driver experiment?)")

    for r in rows:
        r["settled"] = r["overshoot_fraction"] <= MAX_OVERSHOOT_FRACTION

    # Tier 1 reports watts, tier 2 kHz. Printing kHz raw put a 660 MHz noise
    # figure on the page as "660223", which reads as gibberish; scale it.
    freq = any(r["receiver"] == "freq" for r in rows)
    timing = any(r["receiver"] == "timing" for r in rows)
    if freq:
        scale, unit = 1e-3, "MHz"
    elif timing:
        scale, unit = 1e-3, "kTSC"
    else:
        scale, unit = 1.0, "W"

    print(hr("per-run decode"))
    print(f"  {'run':>22} {'sym us':>7} {'bit/s':>7} {'BER':>7} {'BER|snc':>8} "
          f"{'acq':>4} {'|pk|':>5} {'pre':>6} "
          f"{'vote':>6} {'ties':>5} {'cap b/s':>8} {'d' + unit:>8} {'sd ' + unit:>8} {'d-marg':>7} "
          f"{'d-pair':>7} {'Q(pair)':>8} {'ovr%':>5} {'sync':>7} {'late':>5}")
    for r in sorted(rows, key=lambda r: (r["label"], r["repeat"])):
        ties = "{}/{}".format(r["voted_ties"], r["payload_bits"])
        print(f"  {r['tag']:>22} {r['symbol_us']:>7.0f} {r['raw_bps']:>7.1f} "
              f"{r['ber']:>7.4f} {r['ber_oracle_sync']:>8.4f} "
              f"{('yes' if r['acquired'] else 'NO'):>4} "
              f"{abs(r['sync_peak']):>5.2f} "
              f"{r['preamble_ber']:>6.3f} "
              f"{r['voted_ber']:>6.3f} {ties:>5} {r['capacity_bps']:>8.1f} "
              f"{r['delta_w'] * scale:>8.3f} "
              f"{r['noise_sd_w'] * scale:>8.3f} {r['d_prime']:>7.2f} "
              f"{r['d_prime_paired']:>7.2f} {r['ber_predicted']:>8.4f} "
              f"{r['overshoot_fraction'] * 100:>5.2f} "
              f"{r['sync_error_chips']:>+7.2f} {r['late_chips']:>5}"
              f"{'' if r['settled'] else '   << unsettled sampler'}")
    tie_bits = sum(r["voted_ties"] for r in rows)
    vote_bits = sum(r["payload_bits"] for r in rows)
    if tie_bits:
        print(f"\n  ties: {tie_bits} of {vote_bits} voted bits split evenly across "
              f"frames and were\n  decided on the summed decision margin rather than "
              f"resolved to 0. An even\n  frame count is what makes that possible; a "
              f"high tie rate is also a symptom,\n  since frames only disagree this "
              f"much when there is nothing for them to agree on.")

    print(f"\n  BER over payload bits, decoded at the sync the receiver recovered.")
    print(f"  BER|snc is the same decode on the TRUE chip grid, so it measures")
    print(f"  demodulation with acquisition removed; acq says whether the recovered")
    print(f"  sync landed within half a chip. A run with acq=NO has a BER at chance")
    print(f"  whatever the channel did, so read BER and BER|snc together: they part")
    print(f"  company exactly where the failure is acquisition and not noise. |pk| is")
    print(f"  the winning correlation, the acquisition statistic a real receiver could")
    print(f"  compute for itself -- it has no sync column to check. Peak over best")
    print(f"  sidelobe, the obvious alternative, does not separate the two at all.")
    print(f"  pre = preamble BER (a sync check);")
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
    if timing:
        print(f"  Tier 3: the level is the receiver's own workload duration, so a")
        print(f"  POSITIVE separation is the expected direction -- throttling makes")
        print(f"  it slower. Polarity is recovered from the preamble either way.")

    print(hr("by condition (between-repeat spread is the error bar)"))
    print("  THIS is the reporting unit. A single repeat is not quotable -- the")
    print("  project's own rule everywhere else -- and at a given rate the repeats")
    print("  here span far more than any one of them admits.")
    print(f"\n  {'label':>18} {'n':>3} {'raw b/s':>8} {'BER':>8} {'SD':>8} "
          f"{'acq':>5} {'BER|snc':>8} {'cap b/s':>8} {'vote BER':>9} {'vote b/s':>9} "
          f"{'bits d/tx':>11} {'p<chance':>10}  settled-only")
    zero_error = []
    for label, group in sorted(aggregate(rows).items()):
        bers = np.array([g["ber"] for g in group])
        votes = np.array([g["voted_ber"] for g in group])
        oracle = np.array([g["ber_oracle_sync"] for g in group])
        acq = np.array([g["acquired"] for g in group])
        sd = bers.std(ddof=1) if len(bers) > 1 else float("nan")
        raw = group[0]["raw_bps"]
        # Capacity of the channel as *aggregated*, not the mean of the
        # per-repeat capacities. 1 - H(p) is convex, so averaging per-run
        # capacities is dominated by the best repeat and reintroduces exactly
        # the selection this table exists to remove: at 500 bit/s the repeats
        # give 0.0 / 0.0 / 385.6 b/s, whose mean of 128.6 describes no run and
        # no channel. The per-repeat figures are in the table above.
        cap = raw * bsc_capacity(float(bers.mean()))
        # Distinct payload bits carried, and the number of transmissions of
        # them. The p-value below is over the latter; see the note after the
        # table for why that overstates the evidence.
        n_distinct = sum(g["payload_bits"] for g in group)
        tot_bits = sum(g["transmitted_bits"] for g in group)
        tot_err = sum(int(round(g["ber"] * g["transmitted_bits"])) for g in group)
        pval = binom_p_below_chance(tot_err, tot_bits)
        if tot_err == 0:
            zero_error.append((label, n_distinct, tot_bits))
        # Both columns, always. Restricting to settled runs is a defensible
        # instrument-quality criterion -- the overshoot rate is measured by the
        # receiver and knows nothing about the decode -- but a filtered number
        # quoted on its own is how selection bias gets in, so the unfiltered
        # one stays next to it.
        keep = [g["ber"] for g in group if g["settled"]]
        note = (f"{np.mean(keep):.4f} (n={len(keep)})" if keep else "none settled")
        print(f"  {label:>18} {len(group):>3} {raw:>8.1f} "
              f"{bers.mean():>8.4f} {sd:>8.4f} "
              f"{str(int(acq.sum())) + '/' + str(len(acq)):>5} {oracle.mean():>8.4f} "
              f"{cap:>8.1f} "
              f"{votes.mean():>9.4f} {raw / group[0]['frames']:>9.1f} "
              f"{str(n_distinct) + '/' + str(tot_bits):>11} {pval:>10.2e}  {note}")

    print("\n  acq       : repeats whose recovered sync landed within half a chip.")
    print("  BER|snc   : mean BER demodulated on the true chip grid. Where this is")
    print("              far below BER, the rate was limited by acquisition and not")
    print("              by per-chip noise, and the remedy is a better preamble")
    print("              rather than a slower symbol.")
    print("  cap b/s   : raw rate x (1 - H(BER)), the BSC capacity at the aggregated")
    print("              BER. This is the rate/reliability figure to quote and compare")
    print("              against the literature; BER at a rate is not one number.")
    print("  vote b/s  : raw / frames, the rate actually delivered after the majority")
    print("              vote, since the F frames carry one payload between them.")
    print("  bits d/tx : distinct payload bits / times they were transmitted. p<chance")
    print("              is a binomial over the second, which assumes every")
    print("              transmission is an independent truth. It is not: a decoder")
    print("              biased but independent of the message errs the same way on")
    print("              every repeat of a bit, so the effective n is the first")
    print("              number. Read p as an upper bound on the evidence.")
    if zero_error:
        print("\n  zero errors bounds BER only as well as the bit count allows (rule of")
        print("  three, 95%): over transmissions, and over distinct bits --")
        for label, n_distinct, tot_bits in zero_error:
            print(f"    {label:>18}  <= {3 / tot_bits:.1e} over {tot_bits} tx"
                  f"   |  <= {3 / n_distinct:.1e} over {n_distinct} distinct")

    # --- gates ------------------------------------------------------------
    print(hr("gates"))
    for r in rows:
        if r["late_chips"]:
            failures.append(f"{r['tag']}: {r['late_chips']} late chips -- the "
                            f"transmitter could not hold its schedule, so this "
                            f"run's BER is not a property of the channel")
        if r["missed_chips"]:
            failures.append(f"{r['tag']}: {r['missed_chips']} of "
                            f"{r['payload_bits'] * r['frames']} bits' worth of "
                            f"chips were never observed by a victim -- the "
                            f"modulation was scheduled but did not reach the die")
        if r["trace_unique"] < MIN_TRACE_VALUES:
            failures.append(f"{r['tag']}: receiver trace takes only "
                            f"{r['trace_unique']} distinct values -- there was "
                            f"nothing there to decode, so its BER is not "
                            f"evidence about the channel")
        elif (r["receiver"] in ("freq", "timing")
                and r["level_range"] < MIN_LEVEL_RANGE):
            warnings.append(f"{r['tag']}: the receiver's level spans only "
                            f"{r['level_range']:.1%} of itself "
                            f"({r['level_p5']:.0f}-{r['level_p95']:.0f} "
                            f"{r['trace_unit']}), so the part was not throttling "
                            f"and there was nothing for the transmitter to "
                            f"modulate -- this run's BER is about the machine's "
                            f"state, not about the rate it was testing")
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
        if abs(r["truth_ones"] - 0.5) > 0.2 and r["payload_bits"] >= 8:
            warnings.append(f"{r['tag']}: payload is {r['truth_ones']:.0%} ones, "
                            f"so BER is vulnerable to a biased decode scoring "
                            f"well by coincidence -- use a balanced payload")

    # A/A is judged pooled across repeats, not per run. A single A/A here
    # carries as few as 32 bits, where the SD of BER is 0.09 and a 3-sigma
    # excursion is a 1-in-140 event -- the gate would fire on noise about as
    # often as on a real problem. Pooling is also the project's stated
    # reporting unit everywhere else.
    for label, group in sorted(aggregate(rows).items()):
        if not group[0]["aa_control"]:
            continue
        bits = sum(g["payload_bits"] * g["frames"] for g in group)
        errs = sum(int(round(g["ber"] * g["payload_bits"] * g["frames"]))
                   for g in group)
        pooled = errs / bits
        p = binom_p_below_chance(errs, bits)
        print(f"  A/A {label}: pooled BER {pooled:.3f} over {bits} bits, "
              f"p = {p:.3g}")
        if pooled < AA_BER_FLOOR and p < 0.01:
            failures.append(f"{label}: A/A pooled BER {pooled:.3f} over {bits} "
                            f"bits (p = {p:.2g}) -- the decoder is finding "
                            f"structure in a transmission that has none")
        elif any(g["ber"] < AA_BER_FLOOR for g in group):
            worst = min(g["ber"] for g in group)
            warnings.append(f"{label}: one A/A repeat reached BER {worst:.3f} "
                            f"while the pooled figure is {pooled:.3f} "
                            f"(p = {p:.2g}) -- single-run noise, recorded not hidden")

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
