# A Covert Channel Out of Operand Power

*Draft — Phase 2 chapter, **partial**. This covers tier 1 (RAPL) only. The two
unprivileged receiver tiers that make the chapter a security result rather than an
instrument reading are built into the plan but not yet built into the repository; §9
says exactly what is missing and what it would take. Every number here is reproducible
from `results/` plus the run manifests, cited inline as a run directory. All
measurements are Config-A (turbo disabled, frequency pinned) unless stated otherwise.*

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

1. The channel works. It runs error-free at 83 bit/s and reaches 500 bit/s at a bit-error
   rate of 0.037 (§5). That is an order of magnitude above the expectation this project
   set out with, which was "low tens of bits/s".
2. The error rate is set by a single quantity, the per-chip separation d′, and follows
   the Gaussian prediction Q(d′/√2) closely (§6).
3. The run-to-run variation in that error rate is the *instrument*, not the channel — and
   the artifact responsible was one the measurement chapter had recorded as harmless
   (§7). It is harmless to a mean difference and not to a per-symbol decision.
4. The ceiling on rate is the RAPL update period low-passing the modulation, not any
   limit of the transmitter (§8).

## 2. Threat model

The transmitter is an unprivileged process. It runs ordinary AVX loads over its own
memory and needs no capability a normal user process lacks — no root, no MSR access, no
elevated scheduling priority, no special mapping. In the intended scenario it is code
that has been induced to run inside a confidentiality boundary and has data it wants out.

The receiver, **in this chapter**, is privileged: it reads `MSR_PKG_ENERGY_STATUS`
through `/dev/cpu/N/msr`, which requires root. This is a deliberately weak threat model
and it is the chapter's principal limitation. A root receiver can already read the
transmitter's memory directly, so tier 1 on its own demonstrates the mechanism rather
than an attack. Its purpose is to establish the ceiling — what the channel can carry when
the receiver is as capable as it can be — against which the unprivileged tiers of §9 are
measured. The claim that this is a security result rests on those tiers, and this draft
does not yet make it.

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
transmissions buys back error rate but not bandwidth. And the unprivileged tiers of §9 are
not bound by this particular limit — `scaling_cur_freq` and self-timing have entirely
different bandwidths and entirely different noise — so tier 1 being the most privileged
receiver does not automatically make it the fastest.

## 9. What this chapter does not yet cover

This is a partial draft, and the gaps are not incidental.

**Tiers 2 and 3 do not exist yet.** The plan specifies a receiver ladder, and it is the
ladder rather than tier 1 that constitutes the security result: tier 2 reads
world-readable `scaling_cur_freq` and needs no privilege at all, tier 3 times its own
fixed workload and infers the transmitter's activity from frequency-induced dilation, in
the manner of Hertzbleed. `frequency_cpufreq()` already exists and is already used, so
tier 2 is cheap. **Both require Config-B**, because they observe the DVFS response that
Config-A deliberately removes — every number in this chapter is Config-A and none of it
transfers.

**Only one placement is measured.** The plan asks for cross-core, cross-SMT-sibling,
cross-P/E-core and cross-container. This chapter has cross-core between P-cores only.

**No comparison to the literature.** Liu et al. (CCS'22) and Hertzbleed are the two
obvious points of reference and neither is engaged.

**No BER-versus-rate curve per tier, and no capacity figure worth quoting.** The decoder
computes a binary-symmetric-channel capacity per run, but with one tier and one placement
it is a number without a comparison.

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
- Its error rate is quantitatively predicted by the per-chip separation d′ through the
  Gaussian Q(d′/√2), with a log-log correlation of +0.895 and a median ratio of 0.91,
  down to an unexplained error floor of a few times 10⁻³.
- The transmitter is not the limit at any rate tested. The limit is the ~1 ms RAPL
  integration window, which erodes the usable separation from 1.54 W to 0.64 W as the chip
  shrinks from 4 ms to 1 ms.
- A measurement artifact that the previous chapter correctly established as harmless to a
  mean difference is *not* harmless to a per-symbol decision, and accounts for most of the
  run-to-run variation in error rate. Validity gates are relative to an inferential use.

What it does not establish is the security claim, which needs an unprivileged receiver.
Tier 1 bounds what is there to be extracted; §9 is the work that decides how much of it a
process without privilege can actually reach.
