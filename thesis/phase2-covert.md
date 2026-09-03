# A Covert Channel Out of Operand Power

*Draft — Phase 2 chapter, **partial**. Tiers 1 (RAPL, privileged) and 2
(`scaling_cur_freq`, unprivileged) are measured; tier 3 and the placement matrix are
not, and §9 says what is missing. Every number here is reproducible from `results/`
plus the run manifests, cited inline as a run directory. Tier-1 measurements are
Config-A (turbo disabled, frequency pinned); tier-2 measurements are necessarily
Config-B, and §9 explains why that makes them not directly comparable.*

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

1. The channel works. Under a privileged receiver it runs error-free at 83 bit/s and
   reaches 500 bit/s at a bit-error rate of 0.037 (§5). That is an order of magnitude
   above the expectation this project set out with, which was "low tens of bits/s".
2. **It also works with no privilege at all.** A receiver reading only world-readable
   `scaling_cur_freq` decodes at 2 bit/s with a bit-error rate of 0.083 and no errors
   after a majority vote (§8.1). That is the result the security claim rests on, and it
   costs two and a half orders of magnitude of rate.
3. The error rate is set by a single quantity — but not the one that first suggests
   itself. What predicts it is the separation of the *within-symbol difference* the
   decision actually uses, not the marginal separation of a chip (§6). The two agree only
   when chip noise is white, and tier 2 is precisely where they do not.
4. The run-to-run variation in tier 1's error rate is the *instrument*, not the channel —
   and the artifact responsible was one the measurement chapter had recorded as harmless
   (§7). It is harmless to a mean difference and not to a per-symbol decision.
5. The ceiling on each tier's rate is its receiver's integration behaviour — RAPL's ~1 ms
   update for tier 1, the governor's control loop for tier 2 — not any limit of the
   transmitter, which held its schedule at every rate tested (§8).

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

**Tier 3**, self-timing in the manner of Hertzbleed, is not built. See §9.

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
+1.943 and +2.054 W from four earlier sessions at identical settings, at 13.1 pJ/byte
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

Read off the repeat whose sampler stayed in the good regime (§7 explains why that
qualification is necessary, and reports the sweep both ways):

| symbol | bit/s | BER | after 8-frame vote |
|---|---|---|---|
| 2 ms | 500.0 | 0.037 | 0 |
| 3 ms | 333.3 | 0.012 | 0 |
| 4 ms | 250.0 | 0.008 | 0 |
| 6 ms | 166.7 | 0.005 | 0 |
| 8 ms | 125.0 | 0.002 | 0 |
| 12 ms | 83.3 | **0** | 0 |
| 16 ms | 62.5 | **0** | 0 |
| 24 ms | 41.7 | **0** | 0 |
| 32 ms | 31.2 | **0** | 0 |

The channel is error-free from 83 bit/s down over the bits transmitted, and carries
500 bit/s at a 3.7% error rate. Majority vote across the eight repeated frames — the
cheapest possible error correction, and a fair one to quote since the frames are already
there for sync — clears every rate in the table to zero errors.

**What "error-free" is and is not.** It means no bit errors were observed, over 2048
payload bits per run at 83 bit/s and 512 at 31–42 bit/s. It does not certify a low error
rate. By the rule of three, zero errors in *n* bits bounds the rate at 3/*n* with 95%
confidence: **5.9 × 10⁻³ for the 512-bit runs and 1.5 × 10⁻³ for the 2048-bit ones**.
Certifying 10⁻⁶ would need millions of bits, which at 83 bit/s is hours of continuous
transmission. This is a limitation of the experiment's length, not of the channel, and
the throughput-at-BER-below-10⁻³ figure the plan asks for cannot honestly be quoted from
runs this short.

**The negative control.** An A/A transmission — the same operand in both states, so the
selector writes still happen but carry nothing — decodes at BER 0.496 with a mean
preamble correlation of 0.30 and a per-chip separation of −0.01 W. Its recovered sync
lands 68, 176 and 564 chips from where a message would have been, scattered and
sign-flipping, which is what a correlation peak looks like when it is fitting noise. The
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

The prediction holds. Over the 27 non-control runs, taking the error rate with sync
constrained near the truth so that the comparison is about demodulation rather than
acquisition, BER tracks Q(d′/√2) with a **log-log correlation of +0.895** across the 17
runs where the prediction is numerically resolvable, at a **median BER/Q of 0.91**.
Representative rows:

| run | d′ | BER | Q(d′/√2) |
|---|---|---|---|
| `sym_02ms_r1` | 0.33 | 0.312 | 0.409 |
| `sym_04ms_r0` | 0.69 | 0.346 | 0.313 |
| `sym_06ms_r0` | 1.51 | 0.181 | 0.143 |
| `sym_08ms_r0` | 2.52 | 0.038 | 0.038 |
| `sym_03ms_r1` | 2.77 | 0.022 | 0.025 |
| `sym_04ms_r2` | 3.43 | 0.009 | 0.008 |
| `sym_16ms_r0` | 4.24 | 0.007 | 0.001 |

Above d′ ≈ 6 the Gaussian prediction underflows while the measured rate settles on a
**floor of 2–5 × 10⁻³**. The model is therefore right about the regime where errors are
common and optimistic about the regime where they are rare, which is the usual shape:
the residual errors at high d′ are not Gaussian tail events but occasional outlier
samples, and nothing here identifies their source.

The practical value of d′ is that it separates the two ways a run can fail. A low Δ is a
weak channel; a high σ is a poor measurement. The next section is entirely about a case
where those look identical in the error rate and are not.

## 7. The run-to-run spread is the instrument, not the channel

Read naively, the sweep looks like it has a rate limit with a strange shape: 4 ms failed
in two repeats of three, while 3 ms — a *shorter* symbol — worked in all three. A rate
ceiling that is not monotone in rate is not a rate ceiling.

It is not one. Two observations settle it. Forcing sync to the true offset leaves the bad
runs failing, so the problem is not acquisition. And the per-chip numbers show Δ is
essentially identical across repeats at a given rate while σ is not:

| 4 ms repeat | Δ | σ | d′ | BER |
|---|---|---|---|---|
| `sym_04ms_r0` | 1.063 W | 1.544 W | 0.69 | 0.498 |
| `sym_04ms_r1` | 1.124 W | 0.687 W | 1.64 | 0.519 |
| `sym_04ms_r2` | 1.147 W | 0.335 W | 3.43 | 0.008 |

The signal is the same in all three. The noise varies more than fourfold, and the error
rate follows it exactly as §6 predicts. So the question is what moves σ between runs of
an identical experiment.

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
rate. Overshoots are the dominant cause of the spread, not the only one, and the second
source is unidentified.

**An open problem.** The bad regime becomes rarer over a session: 1.66%, 0.89% and 0.09%
of edges by repeat index, so the first repeat is reliably the noisiest and the last
reliably the cleanest. This is a session-level warm-up distinct from the per-run transient
that `--warmup-blocks` was built for, it survives the per-run cooldown, and nothing here
explains it. Its practical consequence is that the fast end of the table in §5 currently
rests on a single usable repeat per rate, which is why that sweep needs re-running at four
repeats before its numbers are quoted as final. This is stated as a defect of the present
draft.

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
straddles a boundary, and roughly a third of the separation survives. This is the real
ceiling on tier 1, it is a property of the instrument rather than of the leakage, and it
is why 500 bit/s rather than some higher number is where the table stops.

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

| symbol | bit/s | BER | SD | after vote | p vs chance |
|---|---|---|---|---|---|
| 32 ms | 31.2 | 0.516 | 0.037 | 0.563 | 0.82 |
| 64 ms | 15.6 | 0.492 | 0.059 | 0.510 | 0.40 |
| 128 ms | 7.8 | 0.500 | 0.165 | 0.479 | 0.53 |
| 256 ms | 3.9 | 0.365 | 0.118 | 0.250 | 5 × 10⁻³ |
| **512 ms** | **2.0** | **0.083** | **0.018** | **0** | **2 × 10⁻¹⁸** |
| A/A, 256 ms | 3.9 | 0.490 | 0.018 | 0.542 | 0.46 |

**An unprivileged process recovers the message at 2 bit/s with no errors after a
four-frame majority vote**, and the control at the same load and rate sits at chance. The
channel falls off a cliff above that: usable at 3.9 bit/s, indistinguishable from chance
at 7.8 and beyond. Tier 2 is therefore slower than tier 1 by a factor of about 40 on raw
rate, and that gap — not the existence of the channel — is the honest headline.

The rate ceiling here is not RAPL's integration window but the governor's control loop.
A 512 ms symbol gives each state a 256 ms chip to settle a clock decision that the
platform makes on its own schedule, and halving that is enough to destroy the channel.

**Where the receiver looks matters.** An attacker need not know which cores the victim
occupies, so the receiver watched two: its own core and one of the victim's. Decoding the
victim's core is strictly better — BER 0.229 at 3.9 bit/s and 0.297 at 7.8, where its own
core is at chance — so the channel extends about one rate step further when the attacker
guesses right. Both readings are in the run's `summary.txt`; the table above is the
conservative one. Polling another core's `scaling_cur_freq` costs 0.35 µs and issues no
inter-processor interrupt, so watching the victim does not perturb it.

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

## 9. What this chapter does not yet cover

This is a partial draft, and the gaps are not incidental.

**Tier 3 does not exist yet.** It times its own fixed workload and infers the
transmitter's activity from frequency-induced dilation, in the manner of Hertzbleed. It
is the one receiver that needs no filesystem interface at all, so it is the tier a
container or a sandbox is least able to take away — which makes it the most interesting
of the three and the largest remaining gap. §8.2's precondition should apply to it in the
same form, since it observes the same throttling.

**The two tiers are not measured under the same configuration**, and cannot be. Tier 1
needs Config-A to isolate power leakage from DVFS; tier 2 needs Config-B because Config-A
removes the response it reads. So the 83 bit/s and the 2 bit/s in §10 are not a
controlled comparison of receivers — they are each tier measured where it works, on a
machine in two different states. A fair comparison would run both under Config-B, which
would cost tier 1 something unmeasured. That has not been done.

**Only one placement is measured.** The plan asks for cross-core, cross-SMT-sibling,
cross-P/E-core and cross-container. This chapter has cross-core between P-cores only. The
container case matters most for tier 2, since `scaling_cur_freq` may or may not be
visible inside one, and that is a one-command experiment nobody has run.

**Tier 2's rate curve is coarse.** Five symbol periods, a factor of two apart, locate the
cliff between 3.9 and 7.8 bit/s but do not resolve its shape. And the slowest rows carry
few bits — 32 payload bits per run at 512 ms — so BER 0.083 rests on 96 bits in total.
That is enough to establish the channel at p = 2 × 10⁻¹⁸ and nowhere near enough to
quote a low error rate precisely.

**No comparison to the literature.** Liu et al. (CCS'22) and Hertzbleed are the two
obvious points of reference and neither is engaged.

**No capacity figure worth quoting.** The decoder computes a binary-symmetric-channel
capacity per run, but with two tiers under two configurations and one placement it is a
number without a comparison.

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
- Under a root receiver the channel runs error-free at 83 bit/s over the bits transmitted,
  and carries 500 bit/s at BER 0.037. An order of magnitude above this project's stated
  expectation of low tens of bits/s.
- **A receiver with no privilege at all recovers the message.** Reading only
  world-readable `scaling_cur_freq`, it decodes at 2 bit/s with BER 0.083 and no errors
  after a majority vote, against a control at chance. This is the security claim: the
  leak is reachable by an ordinary process, not only by one that could already read the
  victim's memory.
- The unprivileged channel costs a factor of about 40 in rate and exists only while the
  part is throttling. Four victim threads on this machine do not make it throttle; ten
  do. An idle machine does not carry this channel.
- The error rate of both tiers is predicted by the separation of the within-symbol
  difference the decision uses, through Q(d′_paired). The marginal per-chip form is a
  white-noise special case: it fits tier 1 with a log-log correlation of +0.895 and a
  median ratio of 0.91, and it calls tier 2 dead at d′ 0.08 on a run decoding at BER
  0.09. Both tiers bottom out on an unexplained error floor of a few times 10⁻³.
- Manchester coding is doing more work than a line code usually does. It was chosen to
  reject thermal drift; it turns out to be what makes tier 2 exist at all, by cancelling
  a governor wander hundreds of MHz deep on a ~50 MHz signal.
- The transmitter is not the limit at any rate on either tier. Tier 1's limit is the
  ~1 ms RAPL integration window, which erodes the usable separation from 1.54 W to 0.64 W
  as the chip shrinks from 4 ms to 1 ms; tier 2's is the governor's own control loop.
- A measurement artifact that the previous chapter correctly established as harmless to a
  mean difference is *not* harmless to a per-symbol decision, and accounts for most of the
  run-to-run variation in tier 1's error rate. Validity gates are relative to an
  inferential use, and so are null results: §8.2's mean-difference proxy found nothing on
  a channel that works.

What remains is tier 3, the placement matrix, and a comparison against the published
attacks — §9. The ladder's shape is now established at both ends, and what it says is
that privilege buys rate rather than access.
