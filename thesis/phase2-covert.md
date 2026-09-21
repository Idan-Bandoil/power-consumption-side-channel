# A Covert Channel Out of Operand Power

*Draft — Phase 2 chapter, **partial**. All three receiver tiers are measured; the
placement matrix, a controlled comparison across the tiers, and the literature comparison
are not, and §9 says what is missing. Every number here is reproducible from `results/`
plus the run manifests, cited inline as a run directory, and every rate figure is the mean
over three repeats rather than any single run. Tier-1 measurements are Config-A (turbo
disabled, frequency pinned); tiers 2 and 3 are necessarily Config-B, and §9 explains why
that makes them not directly comparable with tier 1.*

## 1. What this chapter asks

The previous chapter established that a victim's operand is visible in package power,
and quantified how visible: within a fixed victim the difference between an all-zero and
an all-ones 32-bit operand is about 1.9 W, and the detector converts that into an
accuracy-versus-*n* curve reaching 95% at one to eight RAPL samples.

That is a correlation. This chapter asks whether it is a *channel*: can a process with no
shared memory, no shared files and no IPC send chosen bits to another process, and can
those bits be recovered with a stated error rate at a stated rate? The distinction
matters because a correlation is a property of an instrument and a channel is a property
of a system. An effect that is trivially visible when you know exactly when to look can
still be useless to a receiver that has to find the message itself.

Three things have to be true, and each is a place the result could fail:

1. The transmitter must modulate the operand fast enough and precisely enough that
   symbol boundaries mean something (§3).
2. The receiver must recover the message without being told where it starts (§4).
3. The error rate must be low enough, at a rate high enough, to be worth reporting — and
   the whole thing must fail when nothing is transmitted (§5, §6).

The results, in order of how much they constrain the rest:

1. The channel works. Under a privileged receiver it carries **241 bit/s of capacity** at
   a 3 ms symbol, and clears to zero observed errors after an eight-frame vote from
   167 bit/s down (§5). That is an order of magnitude above the expectation this project
   set out with, which was "low tens of bits/s".
2. **It also works with no privilege at all.** A receiver reading only world-readable
   `scaling_cur_freq` decodes at 2 bit/s with a bit-error rate of 0.083 and no errors
   after a majority vote (§8.1). That is the result the security claim rests on, and it
   costs more than two orders of magnitude of capacity.
3. **And it works for a receiver that reads nothing at all** — one that only times its
   own workload, with no file to revoke and no interface to restrict. It matches the
   file-reading receiver and beats it at 3.9 bit/s (§8.3). So the entire cost of the
   ladder is the step from root to unprivileged; giving up the last interface costs
   nothing. Any mitigation aimed at the interface rather than at the throttling is
   therefore defeated before it starts.
4. The error rate of a *demodulated* frame is set by a single quantity — but not the one
   that first suggests itself. What predicts it is the separation of the *within-symbol
   difference* the decision actually uses, not the marginal separation of a chip (§6). The
   two agree only when chip noise is white, and tier 2 is precisely where they do not.
5. **A decode fails two ways, and at the fast end the dominant one is that the receiver
   never finds the frame** (§6.1). Separating acquisition from demodulation changes what
   the rate ceiling means: runs reported at chance turn out to carry a working channel the
   sync search missed, and every tier's usable rate is set partly by a 13-bit preamble
   rather than by the leakage or the instrument.
6. The run-to-run variation in tier 1's error rate is the *instrument*, not the channel —
   and the artifact responsible was one the measurement chapter had recorded as harmless
   (§7). It is harmless to a mean difference and not to a per-symbol decision.
7. The transmitter is never the limit: it held its schedule at every rate tested (§8). The
   receivers' integration behaviour — RAPL's ~1 ms update for tier 1, the governor's
   control loop for tiers 2 and 3 — bounds what is reachable in principle, but acquisition
   binds first.

## 2. Threat model

The transmitter is an unprivileged process. It runs ordinary AVX loads over its own
memory and needs no capability a normal user process lacks — no root, no MSR access, no
elevated scheduling priority, no special mapping. In the intended scenario it is code
that has been induced to run inside a confidentiality boundary and has data it wants out.

The receiver comes in tiers, and the difference between them is the whole argument.

**Tier 1** reads `MSR_PKG_ENERGY_STATUS` through `/dev/cpu/N/msr`, which requires root.
A root receiver can already read the transmitter's memory directly, so tier 1 on its own
demonstrates a mechanism rather than an attack. Its purpose is to establish the ceiling —
what the channel carries when the receiver is as capable as it can be.

**Tier 2** reads `/sys/devices/system/cpu/cpuN/cpufreq/scaling_cur_freq`, which is
world-readable, and nothing else. No MSR, no `perf`, no root, no shared memory. This is
the receiver the security claim rests on, and §8.1 measures it. Its mechanism is one step
longer than tier 1's: the operand changes the victim's power draw, the extra power forces
a clock reduction *if something is limiting*, and the reduction shows up in the frequency
the kernel reports. That conditional is load-bearing — §8.2 is about what happens when
nothing is limiting.

Because tier 2 is dropped to an ordinary user in the experiment harness rather than
merely described as unprivileged, the claim is enforced rather than asserted: the runner
runs it under the invoking user's uid, and a tier-2 receiver left running as root would
demonstrate nothing.

**Tier 3** reads *nothing*. It runs its own fixed instruction stream and times it with
`rdtsc`, both of which any process can do anywhere. This matters because tier 2, for all
that it needs no privilege, still depends on an interface: a container that does not
mount sysfs, or a kernel built without cpufreq, takes it away. There is nothing here to
revoke. §8.3 measures it.

The two processes share no memory, no files and no IPC. They do share the invariant TSC,
which any process can read, and the receiver's recording is timestamped in it. This is a
real simplification and §8 states its cost: a receiver with an independent clock would
have to recover symbol *timing* as well as symbol phase. What the receiver is not given
is the message, the preamble position, or when transmission starts.

Both processes are pinned. The victim threads occupy P-cores 2, 4, 6, 8 and 10; the
receiver samples from core 0, which the kernel is booted to isolate; the transmitter's
own control thread sits on an E-core so that its busy-waiting does not compete with the
victims for P-core resources. Cross-core is therefore the only placement measured here —
§9 lists the placements the plan asks for and this draft does not cover.

## 3. The transmitter

The modulation primitive already existed. Victims are cloned with `CLONE_VM` and re-read
a shared `ctl->selector` between bursts of a few thousand instructions, which is how the
measurement driver interleaves conditions without tearing down threads; the same write
re-tunes every running victim within about 0.6 µs. The measurement chapter built that to
make thermal drift common-mode. Here it is the transmitter.

Two properties of the victim set decide whether this modulates the right thing.

**It must change the operand, not the amount of work.** A working-set victim fills a
buffer with a repeated word for each distinct selector value and caches two of them. With
exactly two values alternating, both stay resident and a symbol transition costs a
pointer swap — no refill, no allocation, no change in the instruction stream or the
number of bytes moved. Had the transmitter instead switched between *doing work* and
*not doing work*, the channel would be a duty-cycle channel and would say nothing about
operand leakage. The transmitter pre-touches both operands before the frame so that the
first use of each does not pay an `mmap`-populate inside the message.

**Symbol boundaries must not drift.** Chip *i* is emitted at an absolute deadline
`t0 + i·chip_tsc`, not by sleeping between chips, so a late chip does not push its
successors. Chips that miss their deadline anyway are counted and reported. This is a
self-check with teeth: a run with non-zero `late_chips` has outrun the mechanism, and its
error rate describes the transmitter rather than the channel. Across all 30 runs of the
sweep below, at symbol periods down to 2 ms, `late_chips` was zero everywhere — so
nothing in this chapter's rate ceiling comes from the transmitter's timing.

**Line code.** Bits are Manchester-encoded: one symbol is two chips, high-then-low for a
1 and low-then-high for a 0. A decision is therefore the difference between the two
halves of the *same* symbol. Any drift slower than one symbol — which is all of the
thermal drift that invalidated this project's pre-rework datasets — is common-mode to
both halves and cancels, and the receiver needs no baseline to track and no threshold to
calibrate. This is the same argument the measurement chapter makes for interleaved
blocks, applied at the symbol timescale, and it is why the covert channel needs no
equivalent of the drift gate. An NRZ mode exists for comparison and is not used below.

**Framing.** A frame is a 13-bit Barker preamble followed by the payload, and the frame
is repeated. The Barker code is chosen for its autocorrelation: sidelobes bounded at 1
against a peak of 13, which is what lets the receiver find the frame by sliding a
correlation along a noisy trace instead of being told where to look.

## 4. The receiver and the decoder

The receiver (`src/covert/rx_rapl.c`) is a separate binary and a separate process. It
records a power trace and nothing else: it is not told the symbol period, the line code,
the preamble or the payload, and it has no way to ask. Each row it writes covers the
interval `(tsc − dtsc, tsc]` with the energy actually accumulated over it.

It samples with the same edge-triggered code the measurement chapter validated. That was
worth some refactoring — the sampling loop was lifted out of the driver into
`util/sampler.c` so that both use one implementation — because the alternative was a
second copy of the instrument with its own noise floor to characterise. The extraction
was checked by measurement rather than assumed: the cross-session anchor from the
previous chapter reads +1.877 W (SD 0.120 over three repeats) against +1.841, +1.904,
+1.943 and +2.054 W from four earlier sessions at identical settings, at 13.1 Δ pJ/byte
against 13.2 (`results/20260903-114606-phase0_refactor_check`, 18 of 18 gates passing).

Decoding (`analysis/covert.py`) has three parts.

**Windowed integration.** Symbol boundaries never line up with RAPL update instants, so
chip power cannot be read off samples directly. Treating power as constant within a row —
which is exactly the sampler's own assumption — the cumulative energy at row boundaries
plus linear interpolation inside them gives the energy in an arbitrary window exactly,
and the mean power in a chip is one subtraction.

**Sync.** The frame is found by sliding the preamble along the trace and taking the
normalised correlation, summed over all frames at a candidate offset — the frame length
is part of the protocol, so a receiver may legitimately add the correlation from every
preamble, which turns F weak peaks into one strong one. Each frame's phase is then
refined against its own preamble. **Sync is recovered, not supplied.** The transmitter
does log its true start time, but the decoder reads it only afterwards, to report how far
the recovered sync landed from the truth. In the runs where sync succeeded it lands
within a tenth of a chip.

**Demodulation.** For each bit, compare the two chip powers; higher-then-lower is a 1.
No threshold, no baseline.

Because a synthetic trace has a known answer and a real one does not,
`tests/test_covert_decode.py` exercises the framing against generated traces: exact
decoding on a clean channel, an A/A that must come out at chance, sync recovered to
within half a chip, the ASCII round trip, and the Q(d′/√2) relation of §6 in a regime
where errors actually occur.

## 5. The channel works

`experiments/phase2_tier1_rate.json` sweeps the symbol period from 2 ms to 32 ms on
`ws_l3_x8`, with 8 frames per run and 3 repeats
(`results/20260903-115606-phase2_tier1_rate`, 30 runs, `late_chips` zero throughout).
The transmitter is `ws_l3_x8` rather than the victim with the largest effect, on the
previous chapter's finding that detectability and Δ power are not the same ranking:
`ws_dram_x8` has the highest energy per byte in the whole victim set and needs 13–89
samples per decision against this one's 1–8.

Every figure below is the mean over three repeats, with the between-repeat SD beside it.
That is the reporting unit this project uses everywhere else, and it matters more here
than anywhere: at a given rate the three repeats of an identical configuration span far
more than any one of them admits, and §7 is about why.

| symbol | bit/s | BER (SD) | acq | BER given sync | **capacity** | vote BER | vote bit/s |
|---|---|---|---|---|---|---|---|
| 2 ms | 500.0 | 0.342 (0.264) | 1/3 | 0.251 | 36.8 | 0.333 | 62.5 |
| 3 ms | 333.3 | 0.048 (0.057) | 3/3 | 0.057 | **241.2** | 0.003 | 41.7 |
| 4 ms | 250.0 | 0.339 (0.286) | 1/3 | 0.151 | 19.1 | 0.340 | 31.2 |
| 6 ms | 166.7 | 0.060 (0.097) | 3/3 | 0.064 | 111.8 | **0** | 20.8 |
| 8 ms | 125.0 | 0.017 (0.022) | 3/3 | 0.020 | 109.6 | **0** | 15.6 |
| 12 ms | 83.3 | 0.011 (0.017) | 3/3 | 0.011 | 76.2 | **0** | 10.4 |
| 16 ms | 62.5 | 0.009 (0.009) | 3/3 | 0.010 | 58.1 | **0** | 7.8 |
| 24 ms | 41.7 | 0.016 (0.014) | 3/3 | 0.015 | 36.7 | **0** | 5.2 |
| 32 ms | 31.2 | 0.003 (0.005) | 3/3 | 0.002 | 30.4 | **0** | 3.9 |
| A/A, 8 ms | 125.0 | 0.500 (0.012) | 0/3 | 0.507 | 0.0 | 0.497 | 15.6 |

**The headline is the capacity, not a rate at which no errors happened.** A bit-error rate
is only half of an operating point; the quantity that combines the two, and the one the
published attacks report, is the capacity of the binary symmetric channel the decoder
sees — the raw rate times 1 − H(BER). It peaks at **241 bit/s, at a 3 ms symbol**, and
across the rates where all three repeats acquired sync it stays above 100 bit/s from 333
down to 125.

The two rates that break that pattern, 500 and 250 bit/s, are exactly the two where
acquisition failed in two repeats of three — so their capacities of 36.8 and 19.1 bit/s
are averages over a working channel and two runs that never found the frame, not
measurements of a channel that got worse. §6.1 takes that apart; it is the reason the
`acq` column is in the table at all.

Two things follow that the raw-BER view hides. The best operating point is *not* the
slowest: above 8 ms the channel is already clean enough that slowing further only costs
rate. And the fastest rate tested is not the most capable — 36.8 bit/s of capacity at
500 bit/s against 241 at 333 — even though 500 bit/s is the number the raw table makes
most tempting to quote.

**No rate in this sweep is error-free when the repeats are aggregated.** At 83 bit/s the
three repeats read 0.0303, 0.0020 and 0.0000; one of them was clean and the mean is 0.011.
The same is true at every rate: the lowest aggregated BER anywhere in the table is 0.003
at 31 bit/s. An earlier draft of this chapter quoted the best repeat at each rate and
reported the channel as error-free from 83 bit/s down. That was selection, not a result,
and it is corrected here — the honest claim is a capacity of 241 bit/s and a raw BER
around 1% at 83.

**What the majority vote does and what it costs.** Voting across the eight repeated frames
clears the channel to zero observed errors from 167 bit/s down. It is not free: eight
frames carry one payload between them, so the rate actually delivered is the raw rate
divided by eight. The `vote bit/s` column above is that rate, and at 125 bit/s raw it is
**15.6 bit/s**. Quoted honestly the vote is a repetition code of rate 1/8, and comparing
its output against the raw rate — as the earlier draft did — charges nothing for the
repetition. Against capacity it is also a poor code: 15.6 bit/s delivered where the
uncoded channel at the same symbol period has 110 bit/s of capacity.

**A note on how ties are resolved, because it moved published numbers.** Both frame counts
used here are even, so a vote can split exactly — four frames saying 1 and four saying 0.
The decoder used to compare the mean against 0.5 with a strict inequality, which sent
every such split to 0. That is a systematic bias toward zero bits, applied to precisely
the bits the vote was least certain about, and on a balanced payload it decides half of
them wrongly by construction. Ties are now broken on the summed decision statistic across
frames instead: the frames that voted 1 did so by some margin and the frames that voted 0
by some other, and the larger total is the better guess. The change is small at eight
frames (341 tied bits in 5760 across the tier-1 sweep) and large at four (157 in 408 for
tier 2), and it moves individual figures in both directions — tier 1 at 167 bit/s goes to
zero observed errors, tier 2 at 3.9 bit/s worsens from 0.250 to 0.375. Neither movement is
resolvable: at 3.9 bit/s the vote is scored over 24 distinct bits, where the theoretical
value under either rule is 0.14 and both readings sit inside the noise. Every headline is
unchanged, since the rates that matter have no ties at all. What the change buys is that
the number no longer depends on which way an arbitrary inequality happened to point.

The tie count is now reported per run, and it is a diagnostic in its own right: frames only
disagree this often when there is nothing for them to agree on. At 2 bit/s, where tiers 2
and 3 both decode without errors, not one bit is tied.

**What "zero observed errors" is and is not.** It bounds the error rate only as well as
the bit count allows. At 125 bit/s the vote is scored over 256 distinct payload bits per
run, 768 across the session; by the rule of three, zero errors in *n* bits bounds the rate
at 3/*n* with 95% confidence, so **3.9 × 10⁻³**. And even that is optimistic: each payload
bit is transmitted eight times, so the 6144 transmissions are not 6144 independent truths
(§6.1). Certifying 10⁻⁶ would need millions of distinct bits, which at these rates is
hours of continuous transmission. That is a limitation of the experiment's length, not of
the channel, and the throughput-at-BER-below-10⁻³ figure the plan asks for cannot honestly
be quoted from runs this short.

**The negative control.** An A/A transmission — the same operand in both states, so the
selector writes still happen but carry nothing — decodes at BER 0.500 (SD 0.012) with a
per-chip separation of −0.01 W. Its recovered sync lands +302, −176 and −68 chips from
where a message would have been, scattered and sign-flipping, which is what a correlation
peak looks like when it is fitting noise; its winning correlation is 0.26–0.28 against
0.64–1.00 for every run that acquired. Demodulated on the true chip grid it still reads
0.507, so this is not a sync failure hiding a live channel — there is nothing there. The
decoder finds nothing when nothing is sent.
This is the same A/A discipline the rest of the project uses, in the form the channel
takes, and it is a gate rather than a remark: `analysis.covert` fails a session whose A/A
decodes better than BER 0.40.

## 6. What sets the error rate

A Manchester decision differences two chips. If per-chip power carries noise of standard
deviation σ and the two states are separated by Δ, the difference carries σ√2 and the
error rate should be Q(d′/√2) with d′ = Δ/σ. Both quantities are measurable per run.

**That reasoning contains an assumption worth making explicit, because it fails later in
this chapter.** The difference carries σ√2 only if the noise on the two chips is
independent. What the decision really depends on is the spread of the difference itself,
so the general statistic is

  d′_paired = |mean(P₀ − P₁)| / sd(P₀ − P₁)

taken over symbols, with the error rate Q(d′_paired). When chip noise is white this is
just d′/√2 and the two forms agree — which is why the simpler one fits everything in this
section. When the noise is dominated by drift slower than a symbol, differencing cancels
it, sd(P₀ − P₁) falls far below σ√2, and the marginal form understates the channel
badly. Tier 2 in §8.1 is exactly that case: it decodes at BER 0.09 while its marginal d′
reads 0.08, a value the marginal model would call chance. Both are reported, and it is
the paired one to compare against BER.

They are measured on the *true* chip grid rather than the recovered one, which matters:
a run whose sync failed has every window misaligned, and measuring there would collapse Δ
to zero and report a dead channel where there is a live one the receiver merely failed to
find — precisely the distinction these numbers exist to draw. Like the sync-error column
they read ground truth, so they are diagnostics rather than anything a receiver could
compute.

The prediction holds, provided it is compared against the right error rate. Because these
statistics are computed on the true grid, the BER they predict is the BER of
*demodulation* — the error rate the receiver would have achieved had it found the frame.
That is the `BER given sync` column of §5, and it is reported per run by the decoder as
`BER|snc`. Against it, BER tracks Q(d′/√2) with a **log-log correlation of +0.816** across
the 20 runs where the prediction is numerically resolvable, at a **median BER/Q of 1.15**.
Representative rows:

| run | d′ | BER given sync | Q(d′/√2) |
|---|---|---|---|
| `sym_02ms_r1` | 0.33 | 0.321 | 0.409 |
| `sym_04ms_r0` | 0.69 | 0.353 | 0.313 |
| `sym_06ms_r0` | 1.51 | 0.182 | 0.143 |
| `sym_08ms_r0` | 2.52 | 0.051 | 0.038 |
| `sym_03ms_r1` | 2.77 | 0.021 | 0.025 |
| `sym_04ms_r2` | 3.43 | 0.014 | 0.008 |
| `sym_16ms_r0` | 4.24 | 0.007 | 0.001 |

Above d′ ≈ 6 the Gaussian prediction underflows while the measured rate settles on a
**floor of 0–5 × 10⁻³** over ten runs. The model is therefore right about the regime where
errors are common and optimistic about the regime where they are rare, which is the usual
shape: the residual errors at high d′ are not Gaussian tail events but occasional outlier
samples, and nothing here identifies their source.

### 6.1 Acquisition is a second failure, and the dominant one at the fast end

Everything above is about demodulation. A decode can fail a second, entirely separate
way: the receiver never finds the frame. The two need to be told apart, because they have
different causes and different remedies, and because a failed acquisition produces a BER
of exactly chance no matter how good the channel was.

The decoder reports both. `BER` is the decode at the sync the receiver recovered for
itself, which is what a real attacker gets. `BER|snc` is the same decode on the true chip
grid — a diagnostic, like d′ and the sync-error column, since it reads ground truth. Where
the two agree, the limit is noise. Where they part company, the limit is acquisition.

In this sweep they part company decisively:

| run | overshoots | free-running BER | BER given sync | recovered sync |
|---|---|---|---|---|
| `sym_04ms_r1` | 0.29% (settled) | 0.519 | **0.087** | +236 chips |
| `sym_04ms_r0` | 4.22% | 0.489 | 0.353 | +132 chips |
| `sym_02ms_r1` | 4.14% | 0.496 | 0.321 | +582 chips |
| `sym_02ms_r0` | 1.43% | 0.492 | 0.367 | −714 chips |

`sym_04ms_r1` is the clearest case in the sweep. Its sampler was settled, its paired d′ of
1.16 predicts a BER of 0.12, and on the true grid it decodes at 0.087. The channel carried
the bits; the receiver could not find them. Reporting that run at BER 0.519 describes the
sync search, not the channel.

Across all 30 runs the split is binary, with nothing in between: a run's recovered sync
lands either within a chip of the truth (23 runs, BER 0.000–0.173) or hundreds of chips
away (7 runs, BER 0.489–0.519). There is no intermediate regime. So the aggregated BERs at
2 ms and 4 ms in §5 are not "the channel at that rate" — they are a two-thirds chance of
finding the frame at all, mixed with a working decode.

**A receiver can tell whether it acquired.** This matters for the threat model: an attacker
who cannot distinguish a good frame from a noise peak has to emit garbage and hope. The
obvious statistic does not work — the winning correlation over the best sidelobe reads
1.00–1.19 on failed acquisitions and 1.04–1.43 on successful ones, completely overlapping,
because with ~10⁵ candidate offsets the largest noise peak sits just under the largest peak
of any kind. The *absolute* normalised correlation does work: 0.26–0.71 when acquisition
failed, 0.64–1.00 when it succeeded. A threshold at 0.72 accepts no failed run and rejects
two successful ones. An attacker can therefore discard the frames it did not acquire, at a
cost of a few percent of the good ones — and the A/A control sits at 0.26–0.28, far below
anything the threshold would accept.

**A better correlator was tried, and it is not better.** The demodulator decides a bit by
differencing the two chips of a Manchester symbol, and the paired d′ that produces runs
5–30× the marginal one on tier 2 (§8.1). The acquisition correlator appeared not to be
getting that rejection, so it was rebuilt to difference each symbol before correlating and
re-run over both sweeps (`results/20260903-115606-phase2_tier1_rate`,
`results/20260903-143109-phase2_tier2_covert`). It buys one run and costs another. On
tier 1 `sym_04ms` goes from 1/3 to 2/3 acquired — `sym_04ms_r1`, the run above, finally
lands on the frame and decodes at its own oracle value of 0.087 — lifting that row from
BER 0.339 to 0.197 and its capacity from 78 to 125 bit/s. On tier 2 the 2 bit/s row falls
from 3/3 acquired to 1/3, the recovered sync moving from +0.1/+0.2 chips to +0.6 and past
the half-chip criterion, although the voted BER stays at zero. Every other tier-1 rate
pays a little: 0.048 → 0.060 at 3 ms, 0.060 → 0.066 at 6 ms, 0.016 → 0.022 at 24 ms.

The reason it cannot do better is that the correlator was never missing that rejection. A
Manchester chip pattern is pair-antisymmetric, so correlating against it *is* a
within-symbol difference: drift constant across a symbol already cancels in the numerator.
On a clean tier-1 trace the two numerators correlate at 0.93, and they differ at all only
because the 13-bit Barker preamble is unbalanced — nine ones to four zeros — which gives
the differenced pattern a DC term the raw one does not have. What differencing really
changes is the *denominator*: the score is normalised by the energy of 13 differences
rather than of all 26 chips, so every peak rises, by a median factor of 1.33. The noise
peaks rise with the signal peaks, and that is what disqualifies it. The A/A controls go
from 0.26–0.28 to 0.35–0.38 on tier 1 and from 0.20–0.26 to 0.38–0.55 on tier 2, where
failed runs then reach 0.80 against 0.79 for the single acquired one: the statistic in the
paragraph above stops separating. A correlator whose floor on a transmission carrying
nothing nearly doubles is a worse instrument than one that finds an extra frame, so `raw`
remains the decoder's default and `--sync-mode diff` is kept only as an option. The lever
acquisition needs is a longer preamble, not a different normalisation of this one.

The practical value of separating the two is that it says which knob to turn. A low Δ is a
weak channel; a high σ is a poor measurement; a low correlation peak is a preamble that is
too short for the σ it has to survive. The next section is about a case where the first two
look identical in the error rate and are not, and §8 about what the third implies for the
rate ceiling.

## 7. The run-to-run spread is the instrument, not the channel

Read naively, the sweep looks like it has a rate limit with a strange shape: 4 ms failed
in two repeats of three, while 3 ms — a *shorter* symbol — worked in all three. A rate
ceiling that is not monotone in rate is not a rate ceiling.

It is not one. The per-chip numbers show Δ is essentially identical across repeats at a
given rate while σ is not:

| 4 ms repeat | Δ | σ | d′ | free BER | BER given sync |
|---|---|---|---|---|---|
| `sym_04ms_r0` | 1.063 W | 1.544 W | 0.69 | 0.489 | 0.353 |
| `sym_04ms_r1` | 1.124 W | 0.687 W | 1.64 | 0.519 | **0.087** |
| `sym_04ms_r2` | 1.147 W | 0.335 W | 3.43 | 0.008 | 0.014 |

The signal is the same in all three. The noise varies more than fourfold. So the question
is what moves σ between runs of an identical experiment — and, given §6.1, what σ does
once it has moved.

**An earlier draft of this section got the last link wrong**, and the correction sharpens
the finding rather than weakening it. It claimed that forcing sync to the true offset left
the bad runs failing, so the problem was not acquisition. The `BER given sync` column
above says otherwise: at 4 ms the two bad repeats decode at 0.353 and 0.087 on the true
grid against 0.489 and 0.519 free-running, and the second of those is a working channel
whose frame was never found. What σ destroys first is not the per-symbol decision — it is
the preamble correlation. The chain is **overshoots → σ → failed acquisition → BER at
chance**, with the per-symbol decision still working underneath.

That is why the shape looked non-monotone. Acquisition is close to a threshold at these
rates: 3 ms acquires in 3 repeats of 3, 4 ms in 1 of 3, 2 ms in 1 of 3. Which side of the
threshold a run lands on is decided by its sampler regime, not by its symbol period, and a
rate whose repeats happened to be clean beats a slower rate whose repeats were not.

**It is the sampler's overshoot regime**, and this is the part that revises an earlier
conclusion. An overshoot is a RAPL edge observed more than 1.5 update periods late. The
measurement chapter investigated them, found them bimodal — a run sits at either ~0.1% or
~4% of edges and never between, so the sampler phase-locks into one regime and stays
there — and closed the question: within a run they are balanced across conditions, mean
`dtsc`/period is 1.00 for both, so they are common-mode and cannot bias a mean
difference. **That reasoning is correct and every Phase 0 and Phase 1 result stands on
it.** What does not carry over is the conclusion that they therefore cost only time
resolution. A mean over tens of thousands of samples averages them away. A per-symbol
decision has nothing to average: one overshoot is a single RAPL sample spanning two
chips, smearing them together, and those bits are simply lost. Across the 30 runs the
overshoot rate correlates with per-chip noise at **r = +0.63**.

There is a loose end here that Phase 0 should close rather than this chapter. The
sampler's period estimate is an EWMA over every observed interval *including* the
overshoots it has just flagged, so a run at overshoot fraction *p* estimates the period as
(1 + *p*)·T — and that estimate sets the guard it idles for before polling, which is
itself what decides how often it overshoots. That is a feedback loop with the right shape
to produce the observed bistability, and it is testable on data already committed.

The lesson is more general than the artifact. A validity gate is only ever a gate against
a specific inferential use, and "harmless" is a claim about that use rather than about
the data. The same trace supports one conclusion and not the other.

`analysis.covert` therefore gates on the overshoot rate at 1% of edges, which separates
the two regimes rather than cutting through a distribution. It reports settled-only and
unfiltered numbers side by side. Restricting to settled runs is defensible — the overshoot
rate is computed by the receiver from its own timing and knows nothing about the decode,
which makes it an instrument-quality criterion of the same kind as the zero-tick gate —
but a filtered number quoted on its own is how selection bias gets in, so both are always
printed. The gate flags 7 of the 30 runs.

**The gate is necessary and not sufficient.** `sym_04ms_r1` passes it at 0.29% overshoots
and still carries 0.69 W of per-chip noise against 0.34 W for the clean repeat at the same
rate — enough to lose the frame, though not enough to lose the bits once the frame is
found. Overshoots are the dominant cause of the spread, not the only one, and the second
source is unidentified.

**An open problem.** The bad regime becomes rarer over a session: 1.66%, 0.89% and 0.09%
of edges by repeat index, so the first repeat is reliably the noisiest and the last
reliably the cleanest. This is a session-level warm-up distinct from the per-run transient
that `--warmup-blocks` was built for, it survives the per-run cooldown, and nothing here
explains it — though the period-estimate feedback above is a candidate for the bistability
if not for the session trend.

Its practical consequence is that the aggregated BERs at 2 ms and 4 ms in §5 average a
working decode against two failed acquisitions, so they describe an *acquisition
probability* more than a channel. The three-repeat design cannot separate those, and the
sweep is being re-run at four repeats for that reason. The rates from 8 ms down, where all
three repeats acquire, do not have this problem and are quotable as they stand.

## 8. What limits the rate

Not the transmitter: `late_chips` is zero at every rate tested, so symbol boundaries were
placed as intended down to a 1 ms chip.

The limit is the receiver's integration window. RAPL updates about once per millisecond,
and each sample reports energy accumulated over that whole interval, so the measurement is
a boxcar filter roughly one chip wide at the fast end. Shortening the symbol does not
reduce the *power* difference the victim produces, but it does reduce the difference that
survives integration, and the measured Δ falls accordingly:

| symbol | chip | Δ |
|---|---|---|
| 8 ms | 4 ms | 1.54 W |
| 4 ms | 2 ms | 1.13 W |
| 2 ms | 1 ms | 0.64 W |

At a 1 ms chip the chip and the sampling window are the same length, every sample
straddles a boundary, and roughly a third of the separation survives. This is a property
of the instrument rather than of the leakage, and it is why 500 bit/s rather than some
higher number is where the table stops.

**But it is not the binding constraint on the rates actually achieved.** §6.1 shows that at
2 ms and 4 ms the decode fails at acquisition while demodulation still works — 0.087 on the
true grid for a run reported at 0.519. The integration window sets how far the channel can
be pushed *in principle*; what stops it first, at these rates, is a 13-bit preamble trying
to survive a σ the same instrument inflated. That is the more tractable of the two limits:
integration is fixed by the hardware, while acquisition has obvious headroom —
a longer or coded sync word, coherent accumulation over more frames than the eight used
here, or a correlation template matched to the RAPL boxcar rather than the square chip
the decoder currently slides. None of those has been tried, so the honest statement is
that **tier 1's measured ceiling is an acquisition ceiling, and the integration ceiling
lies somewhere above it, unmeasured.**

Sub-chip alignment is worth a note in the same connection, because it costs nothing on
tier 1 and something real on tier 3. Over the 23 tier-1 runs that acquired, the recovered
sync sits a median 0.078 chips from the truth and the free-running BER matches the true-grid
BER (0.023 against 0.027). On tier 3 the median residual is 0.248 chips and the two part
company — 0.088 against 0.058, a factor of 1.5 given away to imperfect alignment within an
acquired frame. Finer refinement, or interpolating the correlation peak rather than taking
the best of a grid at ⅛-chip spacing, would recover it.

Two consequences worth carrying forward. A receiver willing to average over repeated
transmissions buys back error rate but not bandwidth. And the unprivileged tier is not
bound by this particular limit — `scaling_cur_freq` has entirely different bandwidth and
entirely different noise — so being the most privileged receiver does not automatically
make tier 1 the fastest. It does, as it turns out, make it the fastest by a wide margin,
but for a different reason.

### 8.1 The unprivileged receiver

`experiments/phase2_tier2_covert.json` runs the same transmitter against tier 2 under
Config-B (`results/20260903-143109-phase2_tier2_covert`, 6 conditions × 3 repeats, zero
late chips). The receiver holds no privilege: it polls `scaling_cur_freq` every 200 µs
and has no other input.

| symbol | bit/s | BER (SD) | acq | BER given sync | capacity | vote BER | vote bit/s |
|---|---|---|---|---|---|---|---|
| 32 ms | 31.2 | 0.516 (0.037) | 0/3 | 0.479 | 0.0 | 0.563 | 7.8 |
| 64 ms | 15.6 | 0.492 (0.059) | 0/3 | 0.513 | 0.0 | 0.469 | 3.9 |
| 128 ms | 7.8 | 0.500 (0.165) | 0/3 | **0.339** | 0.0 | 0.458 | 2.0 |
| 256 ms | 3.9 | 0.365 (0.118) | 0/3 | 0.313 | 0.2 | 0.375 | 1.0 |
| **512 ms** | **2.0** | **0.083 (0.018)** | **3/3** | 0.094 | **1.1** | **0** | 0.5 |
| A/A, 256 ms | 3.9 | 0.490 (0.018) | 0/3 | 0.500 | 0.0 | 0.583 | 1.0 |

**An unprivileged process recovers the message at 2 bit/s with no errors after a
four-frame majority vote**, and the control at the same load and rate sits at chance. Its
capacity is 1.1 bit/s; after the vote it delivers 0.5 bit/s. Tier 2 is therefore slower
than tier 1 by a factor of a few hundred on capacity — 1.1 bit/s against 241 — and that
gap, not the existence of the channel, is the honest headline. (An earlier draft put the
gap at "about 40" by comparing raw rates, one of which was a cherry-picked repeat.)

The rate ceiling here is not RAPL's integration window but the governor's control loop.
A 512 ms symbol gives each state a 256 ms chip to settle a clock decision that the
platform makes on its own schedule, and halving that is most of the way to destroying the
channel.

**But the cliff above 2 bit/s is partly an acquisition cliff, not purely a bandwidth
one.** The `BER given sync` column says that at 128 ms — a rate the free-running decode
reports as exactly chance — demodulation on the true grid reads **0.339**, well below
chance, and at 256 ms it reads 0.313. There is recoverable signal at 7.8 bit/s that this
receiver never finds. Acquisition succeeds in 0 of 3 repeats at every rate except 512 ms,
where it succeeds in all three. So tier 2's usable rate is set by how far its 13-bit
preamble survives a trace carrying hundreds of MHz of governor wander, and the remedies of
§8 — a longer sync word, more frames to accumulate over — apply here with more headroom
than they do on tier 1. This is stated as an unexploited margin rather than a result: no
run has been done with a longer preamble, so how much of that 0.339 is actually reachable
is unmeasured.

**Where the receiver looks matters, though the control there is weaker.** An attacker need
not know which cores the victim occupies, so the receiver watched two: its own core and one
of the victim's. Decoding the victim's core reads BER 0.229 at 3.9 bit/s and 0.297 at 7.8,
where its own core is at chance, so the channel appears to extend about one rate step
further when the attacker guesses right. Both readings are in the run's `summary.txt`; the
table above is the conservative one. Polling another core's `scaling_cur_freq` costs
0.35 µs and issues no inter-processor interrupt, so watching the victim does not perturb
it.

That claim is quoted with a caveat it did not previously carry. On the victim-core column
the A/A control pools to **0.4375**, with one repeat at 0.375, against 0.4896 on the
receiver's own core — and the earlier per-run form of the A/A gate failed it. Pooled it
passes at p = 0.13, so nothing here is established as broken; but a control drifting the
same way as the effect, over 24 distinct bits, has very little power to say so either. The
victim-core result needs a longer A/A before it is quoted as a finding rather than an
observation.

This is also the section where §6's caveat pays off. Tier 2's marginal per-chip d′ is
0.08 — a number the simple model calls a dead channel — while the paired statistic reads
1.07 and predicts BER 0.14 against the 0.09 observed. The frequency trace carries
hundreds of MHz of slow wander on top of a ~50 MHz signal, and Manchester differencing
removes it. The line code was chosen in §3 for exactly this reason, against thermal
drift; here it is doing the same work against governor drift, and without it there would
be no tier-2 channel to report.

### 8.2 The channel exists only while the part is throttling

Tier 2 needs something to be limiting. This machine reports PL1 = 200 W and PL2 = 80 W
against a four-thread victim drawing about 16 W at around 50 °C, so at Phase 1's standard
placement nothing limits, nothing throttles, and there is no frequency response to read.
Every tier-2 run above therefore uses **ten** victim threads across the P-cores rather
than four. That is a real precondition of the attack rather than a tuning detail: an
otherwise-idle machine does not carry this channel.

The precondition was established the hard way, and the record is worth keeping.
`experiments/phase2_tier2_feasibility.json` sweeps the thread count under Config-B and
measures the frequency difference between operands directly
(`results/20260903-134424-phase2_tier2_feasibility`). Against its own A/A control **no
load is distinguishable**: |t| ≤ 1.56 at every thread count, and the control itself reads
−48 MHz with the sign flipping across repeats. An interrupted first pass of that sweep
had produced a single run reading −804 MHz with a tight within-run confidence interval,
which looked like a strong effect and was not: that run's die climbed from 42 °C to 77 °C
while it was measured. It is the failure mode the measurement chapter documents — a
single run's interval is optimistic, worst for large effects, and never an error bar —
reproduced here at full size.

That leaves an apparent contradiction with §8.1, and it resolves rather than stands. The
feasibility sweep tested a *mean difference* over interleaved 0.1 s blocks; the channel
needs a 256 ms chip. The proxy was underpowered for the thing that turned out to work,
not evidence against it. Two lessons, both worth more than the measurement: a null on a
proxy is not a null on the mechanism, and the decisive experiment was the one that ran
the actual receiver rather than something correlated with it.

**The precondition is now checked per run, and three runs in the tier-2 sweep fail it.**
Ten loaded threads make the part throttle on average; they do not guarantee it did so
during any particular run, and a run in which it did not carries no channel whatever
symbol rate it was testing. The receiver's own trace answers this for free: the decoder
now reports the 5th-to-95th-percentile band of the level it recorded over the
transmission window, and warns when that band spans less than 10% of itself. Fifteen of
the eighteen runs read 2400–3900 MHz, a band of 14–40%. The other three sat at their
ceiling and never throttled:

| run | band | span |
|---|---|---|
| `sym_256ms_r0` | 3840–3900 MHz | 1.5% |
| `sym_032ms_r2` | 3820–3900 MHz | 2.0% |
| `sym_032ms_r1` | 3768–3900 MHz | 3.4% |

The gap between 3.4% and the next run at 14.3% is wide enough that this is a state the
machine is in or is not, rather than a continuum being cut arbitrarily.

This is worth more than tidiness, because it moves how two rows should be read. The
3.9 bit/s row aggregates to a BER of 0.365 from repeats of 0.500, 0.313 and 0.281 — and
the 0.500 is the un-throttled run. And **two of the three repeats at 31.2 bit/s never
throttled**, so that row's 0.516 is very largely a measurement of an idle-ish machine
rather than of the channel at 31.2 bit/s. Neither disturbs the headline, which is at
2 bit/s where all three repeats throttled normally; what it means is that the fast rows
are even less interpretable than §8.1 already says, and that a rate sweep on a
throttling-dependent channel needs this gate to be readable at all.

Recording also that the first reading of `sym_256ms_r0` was wrong, twice. Its per-chip
noise is 7.87 MHz against 200–700 for every other run, a hundredfold outlier that reads
like a parked or fixed-frequency CPU. It is not one — the raw trace takes 2064 distinct
values with a lag-1 autocorrelation of 0.993. The small per-chip figure is the opposite of
a fault: this run's frequency noise is *white*, so it averages down over a 256 ms chip,
where every other run's noise is governor wander slow enough to survive the integration.
A dispersion statistic cannot tell "nothing was moving" from "what was moving averaged
away"; a percentile band can. The second error was measuring that band over the whole
recording, which opens before the transmitter starts and closes after it stops. A watched
*victim* core idles at 400 MHz outside the transmission, so over the full trace every
watched core looks lively and the same run appears to throttle on cpu2 while not
throttling on cpu0 — an appealing but false explanation for why watching a victim core
decodes better. Restricted to the transmission itself, cpu2 reads 2.8% and agrees with
cpu0: the part was not throttling, full stop.

There is a second, independent reason that sweep could not have concluded anything, found
after the fact and worth recording because it generalises. Under Config-B with the part
free to throttle, its runs drift **29–48 W** within a condition — two orders of magnitude
more than a Config-A session — at a temporal imbalance of 0.093, which passes the
interleaving gate by 7%. Imbalance times drift span is the bias the design admits, and for
these runs that is 2.7–4.4 W against measured effects of 1.3–2.8 W: **every run in the
sweep has an effect smaller than its own admitted bias.** The gate passed it and should
not have. Config-B experiments need a far tighter interleaving requirement than Config-A
ones, because the same imbalance buys vastly more drift, and no gate in this project
currently encodes that.

### 8.3 The receiver that reads nothing

Tier 3 (`experiments/phase2_tier3_covert.json`,
`results/20260903-194816-phase2_tier3_covert`, 5 conditions × 3 repeats) times a fixed
chain of integer operations against the invariant TSC. The TSC ticks at a constant rate
whatever the core clock does, so a workload that never touches memory takes a TSC
duration inversely proportional to the core frequency: when the transmitter makes the
part throttle, the receiver observes *itself* running slower. It never looks at the
victim at all.

Put beside tier 2 at matched rates, three repeats each, capacity in bit/s:

| bit/s | tier 2 (reads a file) | | | tier 3 (reads nothing) | | |
|---|---|---|---|---|---|---|
| | BER | cap | vote | BER | cap | vote |
| 7.8 | 0.500 | 0.0 | 0.458 | — | — | — |
| 3.9 | 0.365 | 0.2 | 0.375 | **0.237** | **0.8** | **0.198** |
| 2.0 | 0.083 | 1.1 | 0.000 | 0.109 | 1.0 | 0.000 |
| 1.0 | — | — | — | **0.000** | **1.0** | **0.000** |

**The receiver that reads nothing performs as well as the one that reads a file**, and at
3.9 bit/s rather better — 0.8 bit/s of capacity against 0.2. Both A/A controls are dead at
chance with real power behind them — pooled BER 0.500 over 384 transmissions of 96 distinct
bits (p = 0.52) and 0.516 over 192 transmissions of 48 (p = 0.69) — against per-chip
separations of 0.4–4 kTSC on 260–430 kTSC of noise.

Tier 3 is also the one tier whose acquisition is not the binding constraint: it acquires in
3 of 3 repeats at 2.0 and 1.0 bit/s, and 2 of 3 at 3.9. What it gives away instead is
sub-chip alignment — a median residual of 0.248 chips, costing a factor of 1.5 in BER
against the true grid (§8) — which is recoverable and has not been recovered.

The whole cost of the ladder, then, is in the step from root to unprivileged: from
241 bit/s of capacity to about 1. Giving up the last readable interface costs nothing
beyond that.

That has a consequence for the mitigations chapter worth stating here, because this
chapter is where the evidence for it lives. Restricting `scaling_cur_freq` is a real
proposed defence against frequency side channels — it is the obvious response to tier 2 —
and on this evidence it buys nothing, because tier 3 never reads it. Any mitigation
aimed at the *interface* rather than at the throttling itself is defeated by a receiver
that only needs a clock.

Tier 3's separation grows with symbol period (15–32 kTSC at 256 ms, 35–62 at 512 ms,
66–75 at 1000 ms). That is the throttle response needing time to settle, the same
governor-timescale limit tier 2 runs into, and not a property of the receiver.

### 8.4 A control that could not have caught anything

Tier 3's first pass (`results/20260903-185812-phase2_tier3_covert`) decoded 1 bit/s at
BER 0.000 in all three repeats and then failed its A/A gate at BER 0.219. The failure was
real and the cause was in the experiment, not the channel.

Payloads were drawn as i.i.d. bits. One repeat drew **seven ones in eight**, and those
eight bits were repeated across four frames — so the run scored 32 comparisons against
only 8 independent truths, badly skewed. A decoder handed a featureless trace still emits
bits, and if those happen to lean the way the payload leans, the bit-error rate flatters
it. Nothing was recovered; the coincidence was scored as though something had been.

Payloads are now balanced by construction: exactly half ones, shuffled. The property that
buys is the whole point of the control — against a balanced payload a decode that is
biased but *independent of the message* scores BER 0.5 exactly, whatever its bias, so an
A/A can only beat chance by actually recovering information. Every number in §8.3 is from
the re-run under that generator.

Two smaller corrections came with it. The decoder now reports the ones-fraction of the
transmitted and decoded payloads and warns when a payload is skewed enough to matter. And
the A/A gate judges the pooled figure across repeats rather than each run: a single A/A
here carries as few as 32 bits, where the standard deviation of BER is 0.09 and a
three-sigma excursion is a one-in-140 event, so a per-run gate fires on noise about as
often as on a fault. A single repeat straying is now recorded as a warning rather than
either failing the experiment or disappearing.

The episode is worth keeping in the chapter rather than quietly fixing, because the
failure mode generalises: a negative control is only as good as the null it is compared
against, and an unbalanced payload silently weakens the null.

## 9. What this chapter does not yet cover

This is a partial draft, and the gaps are not incidental.

**The tier comparison is not controlled, in five dimensions rather than one.** The
configuration is the one usually named: tier 1 needs Config-A to isolate power leakage from
DVFS, tiers 2 and 3 need Config-B because Config-A removes the response they read, and a
fair comparison would run tier 1 under Config-B at a cost that is unmeasured. But the tiers
also differ in transmitter thread count (4 against 10), core layout (stride 2, one thread
per physical core, against stride 1 and SMT pairs), vote depth (8 frames against 4), and
payload length (256 distinct bits against 8). So "241 bit/s against 1" in §10 is a
comparison across five simultaneous changes, and the honest reading is that each tier was
measured where it works rather than against the others. One matched sweep — same threads,
same layout, same frames, same payload length, all three tiers under Config-B — would fix
all five at once and has not been run. The tier-2 against tier-3 comparison in §8.3 does
*not* have this problem: same configuration, same load, same transmitter.

**The transmitter is not the victim Phase 1 characterised.** §5 justifies choosing
`ws_l3_x8` on the previous chapter's detectability ranking, which was measured at four
threads on five distinct physical cores, where the aggregate working set is 16 MB and fits
the 24 MB L3. Tiers 2 and 3 run ten threads on cores 2–11 at stride 1: two threads per
physical core, and 40 MB of aggregate working set against a 24 MB L3.

The achieved bandwidth, pooled across every run in `results/` that used this victim,
confirms it is not the same operating point:

| threads | layout | active set | GB/s | n |
|---|---|---|---|---|
| 4 | stride 2, one per core | 16 MB | **145.8** (SD 8.3) | 130 |
| 6 | stride 1, SMT paired | 24 MB | 99.8 (SD 30.6) | 3 |
| 8 | stride 1, SMT paired | 32 MB | 107.2 (SD 2.1) | 3 |
| 10 | stride 1, SMT paired | 40 MB | **91.9** (SD 5.0) | 7 |
| 18 | stride 1, SMT paired | 72 MB | 65.4 (SD 4.7) | 4 |

Phase 1 measured `ws_l3_x8` at 148 GB/s and `ws_dram_x8` at 41. The tier-2 and tier-3
transmitter runs at 92 — between the two, and monotonically falling as the aggregate set
grows past the L3. So it is a partly DRAM-resident victim wearing an L3-resident victim's
name, and the detectability ranking that selected it does not apply there. The channel
works regardless, but the stated reason for the choice does not survive the arithmetic.
Re-running the victim selection at ten threads is one sweep, and it should also settle
whether `ws_l2_x8` — which would still fit L3 at that thread count — is the better
transmitter.

**The receiver is given an isolated core.** Every run pins the receiver to CPU 0, which the
kernel is booted to isolate with `isolcpus=0`. For tiers 2 and 3 — whose whole claim is
that they need nothing special — that is a privilege granted to the attacker, and it is
precisely the privilege tier 3 is most sensitive to, since its signal *is* the timing of
its own workload. No run has been done with the receiver unpinned or sharing a core with
ordinary load, so the cost is unknown. If the channel survives it, that is a strictly
stronger result than the one reported here.

**The transmitter is not covert.** It holds ten of twelve P-core hyperthreads at 100% for
the whole message; at 2 bit/s with four frames of 21 bits that is roughly three minutes of
full-machine load for eight bits of payload, which any monitoring would see. §8.2 shows the
channel needs that load to exist at all, so this is a property of the attack and not of
the implementation — but the minimum load that still carries it has not been measured, and
the thread-count and duty-cycle sweep that would turn this weakness into a stated bound is
exactly what Phase 4 will need in order to argue that throttling, not the interface, is
the thing to mitigate.

**Tier 3's sweep found its ceiling but not its floor.** It was measured at 3.9, 2.0 and
1.0 bit/s and decodes at all three, so nothing here says how slow it would have to run to
be error-free at 2 bit/s, or whether it keeps improving below 1. The range was inherited
from tier 2's results rather than chosen for tier 3.

**Only one placement is measured.** The plan asks for cross-core, cross-SMT-sibling,
cross-P/E-core and cross-container. This chapter has cross-core between P-cores only. The
container case matters most for tier 2, since `scaling_cur_freq` may or may not be
visible inside one, and that is a one-command experiment nobody has run.

**Tier 2's rate curve is coarse, and its bit counts are small enough to matter.** Five
symbol periods a factor of two apart locate the cliff between 3.9 and 7.8 bit/s but do not
resolve its shape. More seriously, the headline row carries **8 distinct payload bits per
run** — 24 across three repeats, transmitted 96 times between them. The p-value the decoder
reports treats those 96 transmissions as independent trials, which they are not: a decoder
that is biased but independent of the message errs the same way on every repeat of a bit,
so the effective sample size is 24 and not 96. Against a message-permutation null, three
runs of eight balanced bits cannot produce evidence stronger than about 10⁻⁶ however clean
the decode, so the quoted 2 × 10⁻¹⁸ is an artifact of the counting rather than a claim the
design can support. The channel is real — the effect is large and the control is dead —
but the *strength* of that evidence is overstated, and the fix is to spend the run time on
more distinct bits rather than more repetitions of eight. This applies to every A/A gate in
the chapter for the same reason: they pass with far less power than their bit counts
suggest.

**No comparison to the literature.** Liu et al. (CCS'22) and Hertzbleed are the two
obvious points of reference and neither is engaged. Capacity, which §5 now reports and
which is the metric both of those use, is what makes the comparison possible; making it
is still outstanding.

**Error correction is a majority vote.** The plan mentions repetition or Hamming coding;
the vote reported in §5 is the former in its crudest form.

**The shared timebase is a simplification.** Both processes read the same TSC, so the
decoder recovers phase but never has to recover symbol *rate*. A receiver with an
independent clock would need to, and nothing here measures how much that costs.

**One machine, one microarchitecture**, as everywhere else in this thesis.

## 10. What this chapter establishes so far

- Operand-level power leakage supports a real covert channel between processes sharing no
  memory, not merely a correlation visible to an instrument that knows where to look. The
  receiver recovers frame position from the signal itself.
- Under a root receiver the channel carries **241 bit/s of capacity**, at a 3 ms symbol and
  a mean BER of 0.048 over three repeats; an eight-frame majority vote clears it to zero
  observed errors from 167 bit/s down, delivering 20.8 bit/s. An order of magnitude above
  this project's stated expectation of low tens of bits/s. No rate is error-free when the
  repeats are aggregated: the raw BER at 83 bit/s is 0.011, not zero.
- **A receiver with no privilege at all recovers the message.** Reading only
  world-readable `scaling_cur_freq`, it decodes at 2 bit/s with BER 0.083 and no errors
  after a majority vote, against a control at chance. This is the security claim: the
  leak is reachable by an ordinary process, not only by one that could already read the
  victim's memory.
- **A receiver that reads nothing at all does just as well.** Timing only its own
  workload — no file, no interface, nothing a container can decline to mount — it decodes
  without observed errors at 1 bit/s in all three repeats (which bounds its BER at
  6 × 10⁻² over the 48 distinct bits sent, no lower), matches the file-reading receiver at
  2, and beats it at 3.9. The entire cost of the ladder is the step from root to
  unprivileged; the last interface is free to give up. Restricting `scaling_cur_freq`, the
  obvious defence against tier 2, therefore buys nothing.
- The unprivileged channel costs more than two orders of magnitude of capacity — 241 bit/s
  to about 1 — and exists only while the part is throttling. Four victim threads on this
  machine do not make it throttle; ten do. An idle machine does not carry this channel.
- **A decode fails two ways, and telling them apart changes what the rate ceiling means.**
  Demodulated on the true chip grid, runs reported at chance turn out to carry working
  channels their sync search missed: 0.087 for a tier-1 run reported at 0.519, and 0.339
  for a tier-2 rate reported at exactly 0.500. Every tier's usable rate is bounded partly
  by a 13-bit preamble rather than by the leakage or the instrument, and a receiver can
  tell for itself which happened — the absolute correlation peak separates acquired from
  failed runs where the peak-to-sidelobe ratio does not.
- The error rate of a demodulated frame is predicted by the separation of the within-symbol
  difference the decision uses, through Q(d′_paired). The marginal per-chip form is a
  white-noise special case: it fits tier 1's true-grid BER with a log-log correlation of
  +0.816 and a median ratio of 1.15, and it calls tier 2 dead at d′ 0.08 on a run decoding
  at BER 0.09. Tier 1 bottoms out on an unexplained error floor of a few times 10⁻³.
- Manchester coding is doing more work than a line code usually does. It was chosen to
  reject thermal drift; it turns out to be what makes tier 2 exist at all, by cancelling
  a governor wander hundreds of MHz deep on a ~50 MHz signal.
- The transmitter is not the limit at any rate on any tier. Tier 1's *in-principle* limit
  is the ~1 ms RAPL integration window, which erodes the usable separation from 1.54 W to
  0.64 W as the chip shrinks from 4 ms to 1 ms; for tiers 2 and 3 it is the governor's own
  control loop, which is why tier 3's separation grows from 15 to 75 kTSC as the symbol
  lengthens from 256 ms to 1 s. Acquisition binds before either of those, so both ceilings
  remain unmeasured from above.
- A measurement artifact that the previous chapter correctly established as harmless to a
  mean difference is *not* harmless to a per-symbol decision, and accounts for most of the
  run-to-run variation in tier 1's error rate. Validity gates are relative to an
  inferential use, and so are null results: §8.2's mean-difference proxy found nothing on
  a channel that works.
- A negative control is only as good as the null it is compared against. An unbalanced
  payload silently weakened one, and a control that could not have caught anything failed
  anyway on a coincidence (§8.4). Payloads are balanced by construction now, which makes
  the expected error rate exactly 0.5 for any decode independent of the message — though
  balance fixes the expectation and not the variance, and §9 records that the effective
  sample size behind several p-values here is the number of *distinct* payload bits rather
  than the number transmitted.
- A single repeat is not a result, and this chapter had to learn that twice. Phase 1
  established it for effect sizes; an earlier draft of §5 nonetheless reported the best
  repeat at each rate and called the channel error-free at 83 bit/s. Aggregated, it is not.
  The rule holds for bit-error rates exactly as it does for watts.

What remains is the placement matrix, a controlled comparison across the tiers, and a
comparison against the published attacks — §9. The ladder is measured end to end, and what
it says is that **privilege buys rate, not access**: root reads the channel a couple of
hundred times faster, and a process with no privilege and no interface at all still reads
it.
