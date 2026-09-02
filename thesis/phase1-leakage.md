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
4. Leakage is linear in operand Hamming weight at +50.75 mW per set bit (§6), and that
   line is not an artifact of contrasting everything against zero (§7).
5. The line does not pass through the origin: an all-zero operand is cheap by +349 mW
   out of proportion to its weight, and the whole of that step sits at the boundary
   between weight 0 and weight 1 (§8). This is the finding the ML chapter leans on.
6. Hamming *distance* leaks too, at +34.14 mW per flipped bit with weight held fixed
   (§9). The two classical models of data-dependent power are usually presented as
   competitors; on this platform both terms are present and of comparable size.
7. Bit placement matters slightly and is not explained (§10).

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

From §7 onward every session also includes an **anchor** run — a contrast already
measured in an earlier session, repeated unchanged — because nothing else in the design
can detect a session-level shift, and cross-session comparison is otherwise unverifiable.
The practice was adopted after the fact: the sweep of §6 happened to include an operand
that an earlier session had also measured, which is the only reason its session could be
placed at all. The three sessions holding the same weight-16 operand read +1.133, +1.215
and +1.228 W, which is the size of session-level shift an anchor exists to expose.

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

Those are this session's numbers. §8 adds five more weights in the region this sweep
sampled most thinly and refits the two sessions together; the pooled slope is
+50.75 mW/bit at R² 0.967 over eighteen operands, and it is that fit the rest of the
thesis quotes.

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

The disagreement at the end of §7 is about the shape of the curve in a region the sweep
sampled at two points. Two shapes fit it. Either the step is a genuine discontinuity at
HW 0 — all-zero data is anomalously cheap to move and everything from HW 1 upward is one
straight line — or the curve climbs steeply over the first few bits and then flattens to
50 mW/bit, in which case there is no discontinuity to explain and the sweep simply missed
the bend.

Sampling the gap distinguishes them directly. Hamming weights 1, 2, 3, 4 and 6, two bit
patterns each, contrasted against the same all-zero baseline and at driver settings
identical to the sweep so that the points pool with it
(`results/20260902-211017-phase1_low_end`, 12 runs × 3 repeats, 78/78 gates pass; the A/A
is +8.4 mW with the sign flipping and accuracy 0.516):

| HW | operands | Δ power | between-run SD |
|---|---|---|---|
| 0 | *(A/A control)* | +0.008 W | 0.023 |
| 1 | `0x00000008` / `0x08000000` | +0.465 / +0.350 W | 0.086 / 0.159 |
| 2 | `0x01000040` / `0x00200080` | +0.489 / +0.387 W | 0.020 / 0.016 |
| 3 | `0x00042001` / `0x01800800` | +0.465 / +0.456 W | 0.088 / 0.020 |
| 4 | `0x0090000C` / `0x00004848` | +0.513 / +0.517 W | 0.029 / 0.048 |
| 6 | `0x84048014` / `0x30200640` | +0.730 / +0.709 W | 0.156 / 0.075 |

The new points land *on* the existing line rather than filling in the gap beneath it. The
session anchor is +1.228 W against +1.133 W in the sweep and +1.215 W in the non-zero
session, so the three sessions are on comparable footing; pooling all eighteen operands
from HW 1 to HW 32 and fitting one line per repeat gives

**dP = 0.349 + 0.0508·HW watts, R² = 0.967, slope +50.75 mW/bit (SD 1.35 over three
repeats), intercept +349 mW (SD 26, i.e. 23 standard errors from zero)**

against dP(0) = 0 by construction and −2.8 mW measured across six pooled A/A repeats.
**The step is a discontinuity at zero.** The lowest weight it is possible to measure
already costs +0.41 W: setting one bit per 32-bit word — 3.1% of the bits in the buffer —
buys 22% of the full-range effect, and each of the remaining 31 bits costs 51 mW. The
step is eight times the marginal cost of a bit anywhere else on the curve.

That resolves the §7 disagreement in favour of the sweep. Pooled, `hw01 → hw04` is
+0.102 W where the line predicts +0.152 W and the non-zero session's chain implied
+0.217 W; there is no super-linear bend to find. Fitting the low-end points *alone* gives
+61.6 mW/bit, but with an SD of 18.4 over three repeats and a 95% interval of
[40.7, 82.4] — a 0.3 W span is too narrow to fit a slope through at this noise level, and
50.0 sits comfortably inside it. It is the pooled fit that carries the result.

Two consequences follow, and the second matters more than the first.

Every Δ in this chapter quoted against an all-zero baseline — the whole depth table of §4
included — carries a constant ≈0.35 W that belongs to the *baseline* rather than to the
test operand. Combined with §7, which found no per-contrast offset between two non-zero
operands, the entire step sits at the 0 → 1 boundary and nowhere else. The rankings in §4
are unaffected, since every row carries the same constant, but the absolute per-byte
figures are overestimates of the marginal cost of a set bit by that amount.

And a zero operand being disproportionately cheap is not a nuisance for this thesis; it
is the mechanism the application chapter depends on. Post-ReLU activations in a quantized
network are 50–90% zero and input-dependent, and a channel whose first set bit costs eight
times its marginal bit makes the *presence* of non-zero data far more visible than a
linear weight model would predict. Sparsity is exactly the property this leakage is best
at reporting. No mechanism is claimed here — zero-detection or clock gating on the data
path would produce this signature, but nothing in these measurements identifies which.

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

Both words are `0x05A5E34F` and a partner of equal weight, so every condition — baseline
included — carries a mean weight of 16 bits per word, and at 32-byte alternation every
`ymm` register is homogeneous, so the mean *register* weight is 128 bits in both
conditions too. Only the number of bits flipping between consecutive transfers moves
(`results/20260902-220517-phase1_hamming_distance`, 11 runs × 3 repeats, 72/72 gates
pass):

| HD | second word | Δ power | between-run SD | detector |
|---|---|---|---|---|
| 0 | *(A/A control)* | +0.019 W | 0.014 | 0.51 |
| 2 | `0x45A5634F` | +0.086 W | 0.141 | 0.77 |
| 4 | `0x25A5A3CD` | +0.203 W | 0.023 | 0.85 |
| 8 | `0x95A3C15B` | +0.343 W | 0.030 | 0.99 |
| 16 | `0xCDF62C0C` | +0.550 W | 0.071 | 0.97 |
| 16 | `0x39B065F4` | +0.632 W | 0.049 | 1.00 |
| 32 | `0xFA5A1CB0` (= ~A) | +1.139 W | 0.048 | 0.99 |

**Distance leaks, and nearly as much per bit as weight does:**

**dP = 0.048 + 0.0341·HD watts, R² = 0.974, slope +34.14 mW/bit (SD 2.96 over three
repeats, 95% CI [30.8, 37.5]).**

The prediction that priced a flipped bit at the 50.75 mW a set bit costs was too high by a
third, but only by a third: +1.14 W measured at HD 32 against +1.60 W predicted, and
nothing like the zero a pure static-weight model requires.

Three controls carry this result, and all three behave:

- **Composition is not the explanation.** The HD-32 condition is half `~A` by volume, so
  if `~A` were intrinsically dearer than `A` the contrast would inherit about half that
  difference. Measured directly on homogeneous buffers, `A → ~A` is +0.024 W with the
  sign flipping across repeats and detector accuracy 0.591 — bounding the composition
  contribution at roughly 12 mW, 2% of the effect at HD 32.
- **The new fill is the old one.** `0 → A|A` through the dual victim gives +1.198 W
  against +1.254 W for the identical contrast on `ws_l3_x8` in the same session, a
  difference of 0.056 W against between-run SDs of 0.071 and 0.116. The two code paths
  are not distinguishable, as they should not be, since with equal halves they write the
  same bytes.
- **The A/A is clean** at +19.1 mW with detector accuracy 0.509.

Note also what the intercept does *not* do. The weight line of §8 misses the origin by
+349 mW, 23 standard errors; the distance line's intercept is +48 ± 28 mW, 1.75 standard
errors, consistent with passing through zero. Whatever makes an all-zero operand cheap is
a property of the zero *value*, not a generic "this buffer holds two different things"
artifact — a generic artifact would have shown up here as a step at HD 1 and did not.

### 9.1 Which path does the switching happen on?

Running the largest contrast at both alternation periods separates them, under an
assumption of additive superposition. At 32 bytes all eight load-to-load transitions per
iteration toggle and no line-to-line transition does; at 64 bytes four of eight load
transitions toggle and every line transition does. Writing *L* for the load-path term at
full rate and *C* for the line-path term:

| | measured | composition |
|---|---|---|
| `ws_l3_x8_ab` (32 B) | +1.139 W | 1.0·*L* + 0.0·*C* |
| `ws_l3_x8_ab64` (64 B) | +0.781 W | 0.5·*L* + 1.0·*C* |

giving *L* = 1.139 W and *C* = 0.211 W. If only load-to-load switching mattered the
64-byte variant would have read +0.569 W; it reads +0.211 W above that. So the effect is
dominated by toggling on the narrow, fast path — every 32-byte load — with the line-fill
path contributing about 16% of the total. That ordering is what the rates suggest, since
the load path runs at roughly three times the line rate. Two points and a superposition
assumption are not a decomposition to lean on hard, and it is quoted as an estimate.

### 9.2 Both models are true

This is the result that most constrains how the rest of the thesis models leakage. The
two classical accounts of data-dependent power are usually presented as competitors, and
on this platform both terms are present and of comparable size:

| term | coefficient | intercept |
|---|---|---|
| static Hamming weight, on a constant stream | +50.75 mW per set bit | **+349 mW step at zero** |
| Hamming distance, at fixed weight | +34.14 mW per flipped bit | +48 ± 28 mW (through origin) |

Neither can be reduced to the other. The weight slope was measured on a stream whose
distance is pinned at zero, where a pure switching model predicts no effect at all; the
distance slope was measured at a weight that is identical in both conditions, where a
pure weight model predicts no effect at all. Both were measured, on the same victim, in
sessions whose anchors agree.

The practical reading for the chapters that follow is that a victim processing real data
modulates both terms at once, and the two will generally move together — data that
becomes heavier also tends to change more between words. Attributing an observed power
difference to weight alone would overstate what the weight model can do, and any attempt
to *invert* the channel to recover operand values has to carry both terms.

## 10. Bit placement matters a little, and is not explained

At equal Hamming weight, two bit patterns differ by 0.01–0.16 W. Pooling both sweep
sessions gives eight weights with more than one pattern; at two of them the spread between
patterns exceeds the between-repeat SD:

| HW | patterns | spread | between-run SD | verdict |
|---|---|---|---|---|
| 1 | three | 0.115 W | 0.095 | within noise |
| 2 | two | 0.102 W | 0.018 | **larger than noise** |
| 3 | two | 0.009 W | 0.054 | within noise |
| 4 | two | 0.052 W | 0.058 | within noise |
| 6 | two | 0.021 W | 0.115 | within noise |
| 8 | two | 0.162 W | 0.077 | **larger than noise** |
| 16 | two | 0.085 W | 0.071 | within noise |
| 24 | two | 0.041 W | 0.029 | within noise |

Neither a count of cyclic adjacent-bit transitions nor a count of non-zero bytes accounts
for the residual, and the two weights that exceed noise have no structure in common — at
HW 2 the two patterns are `0x01000040` and `0x00200080`, both with their bits in separate
bytes.

Hamming weight is therefore a good predictor of this leakage but not a complete one. The
honest statement is that a placement term exists, is of order 0.1 W against a 1.9 W full
range, appears at some weights and not others, and is not modelled here.

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

**The lowest distance point is at the noise floor.** At HD 2 the effect is +0.086 W with
a between-run SD of 0.141 and the sign flipping across repeats — the fitted line predicts
+0.068 W there, which this design cannot resolve from zero in three repeats. It is
included in the fit and reported as measured, but nothing rests on it; dropping it moves
the slope by less than its own standard error.

**The load-path/line-path split of §9.1 is an estimate, not a measurement.** It rests on
two points and an assumption that the two contributions add linearly, which nothing here
tests. The ordering it implies — the fast narrow path dominating — is consistent with the
relative rates, but a third alternation period would be needed to check the model rather
than assume it.

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
  +50.75 mW per set bit per 32-bit word (R² = 0.967 over 18 operands from weight 1 to 32),
  on top of a **discontinuity of +349 mW between weight 0 and weight 1** that belongs to
  the operand rather than to the contrast. An all-zero operand is cheap out of proportion
  to its weight; a single set bit per word costs eight times what the next bit costs.
- With weight held fixed, the difference is also linear in the Hamming *distance* between
  consecutive transfers, at +34.14 mW per flipped bit (R² = 0.974) — and this line does
  pass through the origin. Neither term reduces to the other: each was measured in a
  design where the other predicts exactly zero.
- A residual placement effect of order 0.1 W exists at fixed weight and is unmodelled.

For the chapters that follow, the operationally important number is not the largest
effect but the best-conditioned one. `ws_l3_x8` under Config-A reaches 95% detector
accuracy at n = 1–8 samples and 99% at n = 2–13 across eight runs — roughly 125–1000
bit/s raw at a ~1 ms RAPL period — while `ws_dram_x8`, which has the highest energy per
byte in the whole set, needs n = 13–89 for the same 95%. Largest Δ power and best
detectability are not the same property, and the covert-channel chapter picks its
transmitter on the second.
