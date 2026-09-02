# What Leaks: Operand Movement, Not Arithmetic

*Draft — Phase 1 chapter. Every number here is reproducible from `results/` plus the
run manifests; the provenance for each claim is cited inline as a run directory. All
measurements are Config-A (turbo disabled, frequency pinned) unless stated otherwise.*

## 1. What this chapter asks

The previous chapter built an instrument and established what it can be trusted to say.
This one asks what it can see: given a victim thread executing a known instruction
stream over an attacker-chosen operand, which property of that operand is recoverable
from package power measured on another core?

The chapter was not originally scoped this way. The working plan for this project
allocated Phase 1 to characterising the *instruction mix* — a Hamming-weight sweep, a
weight-versus-distance contrast, and a per-instruction leakage table, all on a
register-resident AVX victim. That plan did not survive its first result. On the
rebuilt pipeline the register-resident victim does not leak at all (§3), and the
question of which instruction is executing turned out to be far less important than
whether the operand crosses a bus. The instruction-family work is still wanted, but it
has to be done on a victim that moves data or it measures a null; it is deferred, and
this chapter characterises operand *movement* instead.

The results, in order of how much they constrain the rest of the thesis:

1. Register-resident operands do not leak; operands that move do (§3).
2. Leakage per byte scales with how far the operand travels — 68× from L1 to DRAM (§4).
3. The effect is a genuine operand effect, not a bias of the harness (§5).
4. Leakage is linear in operand Hamming weight at +50.0 mW per set bit (§6),
   and that line is not an artifact of contrasting everything against zero (§7).
5. Bit placement matters slightly and is not explained (§10).

## 2. Method common to every experiment

Every experiment in this chapter is a set of *runs*; a run holds one victim and two
operand conditions and produces one CSV. Within a run the two conditions are
interleaved as short blocks in seeded-shuffled order, so thermal and frequency drift is
common-mode between them rather than aligned with condition. Each experiment is
repeated at least three times with the *order of runs within the session* reshuffled,
because interleaving cancels drift within a run but says nothing about a run's position
in a session. Between-repeat spread, not a single run's bootstrap interval, is the error
bar throughout; §5 of the previous chapter gives the reason, and the ratios in this
chapter's own tables — up to 6.8 — show how badly a single-run interval understates the
uncertainty on a large effect.

Three automated gates run over every result and the analysis exits non-zero if any
fails: zero-energy samples ≤1% of the total, temporal imbalance between conditions
≤0.10, and, for the A/A control that ships with every session, a bootstrap interval
containing zero together with detector accuracy ≤0.60. An A/A control is an ordinary run
with the same operand in both conditions; it has no effect by construction, so anything
it reports is the measurement path manufacturing signal. Gate counts are quoted per
session below.

From §6 onward every session also includes an **anchor** run — a contrast already
measured in an earlier session, repeated unchanged — because nothing else in the design
can detect a session-level shift, and cross-session comparison is otherwise unverifiable.

## 3. Register-resident operands do not leak

The preliminary result that motivated this project was a power difference between AVX
multiplies on all-zero and all-ones operands. On the rebuilt pipeline that contrast, run
on a register-resident `vpmuludq` victim, is indistinguishable from nothing.

The natural next question is what the original victim did that the rebuilt one does not.
Compiled at `-O0`, it kept its operands and a volatile result on the stack and reloaded
them every iteration; the rebuilt victim holds them in `ymm0`/`ymm1` for a whole burst,
where the bit pattern never crosses a bus. That suggests a 2×2 over loads and stores,
holding the instruction (`vpmuludq`), the operands (`0` against `0xFFFFFFFF`) and the
methodology fixed, and varying only the surrounding memory traffic
(`results/20260822-215306-phase1_memory_replication`, 3 repeats with victim order
reshuffled each repeat, 39/39 gates pass):

| victim | traffic | Δ power | between-run SD | sign | detector |
|---|---|---|---|---|---|
| `avx2_mul` | none (register-resident) | −0.06 W | 0.034 | all − | 0.57 |
| *(A/A control)* | *none — true zero* | *−0.06 W* | *0.075* | *flips* | *0.52* |
| `avx2_load` | loads only, **no ALU** | +0.21 W | 0.017 | all + | 0.85 |
| `avx2_mul_st` | multiply + stores | +0.32 W | 0.049 | all + | 0.93 |
| `avx2_mul_ld` | loads + multiply | +0.34 W | 0.025 | all + | 0.94 |
| `avx2_mul_ldst` | loads + multiply + stores | **+0.51 W** | 0.049 | all + | **0.98** |

The register-only row must be read against the A/A row rather than against zero. The A/A
control has no effect by construction and still lands at −0.06 W, so −0.06 W is where
this harness puts a true zero on that victim: `avx2_mul` is at the noise floor.

Two things in that table matter more than the headline. `avx2_load` performs no
arithmetic whatsoever — it is eight `vmovdqa` loads and a loop — and leaks +0.21 W with
the tightest between-run spread of any victim in the set. And loads and stores are
roughly additive: 0.21 (loads) + 0.32 (stores, with the multiply) ≈ 0.51 (both). The
vector ALU is not necessary for the effect, and nothing in the data requires it to
contribute at all.

This is a more specific claim than the one it replaces, and it explains an inconsistency
in the preliminary work: the Eigen and TensorFlow sparsity results, which stream large
matrices through memory, separated cleanly, while a register-resident microbenchmark
built to isolate the arithmetic showed nothing. The ML chapter is unaffected by the
retargeting — arguably better motivated by it.

## 4. Leakage per byte scales with how far the operand travels

If movement is what leaks, the distance moved should matter. The traffic-volume sweep
(`results/20260824-213524-phase1_traffic_volume`, 3 repeats, 24 runs) holds the
instruction stream fixed at eight `vmovdqa` loads per iteration and varies only the
working set, so each variant draws its operand from a different level of the hierarchy.

The variants do not move data at equal rates — a DRAM-resident stream is an order of
magnitude slower than an L1-resident one — so watts are not the comparable quantity.
Energy per byte is, and mW per GB/s is exactly picojoules per byte:

| victim | working set | GB/s | Δ power | **pJ/byte** | detector |
|---|---|---|---|---|---|
| `ws_l1_x8` | 16 K (L1) | 741 | +0.23 W | **0.31** | 0.76 |
| `ws_l2_x8` | 512 K (L2) | 330 | +1.10 W | **3.35** | 0.96 |
| `ws_l3_x8` | 4 M (L3) | 148 | +2.05 W | **13.88** | 0.996 |
| `ws_dram_x8` | 32 M (DRAM) | 41 | +0.87 W | **21.02** | 0.998 |

A 68× rise from L1-resident to DRAM-resident, monotone in depth, and reproduced across
two sessions with different thermal histories and different shuffles (a partial sweep
two days earlier gives 0.40 / 3.36 / 14.80 / 22.73 pJ/byte).

The absolute watt difference peaks at **L3, not DRAM**, because throughput falls faster
than per-byte cost rises. This is why the per-byte normalisation is the right frame, and
it is a practical warning for the covert-channel chapter: ranking candidate transmitters
by Δ power alone would pick the wrong one.

Two qualifications. First, the package RAPL domain on this part exposes `package-0`,
`core` and `uncore` but no `dram` domain, so the DRAM figure is measured at the memory
controller with the DRAM devices' own energy excluded; the true operand-movement cost at
that depth is larger than the table says, not smaller. Second, cache residency is not
measured directly — `perf_event_paranoid=4` makes `perf` unusable unprivileged on this
machine — and is corroborated only indirectly, by achieved bandwidth matching each
level's expected ceiling (68 B/cycle/core for the L1 variant against Golden Cove's 3×32 B
peak, 34 B/cycle for L2, and 42 GB/s aggregate for DRAM, about 55% of dual-channel
DDR5-4800). That is weaker than a hardware counter and is stated as such.

The same sweep's second axis — loads per iteration, 1/2/4/8, at a fixed L1-resident
working set — is **not** usable as a dose axis. It gives 0.50 / 1.10 / 1.05 / 0.31
pJ/byte, non-monotone, because throughput saturates well before the instruction count
does (217 → 741 GB/s across a nominal 8× increase). Volume of traffic at fixed distance
is not a clean independent variable on this machine; depth is.

## 5. The effect is the operand's, not the harness's

Every experiment above assigned the all-zero operand to condition 0 and the test operand
to condition 1, in that order. A real operand effect *E* and any bias *B* attached to the
condition *index* — a sampler artifact, a block-ordering asymmetry, anything that adds to
whichever condition is second — are perfectly confounded under that design. Both add to
condition 1. The suspicion was concrete rather than theoretical: the traffic-volume
sweep's `ws_dram_x8` A/A control failed its gate at +15.4 mW with all three repeats
positive.

The separation is cheap. Running the same contrast with the selector mapping reversed
gives *−E + B*, so *(fwd − rev)/2* recovers the effect and *(fwd + rev)/2* the bias
(`results/20260824-222234-phase1_polarity`, 4 repeats, 40/40 gates pass):

| | value |
|---|---|
| `l3_forward` | +1.841 W |
| `l3_reverse` | −1.855 W |
| **E** = (fwd − rev)/2 | **+1.848 W** |
| **B** from 8 A/A runs | **+0.7 ± 3.1 mW**, 95% CI [−5.4, +6.8] |

The effect flips sign cleanly and symmetrically. The +15.4 mW that prompted the check
sits 4.7 standard errors outside the A/A interval, and both A/A flavours — all-zero and
all-ones operands — come out at zero with the sign flipping across repeats, so the bias
does not depend on the operand value either. **No bias correction is warranted** and the
depth table stands as measured.

One design note is worth carrying forward, because the obvious way to do this is wrong.
*B* must be estimated from A/A runs, not from *(fwd + rev)/2*. On a victim where
*E* ≈ 2 W the between-run SD is ~100 mW, so that average has a standard error of about
27 mW over four repeats and cannot resolve a 15 mW bias at all, whereas A/A runs — whose
between-run SD is 4.6–10 mW — reach about 3 mW.

## 6. Leakage is linear in operand Hamming weight

With a victim that leaks and a design that is not biased, the operand's own structure can
be swept. `ws_l3_x8` is the instrument of choice: best-conditioned in the depth table,
detector 0.996, and 2 W of headroom. Eleven runs × 3 repeats, each contrasting a test
operand against an all-zero working set
(`results/20260901-213211-phase1_hamming_weight`, 70/71 gates pass — the exception is
discussed in §11):

| HW | operand(s) | Δ power | between-run SD | detector |
|---|---|---|---|---|
| 0 | *(A/A control)* | −0.014 W | 0.028 | 0.54 |
| 1 | `0x00000008` | +0.42 W | 0.039 | 0.84 |
| 4 | two patterns | +0.46 / +0.56 W | 0.085 / 0.055 | 0.90 / 0.86 |
| 8 | two patterns | +0.71 / +0.88 W | 0.107 / 0.047 | 0.85 / 0.99 |
| 16 | two patterns | +1.13 / +1.22 W | 0.120 / 0.022 | 1.00 / 1.00 |
| 24 | two patterns | +1.57 / +1.61 W | 0.044 / 0.014 | 1.00 / 1.00 |
| 32 | `0xFFFFFFFF` | +1.90 W | 0.051 | 1.00 |

Fitting one line per repeat and taking the spread across repeats as the error bar:

**dP = 0.362 + 0.0500·HW watts, R² = 0.974, slope +50.0 mW/bit (SD 0.25 over three
repeats, 95% CI [49.8, 50.3]).**

Alternative shapes were fitted and rejected: √HW gives R² 0.951, log(1+HW) 0.874, and a
pure power law through the origin 0.928. Adding a quadratic term, a count of cyclic
adjacent-bit transitions, or a count of non-zero bytes each buys ≤0.003 of R² for an
extra parameter. The `hw32` point also reproduces `phase1_polarity`'s `l3_forward` across
sessions (+1.904 against +1.841 W), which is the only cross-session check that session
had.

## 7. The intercept, and the offset it is not

The interesting part of that fit is the constant. The line extrapolates to +362 mW at
HW = 0, but dP(0) is zero by construction and the A/A control confirms it at −14 mW. A
single set bit in a 32-bit word already costs +0.42 W — 22% of the full-range effect —
so the curve has a step somewhere below HW 1 that the line cannot represent.

Two readings fit the sweep equally well, and one of them would have been serious. Either
the step belongs to the *operand* — a set bit is disproportionately expensive at the low
end, or, equivalently, all-zero data is anomalously cheap to move on-die — or it belongs
to the *contrast*: alternating a victim between any two distinct working sets might cost
a fixed ~0.36 W regardless of their contents. Under the second reading every A/B result
in this project, including the depth table, is inflated by that constant, and no A/A
control could have caught it, because an A/A holds the same value in both conditions and
therefore never swaps buffers.

The two are separated by contrasting non-zero operands *against each other*, so that
nothing in the experiment touches the all-zero baseline
(`results/20260901-225254-phase1_nonzero_baseline`, 7 runs × 3 repeats, 48/48 gates
pass). Predictions are differences of the sweep's own per-pattern means, so under the
first reading each measurement should match its prediction and under the second each
should exceed it by roughly 0.36 W:

| contrast | ΔHW | measured | predicted | mW/bit |
|---|---|---|---|---|
| `hw04_a → hw08_a` | 4 | +0.326 W | +0.251 | 81 |
| `hw08_a → hw16_a` | 8 | +0.388 W | +0.418 | 48 |
| `hw16_a → hw32` | 16 | +0.774 W | +0.771 | 48 |
| `hw01 → hw32` | 31 | +1.705 W | +1.482 | 55 |
| `hw16_a → hw16_b` | 0 | +0.071 W | +0.085 | — |
| `0 → hw16_a` (anchor) | 16 | +1.215 W | +1.133 | 76 |

Regressing measured on predicted gives an intercept of **+14 ± 23 mW**, unchanged whether
or not the session is rescaled by its anchor. The +362 mW of the second reading sits 15
standard errors away. **There is no per-contrast offset; the depth table and every
earlier A/B result stand as measured.**

This session also closed a narrower gap. Its A/A control holds a non-zero operand
(+10.6 mW, sign flipping, accuracy 0.506) — every previous A/A in this project held
all-zeros or all-ones, so none of them could have revealed anything peculiar to the zero
buffer specifically.

What the session did not settle is *where* below HW 1 the step lives. Two of its
contrasts, both touching the lowest weights, came in above prediction (+57 mW at z = 2.7
and +115 mW at z = 2.5, both positive), and its internal chain implies
`hw01 → hw04` = +0.217 W where the sweep's own points put that step at +0.043 W. A factor
of five is not a rounding disagreement, and it points at the region the sweep sampled
most thinly.

## 8. The first few bits

<!-- PENDING: experiments/phase1_low_end.json, launched 2026-09-02.
     HW 1, 2, 3, 4, 6, two patterns each, against the same all-zero baseline and at
     driver settings identical to the sweep so the points pool with it. Distinguishes a
     genuine discontinuity at zero (points near the fitted line) from a smooth but
     super-linear climb over the first few bits (points climbing steeply from HW 1 to
     HW 4, then rejoining the line). Fill from results/<run_id> when it lands. -->

## 9. Weight, or distance?

The sweep in §6 measures a stream whose Hamming *distance* is zero. Every working-set
victim used so far fills its buffer with a single repeated 32-bit word, so consecutive
32-byte transfers are bit-identical and only the static weight of the operand ever
varies. That is a confound rather than a detail, because it makes the two competing
models of data-dependent power indistinguishable. Classical differential power analysis
attributes the draw to bits *flipping* on a bus — charging and discharging its
capacitance — and predicts nothing from a constant value however heavy it is. A +50.0
mW/bit slope on a constant stream should not exist under that model at all.

Separating them needs a victim that can vary distance while holding weight fixed.
`ws_l3_x8_ab` splits its 64-bit selector into two 32-bit words and alternates them every
32 bytes, one `ymm` load wide, so consecutive transfers differ in exactly HD(A, B) bits
per word. Choosing both words with Hamming weight 16 holds the mean weight of the stream
at 16 in every condition, identical to the constant-word baseline: weight is common-mode
and distance is the only thing that moves. With the two halves equal the fill is
bit-identical to the single-word one, so the baseline condition is exactly `ws_l3_x8`.

The alternation period decides which path sees the switching, and one period cannot
cover both. At 32 bytes every `ymm` load differs from the one before it, which is the
toggling the load ports and the L1 read path see — but a 64-byte line is then a fixed
A-then-B composite, so consecutive line fills from L2 or L3 are identical and that path
sees no switching at all. `ws_l3_x8_ab64` alternates every 64 bytes instead: consecutive
lines differ, at the cost of halving the load-to-load toggle rate. The largest contrast
is run at both granularities, so that a null cannot be explained by the bus in question
never having seen a transition.

<!-- PENDING: experiments/phase1_hamming_distance.json.
     HD 2, 4, 8, 16 (two patterns), 32 at fixed HW 16 on ws_l3_x8_ab, the same HD 32 on
     ws_l3_x8_ab64, plus three controls: an A/A on the dual victim, an equivalence run
     (0 -> A|A on the dual victim must reproduce the sweep's hw16_a through the new
     code path), and a placement control (A -> ~A, both homogeneous, both HW 16,
     bounding how much of the HD-32 condition is composition rather than switching).
     Pricing a flipped bit at the 50.0 mW a set bit costs predicts +1.60 W at HD 32
     under a pure switching model, against 0 under a pure weight model.
     Fill from results/<run_id> when it lands. -->

## 10. Bit placement matters a little, and is not explained

At equal Hamming weight, two random bit patterns differ by 0.04–0.16 W. The largest gap
is at HW 8 (+0.71 against +0.88 W, 2.1× the between-run SD); the gaps at HW 4, 16 and 24
are within run-to-run noise. Neither a count of cyclic adjacent-bit transitions nor a
count of non-zero bytes accounts for the residual.

Hamming weight is therefore a good predictor of this leakage but not a complete one. The
honest statement is that a placement term exists, is roughly an order of magnitude
smaller than the weight term over the full range, and is not modelled here.

## 11. Threats to validity

**A single run's confidence interval is optimistic, and worst for large effects.** The
ratio of between-run spread to within-run bootstrap half-width runs from 0.19 to 6.8
across this chapter's sessions, and the largest ratios belong to the largest effects: for
`l3_forward` a single run's ±15 mW interval sits inside a between-run spread of ±100 mW.
The effect apparently scales with a thermal and frequency state that is constant within a
run and varies between them. No single-run interval is quoted as an error bar anywhere in
this chapter.

**One A/A repeat failed its gate.** In the Hamming-weight session one A/A repeat returned
a 95% interval of [−100.2, −0.6] mW, excluding zero by 0.6 mW, with p = 0.072 and a
+0.3 W decile spike in one condition. Across its three repeats that A/A is −14.1 mW with
the sign flipping, so the aggregate is clean. This is the documented failure mode of the
previous paragraph rather than a new one, and it is why the reporting unit is
`analysis.aggregate` over at least three repeats — but the failure is recorded here
rather than waved away.

**Victim cores are not isolated.** The kernel is booted with `isolcpus=0`, which isolates
the monitor core only; victim cores 2, 4, 6, 8 and 10 still receive stray system work.
Extending the isolation requires a boot-parameter change and has not been done.

**The detector is a mean threshold.** It converts an effect into an accuracy-versus-*n*
curve, which is what the covert-channel chapter needs, but it is blind to effects that
live in variance rather than mean.

**Cache residency is inferred, not measured** (§4), and the DRAM figure excludes DIMM
energy.

**One machine, one microarchitecture.** Everything here is an i7-12700H at 2.3 GHz with
no AVX-512. Nothing in this chapter establishes that the coefficients transfer.

## 12. What this chapter establishes

A quantitative leakage model for operand movement on this platform:

- The leaking element is the movement of the operand, not the arithmetic performed on
  it. A victim doing only loads leaks; a victim doing only register-resident multiplies
  does not.
- Cost per byte moved rises monotonically with the depth the operand is drawn from,
  0.31 pJ/byte at L1 to 21.02 pJ/byte at DRAM. Absolute power difference peaks at L3.
- Within a fixed victim, the difference is linear in the operand's Hamming weight at
  +50.0 mW per set bit per 32-bit word (R² = 0.974), on top of a step between weight 0
  and weight 1 that belongs to the operand rather than to the contrast.
- A residual placement effect of 0.04–0.16 W exists at fixed weight and is unmodelled.

For the chapters that follow, the operationally important number is not the largest
effect but the best-conditioned one. `ws_l3_x8` under Config-A reaches 95% detector
accuracy at n = 1–8 samples and 99% at n = 2–13 across eight runs — roughly 125–1000
bit/s raw at a ~1 ms RAPL period — while `ws_dram_x8`, which has the highest energy per
byte in the whole set, needs n = 13–89 for the same 95%. Largest Δ power and best
detectability are not the same property, and the covert-channel chapter picks its
transmitter on the second.
