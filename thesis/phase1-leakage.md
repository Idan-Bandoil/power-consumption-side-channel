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
2. Leakage per byte scales with how far the operand travels — 55× from L1 to DRAM once
   each depth's own zero-step is removed (§4, §8.1).
3. The effect is a genuine operand effect, not a bias of the harness (§5).
4. Leakage is linear in operand Hamming weight at +50.75 mW per set bit (§6), and that
   line is not an artifact of contrasting everything against zero (§7).
5. The line does not pass through the origin: an all-zero operand is cheap out of
   proportion to its weight, by +349 mW at L3, and the whole of that step sits at the
   boundary between weight 0 and weight 1 (§8). The step is not a platform constant —
   it scales with the depth the operand is drawn from, and is absent at L1 (§8.1). This
   is the finding the ML chapter leans on.
6. Hamming *distance* leaks too, at +34.14 mW per flipped bit with weight held fixed
   (§9). The two classical models of data-dependent power are usually presented as
   competitors; on this platform both terms are present and of comparable size.
7. The instruction consuming the operand matters, but only by 10–24% on top of the
   movement term, and not because of its result (§10).
8. Bit placement matters slightly and is not explained (§11).

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

One qualification, established later and flagged here so this section is not read too
broadly: the null in the first row is `vpmuludq`'s. It does **not** hold for every
instruction. `vpdpbusd` leaks +0.20 W with its operand never leaving a register (§10.1).
What this table shows is that the ALU is not *necessary* for the leak, not that no
instruction can produce one on its own.

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

| victim | working set | GB/s | Δ power | **Δ pJ/byte** | detector |
|---|---|---|---|---|---|
| `ws_l1_x8` | 16 K (L1) | 741 | +0.23 W | **0.31** | 0.76 |
| `ws_l2_x8` | 512 K (L2) | 330 | +1.10 W | **3.35** | 0.96 |
| `ws_l3_x8` | 4 M (L3) | 148 | +2.05 W | **13.88** | 0.996 |
| `ws_dram_x8` | 32 M (DRAM) | 41 | +0.87 W | **21.02** | 0.998 |

A 68× rise from L1-resident to DRAM-resident, monotone in depth, and reproduced across
two sessions with different thermal histories and different shuffles (a partial sweep
two days earlier gives 0.40 / 3.36 / 14.80 / 22.73 Δ pJ/byte).

**These figures are uncorrected, and the correction is not a constant.** Every row here
contrasts an all-zero operand against an all-ones one, and §8 shows that the all-zero
operand is anomalously cheap — so each row carries a step that belongs to its baseline
rather than to the distance travelled. That step was originally measured on `ws_l3_x8`
alone and assumed to be common to the table, which is a safe assumption in watts and an
unsafe one per byte, because dividing a fixed watt offset by throughputs spanning
41–741 GB/s corrects each row by a different amount. §8.1 measures it separately at every
depth and finds it depth-dependent: −60 mW at L1, +231 at L2, +194 at L3 and +52 at DRAM.
Removing each depth's own step leaves the ladder

| victim | GB/s | Δ pJ/byte uncorrected | Δ pJ/byte less its own step |
|---|---|---|---|
| `ws_l1_x8` | 723 | 0.225 | **0.319** |
| `ws_l2_x8` | 339 | 3.150 | **2.454** |
| `ws_l3_x8` | 143 | 12.427 | **10.971** |
| `ws_dram_x8` | 41 | 18.514 | **17.598** |

on the depth × operand session's own measurements of the same contrast
(`results/20260904-103411-phase1_depth_operand`), which reproduce this table's shape at
0.225 / 3.150 / 12.427 / 18.514 uncorrected. **The corrected ladder is 55×, still
monotone, with L1 still positive and L3 still well clear of DRAM.** The conclusion of this
section is therefore unchanged and its ratio is revised from 68× to 55×; what the
correction removes is not the depth dependence but a baseline artifact that was largest in
the middle of the range.

**Δ pJ/byte, and the delta is not decoration.** This column is the *difference* in energy
per byte between two operands, which is what the experiment measures: it contrasts an
all-zero working set against an all-ones one and divides the power difference by the
traffic. It is not the energy needed to move a byte, which is a much larger quantity that
nothing in this project measures and which would require a contrast against not moving
the byte at all. The distinction is easy to lose because published DRAM transport
energies are quoted in the same units — 10–20 pJ/bit is a common figure — and the DRAM
row's 21.02 sits invitingly close to them. They are not the same quantity and should not
be compared. Every per-byte number in this chapter is a delta, and the tables and
`analysis.aggregate`'s column header say so.

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
Δ pJ/byte, non-monotone, because throughput saturates well before the instruction count
does (217 → 741 GB/s across a nominal 8× increase). Volume of traffic at fixed distance
is not a clean independent variable on this machine; depth is.

### 4.1 Which rail: core or uncore?

The package counter is a sum of independently metered sub-domains, and reading them apart
does two things at once — it says *where* in the package the operand effect sits, and it
cross-checks the package number against a counter the package model does not itself
produce. This part exposes two sub-domains beside the package: PP0 (`MSR_PP0_ENERGY_STATUS`,
the cores and, on a client SKU, their caches) and PP1 (graphics, idle here). The driver
records both at every RAPL edge, so every condition difference is available in three
domains: package, core (PP0), and uncore, taken as package minus core. Repeating the
hw32-against-zero contrast at three depths, plus the HW-16 anchor and the L3 A/A
(`results/20260929-134751-phase1_domain_split`, 5 labels × 3 repeats, `power_state`
constant at PL1 200 W across all 30 snapshots):

| contrast | Δ package | Δ core (PP0) | Δ uncore (pkg−core) | core share | uncore base |
|---|---|---|---|---|---|
| `l1_hw32` | +0.365 W | +0.364 | +0.000 | 100% | 0.61 W |
| `l3_hw32` | +1.938 W | +1.927 | +0.011 | 99% | 0.67 W |
| `dram_hw32` | +0.679 W | +0.679 | +0.000 | 100% | 1.69 W |
| `anchor_hw16` | +1.093 W | +1.091 | +0.002 | 100% | 0.67 W |
| `aa_l3` (A/A) | −0.024 W | −0.025 | +0.001 | — | 0.78 W |

Two readings. **As a mechanism result, the operand effect is a core-domain effect at every
depth.** Between 99% and 100% of each package difference is in PP0, and the separately
metered uncore rail does not move with the operand — Δ uncore is +11 mW at its largest and
within noise of zero elsewhere. This holds at DRAM, where the intuition points the other
way: streaming from DRAM lights up the memory controller and the ring, and the uncore's
*baseline* does rise with traffic volume (0.61 W at L1 to 1.69 W at DRAM). But its
operand-dependent *difference* stays at zero. The clean statement is that traffic volume
drives the uncore while operand value drives the core: the bytes cost energy in the ring
and controller regardless of what they are, and the data-dependent part — the Hamming-weight
term this chapter is built on — is paid where the bits are toggled at rate, in the load
path, caches and register file that PP0 meters. This localises the leak more sharply than
"package power" did, and it does so without a new victim, from two extra MSR reads per edge.

**As a cross-check, it is the RAPL-internal answer to whether the counter is physical.**
The l3_hw32 package difference reproduces the +1.90 W measured across earlier sessions
(+1.938 W here, between-run SD 14 mW), and it does so while also appearing, cleanly and
reproducibly, in the PP0 sub-counter — with the A/A control flat in every domain
(≤ 1 mW). A flat package-level activity model that manufactured the effect would have to
manufacture it consistently in an independently addressed sub-counter as well, which is a
much stronger thing to have to assume. This is the weakest-sharing of the three
corroborations gathered for the counter (it is still RAPL); the battery cross-check in the
measurement chapter (§5.2) and the timing receiver of chapter 3, which reads no RAPL at
all, share progressively less with it.

Two honest limits. On this consumer part PP0 bundles the cores with their caches rather
than isolating the execution units, and "uncore" as computed here folds in the idle
graphics domain, so neither figure is a pure structural attribution — the result is
core-rail against everything-else, not ALU against ring. And it does not contradict the
depth ladder of §4: depth still moves the *package* effect 55×, and what §4.1 adds is that
whatever depth the operand is drawn from, the value-dependent energy is collected on the
core rail.

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
discussed in §12):

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

Alternative shapes were fitted and rejected by `analysis.modelcompare`, one fit per repeat
as above. Raw R² alone would not be a fair test: √HW and log(1+HW) sit near the origin that
dP(0)=0 makes exact, while the line misses it by 23 SE (§7), so a curved form would be
judged partly on the one point the line is licensed to ignore. Giving *every* candidate a
free intercept and scoring on AICc — which charges for parameters instead of rewarding
flexibility — keeps the verdict. Against the line (R² 0.974) √HW (R² 0.951) loses by
ΔAICc 6.8 and log(1+HW) (R² 0.875) by 16.5; pooling the low-end points §8 adds, where the
shapes diverge most, widens both to 13.7 and 30.9. The two forms flexible enough to curve
toward the origin — a free-intercept power law a+b·HWᵏ and a saturating a+b(1−e^(−HW/τ)) —
instead straighten out: their shape parameters converge on the line (exponent 0.90,
τ ≈ 90 ≫ 32), and after AICc's penalty for the extra parameter they still fall short. Held
out one whole Hamming weight at a time, the line predicts it to 90 mW RMSE against √HW's 139
and log's 232. Adding a quadratic term, a count of cyclic adjacent-bit transitions, or a
count of non-zero bytes each buys ≤0.003 of R² for an extra parameter — which AICc rejects
outright. The `hw32` point also reproduces `phase1_polarity`'s `l3_forward` across
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
included — carries a step that belongs to the *baseline* rather than to the test operand.
Combined with §7, which found no per-contrast offset between two non-zero operands, that
step sits at the 0 → 1 boundary and nowhere else. How large it is at depths other than L3
is a separate question, and §8.1 answers it; the figure of +349 mW above is a measurement
on `ws_l3_x8` and not a platform constant.

An earlier draft of this paragraph drew the obvious conclusion for the application
chapter: post-ReLU activations are 50–90% zero, so a channel whose first set bit costs
eight times its marginal bit should make sparsity exactly the property this leakage is
best at reporting. That conclusion was an extrapolation — every buffer measured so far
holds one repeated word, so it is either entirely zero or entirely not, and real
activations are a *mixture* — and §8.3 tests it. It does not survive in that form. A
mixture does not inherit the cheap zero word by word: a buffer that is 87.5% zero words
pays the whole step, and power then tracks the buffer's *mean* Hamming weight along the
same line as a single repeated word. The step therefore separates "entirely zero" from
"anything else" and says nothing about how sparse a non-zero buffer is; what makes
partial sparsity readable is the per-bit weight term, and what it reads is mean bit
density rather than the count of zeros as such. No mechanism for the step is claimed here —
zero-detection or clock gating on the data path would produce this signature, but nothing
in these measurements identifies which.

### 8.1 The step scales with depth

The step above was measured on `ws_l3_x8` and on nothing else, and §4's ladder is reported
per byte. Those two facts do not compose on their own, and until they are made to, the
chapter carries two results that point in opposite directions.

If the step is a constant in **watts**, dividing it by throughputs spanning 41–741 GB/s
corrects each of §4's rows by a different amount per byte: the ladder collapses from 68×
to about 5.5×, L3 and DRAM become indistinguishable, and the L1 row goes *negative* — that
is, L1-resident operand movement would leak nothing at all beyond the zero anomaly. If the
step is a constant in **Δ pJ/byte**, §4 stands exactly as published. Both readings fit
everything measured so far and they give opposite headlines.

Both are also arithmetically impossible at L1, whose entire HW 0 → 32 effect is +228 mW
against a step of +349 mW in the first reading, or +1.75 W in the second. That points at a
third possibility — the step scales with depth, like the rest of the effect, and is not a
baseline offset at all — but an argument from impossibility says only that the first two
are wrong, not what the third is worth. It has to be measured.

Crossing the two axes measures it. Four depths × four Hamming weights, every run
contrasting the test operand against an all-zero working set exactly as §6's sweep did, so
that the L3 column is directly comparable to it, and with the bit patterns held identical
across depths so that a difference between depths is a depth difference and not a
placement one (`results/20260904-103411-phase1_depth_operand`, 22 labels × 3 repeats).
Weights 1 and 2 sit below the discontinuity's shoulder and pin the low-end level; 8 and 32
pin the slope. The step at each depth is then the intercept of the line through those four
points, which assumes nothing about the shape of the curve below HW 1 — a design the low-
end sweep above showed to be necessary.

| depth | HW 1 | HW 2 | HW 8 | HW 32 | intercept *a* | slope *b* | *b*·32 |
|---|---|---|---|---|---|---|---|
| L1 | −0.058 | −0.074 | +0.042 | +0.163 | **−60 mW** | +7.23 ± 2.97 mW/bit | 231 mW |
| L2 | +0.265 | +0.295 | +0.413 | +1.068 | **+231 mW** | +25.99 ± 4.10 | 832 mW |
| L3 | +0.270 | +0.319 | +0.515 | +1.775 | **+194 mW** | +48.96 ± 3.24 | 1567 mW |
| DRAM | +0.037 | +0.085 | +0.296 | +0.759 | **+52 mW** | +22.52 ± 1.20 | 721 mW |

Δ power in watts; error bars are the spread of one fit per repeat, which is this chapter's
reporting unit throughout.

**The step is depth-dependent, and it is the third reading.** At L1 it is absent and
slightly negative — the all-zero operand is not cheap there at all. At L3 it is +194 mW,
about 55% of the +349 mW that §8 established on that victim in a different session. At
DRAM it is +52 mW, inside its own spread of zero. So the constant that §8 found is a
property of the transport path the operand takes, not of the baseline value, and it cannot
be lifted from one depth and applied to another.

Removing each depth's own intercept gives the corrected ladder already quoted in §4:
0.319 / 2.454 / 10.971 / 17.598 Δ pJ/byte, a **55× rise, monotone, L1 positive and L3
clear of DRAM**. The collapse the first reading predicted does not happen, and §4 keeps
its conclusion with its ratio revised.

Three checks tie the session to the rest of the chapter. Its anchor reads +1.160 W against
+1.133, +1.215 and +1.228 W in the three earlier sessions; `l3_hw32` reads +1.775 W
against +1.904 in §6 and +1.841 in §5's polarity control; and the fitted L3 slope of
+48.96 mW/bit reproduces the pooled +50.75 of §8 inside its error bar. The session is on
the same footing as the corpus.

**The grid is also the chapter's only interaction term.** §4, §6, §9 and §10 each vary one
factor with the others held fixed, and none of them tests whether the coefficients
multiply. Here the weight coefficient rises from 7.2 mW/bit at L1 to 49.0 at L3 and falls
to 22.5 at DRAM — a factor of nearly seven across the range — so weight and depth are not
separable and a combined model needs the product rather than a sum of independently
fitted terms. Both the slope and the intercept peak at L3, where the absolute watt
difference already did.

### 8.2 What the same session says about the instrument

Two controls in that grid were included to measure the measurement, and both returned
something.

**Two buffers holding bit-identical data differ by about 230 mW.** Every `ws_*` contrast
confounds the condition with the buffer address, because `ws_init` maps one buffer per
slot and `ws_get` assigns a slot per distinct selector — so conditions 0 and 1 always read
from different mappings. An ordinary A/A cannot see this: holding the same selector twice
yields one slot. But `ws_get` keys its cache on the full 64-bit selector while `ws_fill`
uses only the low 32 bits, so `0x00000000_5A5A5A5A` and `0x00000001_5A5A5A5A` allocate two
distinct buffers with identical contents, and the victim loop reads nothing but the
pointer it is handed. That control **fails in all three repeats**, at −0.198, −0.354 and
−0.128 W, same sign throughout, with detector accuracy 0.78–0.98. Achieved throughput
moves with it — −1.08%, −0.27%, −0.60%, the second buffer always the slower — so part of
the effect is placement acting through bandwidth. The single-buffer A/A in the same
session is clean at −0.003 W and 0.02% throughput spread, so this is not the harness.

It is not, however, a correction to apply to the results above, and the chapter already
contains the evidence that bounds it. §5's polarity control ran this victim forward and
reverse and gave `(fwd+rev)/2 = +0.7 ± 3.1 mW`; a fixed penalty of −227 mW attached to the
second slot would have appeared there in full and did not. The placement term is therefore
large for any one allocation and close to zero in the mean — a source of variance rather
than of bias. That makes it the best candidate yet for the ~100 mW between-run spread that
§12 attributes to a thermal and frequency state without naming a mechanism, and it is
stated here as a candidate rather than a finding: distinguishing them needs a session that
varies the mapping deliberately, with several allocations per condition.

**There is now a noise floor measured at effect scale.** Every A/A control in this chapter
sits at the harness's quiet floor, with a between-run SD of 4.6–10 mW, while the claims
they certify sit at around 2 W where the between-run SD is nearer 100 mW. The `sham`
control contrasts an operand against its own complement — both Hamming weight 16, both
homogeneous, so weight and distance are matched and the expected difference is zero — on
top of the full ~1.2 W of common load signal. It reads −0.174 W with a between-run SD of
0.094. Everything in the grid above at HW 8 and HW 32 clears that comfortably; the HW 1
and HW 2 rows at L1 and DRAM do not, and are not quoted individually anywhere in this
chapter.

### 8.3 A mixture does not inherit the cheap zero

Every buffer so far holds one repeated word, so it is either entirely zero or entirely
not. Real data — a post-ReLU activation tensor, a sparse matrix — is a *mixture* of zero
and non-zero words, and the step of §8 could reach a mixture in two ways that give
opposite answers for the application chapter:

- **per-transfer**: each zero word carries its own share of the discount, so a buffer
  that is a fraction *d* non-zero costs *d* times the fully non-zero one — a line through
  the origin in mean Hamming weight, and sparsity is exactly what power reports;
- **per-stream**: the discount belongs to the all-zero *stream* — an idle detector, a
  clock-gating condition that needs sustained zeros — and is lost as soon as any word is
  non-zero, so a mixture pays the whole step and then follows §8's line in its mean weight.

The measurement fills a `ws_l3_x8`-shaped buffer with 0x00000000 and 0xFFFFFFFF words at
density *d* ∈ {1/8, 1/4, 1/2, 3/4, 1} (fraction non-zero, so mean Hamming weight 32*d*) and
contrasts each against the all-zero buffer, exactly as §6 did
(`results/20260930-132043-phase1_sparsity_mixture`, 13 labels × 3 repeats, Config-A; PL1
200 W on mains in all 78 snapshots; A/A −0.013 W, SD 0.011).

**Mixing zero and non-zero words can add switching, and the design has to keep it out.**
§9 shows that toggling between consecutive transfers leaks at +34 mW per flipped bit, so a
mixture that alternates 0 and 0xFFFFFFFF from one transfer to the next would pay for the
alternation as well as for the weight, and its switching would read as a step. The session
therefore carries two placements at every density with the same mean weight. The *scattered*
arm spreads the non-zero words evenly, one at a time; the *blocked* arm groups them into
64-byte runs. **This design first assigned those two roles the wrong way round**, from the
Hamming distance between neighbouring 32-bit words, which is highest for the scattered
fill. But the data path moves 32-byte loads and 64-byte lines, and §9.1's two-point
comparison showed that toggling *between consecutive transfers* is what costs power. At
that granularity the fill check (`tests/fillcheck.c`, run against the victim's own fill
code) measures the scattered fill toggling **0 bits** at every density — its spread has a
period of one to eight words, so every load carries the identical 256-bit pattern and its
0/1 alternation is spatial, inside one transfer — while the blocked fill toggles 128, 256,
512 and 256 bits between consecutive lines at *d* = 1/8, 1/4, 1/2, 3/4. The scattered arm
is the switching-free one. The session was run as designed and read with the roles
corrected; the spec keeps its pre-registered decision rule as written, with a dated note
saying which victim it was read on.

On the scattered arm, against both readings:

| *d* | mean HW | Δ power | between-run SD | per-transfer: *d*·Δ(1) | per-stream: §8's line, rescaled |
|---|---|---|---|---|---|
| 1/8 | 4 | +0.485 W | 0.151 | 0.222 | 0.497 |
| 1/4 | 8 | +0.631 W | 0.261 | 0.444 | 0.680 |
| 1/2 | 16 | +1.140 W | 0.293 | 0.889 | 1.046 |
| 3/4 | 24 | +1.439 W | 0.275 | 1.333 | 1.411 |
| 1 | 32 | +1.777 W | 0.209 | 1.777 | 1.777 |

The per-stream column is §8's pooled line rescaled by 0.90 so that it meets this
session's own *d* = 1 point; sessions differ in overall level by about that much (§8.1's
anchor comparison) and it is the shape that separates the readings. Against it the
residuals are −0.01, −0.05, +0.09 and +0.03 W, with no trend. Against the per-transfer
line they are +0.26, +0.19, +0.25 and +0.11 W — all positive, largest where the density
is lowest, which is the per-stream signature.

**The mixture follows the single-word law in mean Hamming weight.** Fitted exactly as §6
fitted the weight sweep — one line per repeat against mean weight, via `analysis.hwfit
--axis density` — it gives +47.1 mW/bit (SD 9.1 over three repeats) and an intercept of
+304 mW, against §8's pooled +50.75 mW/bit and +349 mW and §8.1's L3 values of +49.0 and
+194. A buffer that is seven-eighths zero words costs what a buffer of one repeated
four-bit word costs.

**Per-stream is favoured, and three repeats do not close it.** Per-transfer predicts that
Δ(*d*)/Δ(1) = *d* in every repeat, and dividing by each repeat's own *d* = 1 point cancels
the run-to-run scale that dominates the unpaired spread. The mean excess over *d*, one
value per repeat, is +0.087, +0.064 and +0.201 — positive in all three, averaging +0.117,
where per-stream predicts +0.105 and per-transfer 0. That is *t* = 2.78 on two degrees of
freedom, *p* = 0.11 two-sided. Per-transfer is disfavoured and not rejected: this session
is noisier than the weight sweep — the same all-ones content that varies by 51 mW between
repeats there varies by about 200 mW here — and the static arm's own intercept, though
positive in every repeat (+213, +213, +486 mW), has a Student-*t* interval that reaches
zero. The weight of evidence is the agreement with §8's line, whose step is 23 standard
errors from zero, at every density; the single-session test alone is suggestive.

**What that changes.** Density is recoverable either way — the static arm rises
monotonically from +0.49 W at one-eighth non-zero to +1.78 W fully non-zero, a 1.3 W range
at L3 — so the application chapter's premise stands. What changes is which term carries
it and what it reads. If the step is per-stream, it separates an entirely zero buffer from
every other one and adds nothing to how sparse a non-zero buffer is; partial sparsity is
read through the per-bit weight term, and the quantity recovered is **mean bit density**,
not the count of zero words. The two coincide only when every non-zero word has the same
weight. A dense tensor of small values and a sparse tensor of large ones can have the same
mean weight, and the application chapter's axis has to be labelled accordingly.

**The switching arm sits above the static one everywhere, as it should.** Blocked minus
scattered is +0.13, +0.53, +0.34 and +0.38 W at *d* = 1/8 to 3/4, the same sign and order
of magnitude as §9.1's load- and line-path terms predict for the measured toggle rates
(0.20, 0.39, 0.78, 0.39 W). It is not resolved density by density: at *d* = 1 the two
victims hold bit-identical buffers and still differ by +0.16 W, which is the run-to-run
and placement floor that every row above sits on. Not tracking the prediction's peak at
*d* = 1/2 is within that floor, and §9.1's decomposition was itself an estimate from two
points.

**Genuinely random words leak more than the model predicts.** A third victim sets every
bit independently with probability *p*, so words differ from one another at a controlled
mean weight. Against the static arm at the same mean weight, plus §9's switching terms
scaled to its mean per-word distance of 64*p*(1 − *p*):

| *p* | random words | static arm | + switching | model | excess |
|---|---|---|---|---|---|
| 1/2 | +2.486 W (SD 0.012) | +1.140 | +0.675 | +1.815 | **+0.67 W** |
| 3/4 | +2.563 W (SD 0.043) | +1.439 | +0.506 | +1.945 | **+0.62 W** |

A consistent ~0.65 W, about a quarter of the total, that the weight-plus-distance model does
not account for. It is robust to how the switching is decomposed: taking §9's measured
+0.55 to +0.63 W at distance 16 in place of the decomposed terms leaves an excess of
+0.7 W at *p* = 1/2. §9's two terms were calibrated on operands that repeat one word or
alternate between two, and they under-predict data that varies freely. No mechanism is
claimed; the practical reading is that this chapter's coefficients are a lower bound on
what a victim processing real data leaks, not a prediction of it.

Two further checks. The effect stays on the core rail for every contrast in the session,
95–104% of the package difference in PP0 with the uncore difference inside ±0.03 W,
mixtures and random words alike, so §4.1's localisation extends past repeated words.
And `work_balance` fails in 17 of the 36 contrasting runs, at −2.2% to +3.8%; this is §8.2's
two-buffer placement term and not a work difference, because its sign flips (mean
+0.15%), it does not track the power effect (correlation +0.14), the single-buffer A/A
matches to 0.04%, and the lowest-density scattered runs carry the step with throughput
differences of +0.06%, −0.37% and −0.44% — no extra work at all.

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
| static Hamming weight, on a constant stream | +50.75 mW per set bit | **+349 mW step at zero**, at L3 |
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

## 10. Does the instruction matter?

The original plan for this chapter allocated most of its space to a per-instruction
leakage table. §3 is why that table cannot be built the way it was specified: the
register-resident instruction victims do not leak, so a table of their contrasts would
be a table of noise. The `ws_op_*` victims put the same instructions behind the
`ws_l3_x8` load stream instead — eight 32-byte loads per iteration from a 4 MB working
set, then eight independent operations on what was loaded, into a separate bank of
destination registers.

The load stream is byte-for-byte identical across the family, and both source registers
of each operation are the same loaded register, which makes the result Hamming weight
predictable and turns the family into a contrast rather than a list. `vpand` and `vpor`
carry a result that tracks the operand; `vpxor` and `vpsllvd` pin theirs at zero whatever
the operand is. Same input traffic, same instruction cost class, opposite result
behaviour.

The loop is meant to be load-bound so that every variant moves operands at the same rate.
That mostly held — a pre-flight put all nine variants within 4% of the load-only
baseline — but not exactly: over four repeats `vpand`, `vpor` and `vpaddd` settle at
140 GB/s against 147 for loads alone, a systematic 5% (within-victim SD ≈ 1 GB/s). So the
table is read in Δ pJ/byte, the same normalisation §4 uses, and rows are compared *paired*
— differenced against the reference row within each repeat — because every row carries
the same ~2 W of load traffic and the unpaired spread is dominated by run-to-run
variation in that shared term rather than by anything about the instruction
(`results/20260902-230608-phase1_instruction_table`, 13 runs × 4 repeats, 112/112 gates
pass; A/A −8.5 mW, sign flipping, accuracy 0.521).

| victim | instruction | Δ power | Δ pJ/byte | vs loads-only, paired | |
|---|---|---|---|---|---|
| `loads_only` | *(none)* | +1.943 W | 13.2 | — | reference |
| `op_shift` | `vpsllvd` | +1.849 W | 13.0 | −0.21 [−0.76, +0.34] | indistinguishable |
| `op_mov` | `vmovdqa` reg–reg | +2.036 W | 13.8 | +0.65 [−0.12, +1.41] | indistinguishable |
| `op_vnni` | `vpdpbusd` | +2.109 W | 14.6 | +1.37 [+0.09, +2.65] | differs |
| `op_mul` | `vpmuludq` | +2.195 W | 15.1 | +1.88 [+0.06, +3.70] | differs |
| `op_fma` | `vfmadd231ps` | +2.225 W | 15.2 | +2.03 [+1.43, +2.63] | differs |
| `op_add` | `vpaddd` | +2.176 W | 15.5 | +2.36 [+1.39, +3.34] | differs |
| `op_xor` | `vpxor` | +2.259 W | 15.6 | +2.45 [+1.36, +3.55] | differs |
| `op_or` | `vpor` | +2.290 W | 16.2 | +3.07 [+2.27, +3.87] | differs |
| `op_and` | `vpand` | +2.284 W | 16.3 | +3.16 [+1.18, +5.13] | differs |

Intervals are Student's *t* at three degrees of freedom, not the normal approximation,
which at four repeats would be optimistic by a third.

**The instruction matters, and by much less than the movement does.** Seven of the nine
rows sit above loads-only, but the whole spread of the table is 3.4 Δ pJ/byte against the
13.2 the loads alone already cost — so the choice of instruction moves the leak by 10–24%
where the choice of *where the operand comes from* moved it by 55× (§4). A leakage model
for this platform that captures operand movement and ignores the instruction is wrong by
about a fifth; one that captures the instruction and ignores the movement is wrong by
almost everything.

**The result Hamming weight explains none of it.** This was the contrast the family was
built for, and it comes back negative twice over. `vpand` and `vpor`, whose results track
the operand, sit +0.70 and +0.62 Δ pJ/byte above `vpxor`, whose result is pinned at zero —
intervals [−2.35, +3.75] and [−1.26, +2.49], both comfortably containing zero. And
`vpsllvd`, whose result is *also* pinned at zero, sits 2.66 Δ pJ/byte **below** `vpxor`
[−3.87, −1.45]. A result-driven account would have to put those two together, and the
data separates them by more than it separates either from the operand-tracking pair. The
ordering is a property of the instruction, not of what it produces — which is what §3
predicts, since the results never leave the register file.

### 10.1 Register-resident leakage is instruction-dependent

§3 established that register-resident operands do not leak, and it established that for
`vpmuludq`. That does not generalise, and this experiment is what shows it. Two
register-resident controls, paired against the session's A/A:

| victim | instruction | Δ power vs A/A | detector |
|---|---|---|---|
| `avx2_mul` (§3) | `vpmuludq` | ≈0 (−0.06 W against an A/A of −0.06 W) | 0.57 |
| `reg_fma` | `vfmadd231ps` | **+0.045 W** [+0.021, +0.069] | 0.66 |
| `reg_vnni` | `vpdpbusd` | **+0.200 W** [+0.151, +0.249] | 0.94 |

`vpdpbusd` leaks a fifth of a watt with the operand never leaving a register, at a
detector accuracy of 0.94. So the correct statement is not that register-resident
operands never leak, but that **whether they leak depends on how much the execution unit
does with them**: `vpmuludq` performs four 32×32 multiplies per 256-bit vector and is
null, `vfmadd231ps` is marginal, and `vpdpbusd` — thirty-two 8-bit multiplies and an
accumulation per vector — is unmistakable. §3's conclusion stands for the victim it was
measured on and needs this qualification to be stated generally.

That matters beyond bookkeeping. `vpdpbusd` is the instruction quantized int8 inference
actually issues on this part, and it is the one instruction in the set that leaks without
any memory traffic at all. The application chapter therefore has two independent channels
into the same victim rather than one.

Two smaller observations, both worth recording and neither worth leaning on at four
repeats. VNNI's ALU term looks additive — +0.200 W register-resident against a +0.166 W
raw increment over loads-only when traffic is present — while FMA's does not, at +0.045 W
against +0.282 W. And `op_mov`, a register-to-register move, is the one traffic-bearing
row that adds nothing measurable, which is consistent with the rest of the chapter: a
move that stays inside the register file is not movement in the sense that leaks.

## 11. Bit placement, tested and not resolved

An earlier version of this section reported that bit placement matters at fixed Hamming
weight — that at equal weight two patterns differ by up to 0.16 W — from a heuristic that
flagged a weight whenever the spread between its patterns exceeded twice the between-repeat
SD. Pooling the two sweep sessions gives eight weights with more than one pattern, and the
heuristic fired at two of them, HW 2 (spread 0.102 W) and HW 8 (0.162 W). That was not a
test, and it does not survive being made into one.

**The three-repeat design could not resolve placement, in either direction.** Patterns at a
weight were interleaved across the same three repeats, so a repeat index is a session-wide
thermal state shared by every pattern, and the powerful comparison is the paired one:
difference the patterns repeat by repeat, cancelling that state. Done so, the two flagged
weights are the two with the *largest* paired separation — HW 2 at *t* = 5.3, HW 8 at
*t* = −3.9, each consistent in sign across its three repeats — so the heuristic was
tracking signal, not noise. But an exact paired sign-flip test on three repeats cannot
return a *p* below 0.25 however clean the effect (there are only 2³ sign assignments), and
across eight weights the Šidák-corrected smallest *p* is 0.90. The original claim rested on
a design whose best possible result was "not significant."

**Six repeats at HW 8 resolve it, and the answer is no.** HW 8 carried the larger and
cleaner of the two hits, so it is the one to settle. Four patterns spanning the placement
axis — the two original scattered operands `0x0180D203` and `0xD004B080`, eight contiguous
bits `0x000000FF`, and one bit per nibble `0x11111111` — each contrasted against the
all-zero baseline exactly as §6's sweep did, over six repeats
(`results/20260930-155653-phase1_placement` and its top-up
`results/20260930-162959-phase1_placement_topup`; anchor +1.126 W against the corpus's
+1.133/+1.215/+1.228/+1.160; A/A −0.006 W). The four pattern means are +0.745, +0.780,
+0.696 and +0.701 W. Their spread is **0.083 W — now below the effect-scale noise floor**
the `sham` control sets at 0.094 W (§8.2). An omnibus permutation test that exchanges the
pattern labels *within each repeat*, so that it cancels the between-repeat state exactly as
the paired reading does, returns **p = 0.10** — not significant, on a design whose paired
floor at six repeats is 0.031 and which therefore had the power to find a real term of this
size.

**And the hit that motivated the claim did not replicate.** `hw08_a` against `hw08_b` read
−0.162 W over three repeats and was consistent in sign; over six it is −0.035 W, and the
sign is no longer consistent — the two added repeats came in at +0.077 and +0.015 W, the
opposite direction — for a sign-flip *p* of 0.31. The original figure was the tail of a
three-sample draw, and a design that could not have distinguished it from zero happened to
land on a large value twice.

Hamming weight is therefore the predictor of this leakage, and no bit-placement term is
established at fixed weight: what looked like one is below the noise floor and does not
survive a test with the power to see it. This is placement of bits *within the operand*; it
should not be confused with the placement of the *buffer in memory* of §8.2, which is a
separate effect, real, and about 230 mW — the `sham` control's −0.174 W carries that term,
not this one, which is why it is quoted there as a noise floor and not here as a result.

## 12. Threats to validity

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

**At DRAM depth the two conditions do not do quite the same amount of work.** Every
operand claim in this chapter assumes the conditions differ in *which* bits move and not
in how many bytes move, and until the depth × operand session that assumption was argued
architecturally — the loop is the same loop, the buffer is the same size — rather than
measured. It is now measured per condition, and at DRAM it is false: across that session's
twelve DRAM runs the heavier operand achieves **+2.08% more throughput on average**, with
eleven of the twelve outside the 1% gate and the sign never flipping. L1, L2 and L3 sit
inside the gate. The affected rows are the ones with the highest Δ pJ/byte in the chapter,
so the qualification matters where the numbers are largest: some unknown fraction of the
DRAM effect is a work difference rather than an operand difference, bounded above by the
throughput gap and almost certainly well below it, since package power does not scale
one-for-one with achieved bandwidth. Resolving it properly needs a victim whose byte count
is invariant to the operand by construction, which the current `ws_*` design does not
guarantee. No claim in this chapter rests on the DRAM row alone; the depth ladder is
monotone with or without it.

The same gate caught one contaminated run. `l2_hw08_r2` was interrupted by a machine
suspend and ran for 8595 s against 47.1 s for every other run in its session, with the
stall landing in its all-zero condition; it reports 199% throughput imbalance. It is left
in the session so the gate fails visibly rather than being deleted, and it is excluded
from every figure quoted here — the L2 slope is +23.68 mW/bit over the two clean repeats
against +25.99 over all three, which changes nothing in §8.1.

**The interleaving gate bounds a unitless number, so it is now backed by one in watts.**
The gate fails a run whose conditions sit more than a set distance apart in mean
chronological position, and that distance was for a long time never converted into the
quantity it exists to control: how much of the run's drift can reach the difference. Both
halves are measured, so the product is available — a condition sitting a fraction *f* later
in the run than the other picks up *f* of whatever the run drifted by — and
`analysis.report` now both prints it per run as an admitted-bias figure and **gates on
it**. A contrast reported as significant must carry an effect at least three times its
admitted bias; below that, the drift the design lets through is too large a fraction of the
effect for the effect to be trusted on its own. A null has no effect to protect and an A/A
is covered by its own gate, so the check applies only to a significant A/B contrast.

Over the 269 Phase 0 and Phase 1 runs the median admitted bias is **6.0 mW**, against a
median effect 75× larger, so the three-times bar passes every positive result in this
chapter and nothing is threatened by it — no run in the committed corpus fails it. The
margin is not uniform, though, and it is thinnest where it matters most. The Hamming-weight
session drifted 2–3 W within a condition, so its low-weight rows admit 60–90 mW: `hw01`
carries 81 mW of admitted bias against its +458 mW effect, and `hw04_a` 61 mW against
+513 mW. Ratios of 5.7 and 8.4 clear the gate and are decisive, but they are the narrowest
in the chapter, and they sit under the §8 discontinuity — 80 mW is 23% of the +349 mW step.
The step survives comfortably; the honest statement is that its error budget is dominated by
drift admitted through the interleaving window rather than by the between-repeat spread
quoted beside it, and that a session carrying these same effects but twice the drift would
trip the gate.

The gate behaves as a gate should on the run built to fail: `phase0_artifact_demo`'s
deliberately sequential A/A admits 302 mW, two orders of magnitude above the median, and
fails the interleaving gate outright — the design defect the gate was written to catch,
now expressed in watts as well as flagged.

Those figures are for the Config-A sessions this chapter reports. A single fixed imbalance
threshold cannot serve both configurations, because a given imbalance buys one to two orders
more drift when the part is free to throttle. `phase2_tier2_feasibility`, the one Config-B
driver session in the corpus, drifts 29–48 W within a condition at an imbalance of 0.093, so
it admits 2.7–4.4 W of bias against effects of 1.3–2.8 W — every run in it has an effect
*smaller* than its own admitted bias. Under the old single 0.10 threshold it passed by 7%.
The imbalance gate is now tightened to 0.05 under Config-B, which that session fails, so the
point the chapter can now make — that the session could not have resolved anything either
way — is enforced rather than noted after the fact.

**Victim cores are not isolated.** The kernel is booted with `isolcpus=0`, which isolates
the monitor core only; victim cores 2, 4, 6, 8 and 10 still receive stray system work.
Extending the isolation requires a boot-parameter change and has not been done.

**The detector is a mean threshold.** It converts an effect into an accuracy-versus-*n*
curve, but it is blind to effects that live in variance rather than mean — and its
absolute-threshold, trained decision is not the paired, training-free one the covert
receiver makes, so its curve, read directly, overstates the achievable bit rate. §13
recomputes the conversion under the receiver's own rule.

**Cache residency is inferred, not measured** (§4), and the DRAM figure excludes DIMM
energy.

**The lowest distance point is at the noise floor.** At HD 2 the effect is +0.086 W with
a between-run SD of 0.141 and the sign flipping across repeats — the fitted line predicts
+0.068 W there, which this design cannot resolve from zero in three repeats. It is
included in the fit and reported as measured, but nothing rests on it; dropping it moves
the slope by less than its own standard error.

**The instruction table's rows are not perfectly traffic-matched.** The design intends
the loop to be load-bound so that only the instruction varies, and it is within 5% — but
`vpand`, `vpor` and `vpaddd` settle systematically at 140 GB/s against 147 for loads
alone. §10 therefore reports Δ pJ/byte rather than watts. The correction works against the
finding rather than for it: those three rows move *less* data and still leak more, so
normalising enlarges their excess. The one row the normalisation changes qualitatively is
`vpsllvd`, which is 0.094 W below loads-only in raw watts and indistinguishable from it
per byte.

**The load-path/line-path split of §9.1 is an estimate, not a measurement.** It rests on
two points and an assumption that the two contributions add linearly, which nothing here
tests. The ordering it implies — the fast narrow path dominating — is consistent with the
relative rates, but a third alternation period would be needed to check the model rather
than assume it.

**The coefficients were calibrated on degenerate operands.** Every weight and distance
figure in §6–§9 comes from a buffer holding one repeated word or two alternating ones.
§8.3 tests what happens when the words genuinely differ, and the model falls short by
about 0.65 W at L3 — a quarter of the total — on random data at two bit densities. The
coefficients are therefore a floor on real-data leakage rather than a prediction of it,
and any combined model built from them has to be checked against, or recalibrated on,
data that varies freely before it is used to predict a real victim — which §12.1 now does.

**One machine, one microarchitecture.** Everything here is an i7-12700H at 2.3 GHz with
no AVX-512. Nothing in this chapter establishes that the coefficients transfer.

### 12.1 Where the model breaks down

The four characterisations above are assembled into one predictor by `analysis.model` —

    dP = f(depth)·(step·1[op≠0] + α·meanHW + β·meanHD) + γ(instruction)

— and the script does two things no earlier section did: it confirms the combined fit
reproduces every per-section coefficient it is built from, and it predicts victims the fit
never saw. Three results bound where the model may be trusted.

It is portable across sessions for the operands it was calibrated on. The weight law
predicts the recurring HW-16 anchor at +1.161 W against a measured +1.172 W pooled over
five independent sessions — a residual of +11 mW — and the HW-32 point to within −0.109 W.
The per-bit *slope* in particular is portable (the depth session's L3 slope +48.96 mW/bit
against the pooled weight sweep's +50.75); the zero-*step* is the loose coefficient,
+194 mW in the depth session against +349 mW pooled, which is why this chapter quotes the
step with the widest error bar of any figure it reports.

It predicts static mixtures of those operands within the noise floor: across the five
densities of §8.3's scattered arm the weight law's residual is −0.02 to −0.20 W, inside or
just past the 0.094 W effect-scale floor of §8.2.

It under-predicts data whose words genuinely differ, by a known amount and in a known
direction. On the i.i.d. victim at mean bit density one-half the model predicts +1.84 W
against a measured +2.49 W — a residual of +0.65 W, about a quarter of the signal,
reproducing §8.3's independently-computed excess. The cause is structural rather than a bad
fit: every calibration victim held all eight 32-bit words of a 32-byte load identical, so
the fitted switching term β counts only the flips *between* consecutive loads; i.i.d. data
also flips bits word-to-word *inside* a load, and that activity is nowhere in the training
set. The combined model is therefore a floor on real-data leakage, exact to tens of
milliwatts on single-word operands and low by a quarter on high-entropy ones — and the one
measurement that would turn the floor into a two-sided prediction is a held-out victim at
an (HW, HD) combination never run, predicted in advance and then measured.

## 13. What this chapter establishes

A quantitative leakage model for operand movement on this platform:

- The leaking element is primarily the movement of the operand, not the arithmetic
  performed on it. A victim doing only loads leaks; a victim doing only register-resident
  multiplies does not. Which instruction consumes the operand shifts the leak by 10–24%,
  against the 55× that the operand's *depth* shifts it — but the instruction term is not
  zero, and for `vpdpbusd` it survives with no memory traffic at all.
- The *operand-dependent* cost per byte moved rises monotonically with the depth the
  operand is drawn from, 0.319 Δ pJ/byte at L1 to 17.598 at DRAM once each depth's own
  zero-step is removed. Absolute power difference peaks at L3. These are differences
  between two operands, not the cost of moving a byte.
- Within a fixed victim, the difference is linear in the operand's Hamming weight at
  +50.75 mW per set bit per 32-bit word (R² = 0.967 over 18 operands from weight 1 to 32),
  on top of a **discontinuity between weight 0 and weight 1** that belongs to the operand
  rather than to the contrast. An all-zero operand is cheap out of proportion to its
  weight; at L3 a single set bit per word costs eight times what the next bit costs.
- A mixture of zero and non-zero words follows the same line in its *mean* Hamming weight
  (+47 mW/bit, intercept +304 mW, against +50.75 and +349): a buffer that is seven-eighths
  zero words costs what a buffer of one repeated four-bit word costs. The cheap zero is
  therefore favoured as a property of an all-zero *stream* rather than of each zero word —
  per-transfer is disfavoured at *p* = 0.11 over three repeats, not rejected — so density
  is recoverable from power through the per-bit weight term, and what it recovers is mean
  bit density rather than the number of zero words.
- The weight-plus-distance model under-predicts data whose words genuinely differ: random
  words at a controlled bit density leak about 0.65 W more than the model allows, a quarter
  of the total, at two densities. Every coefficient here was calibrated on one repeated
  word or two alternating ones, and is a lower bound on what real data leaks.
- Weight and depth are not separable. The step at zero is +349 mW at L3 but −60 mW at L1
  and +52 mW at DRAM, and the weight slope runs 7.2 / 26.0 / 49.0 / 22.5 mW per bit across
  L1 / L2 / L3 / DRAM, so a combined model needs the product of the two terms and not
  their sum. This is the chapter's only measured interaction; every other result varies
  one factor with the rest held fixed.
- With weight held fixed, the difference is also linear in the Hamming *distance* between
  consecutive transfers, at +34.14 mW per flipped bit (R² = 0.974) — and this line does
  pass through the origin. Neither term reduces to the other: each was measured in a
  design where the other predicts exactly zero.
- The result the instruction computes contributes nothing measurable. Operations whose
  result tracks the operand and operations whose result is pinned at zero are
  indistinguishable, and two operations that both pin their result at zero are among the
  furthest apart in the table. Results stay in the register file, and the register file
  does not leak.
- Register-resident leakage is instruction-dependent: null for `vpmuludq`, +0.045 W for
  `vfmadd231ps`, +0.200 W for `vpdpbusd` at detector accuracy 0.94. The more the
  execution unit does per operand bit, the more it leaks with no traffic at all.
- Bit placement *within the operand* does not measurably matter at fixed weight. A
  heuristic once suggested a term of order 0.1 W; a six-repeat test at HW 8 with a proper
  permutation test puts the spread between four patterns at 0.083 W, below the noise floor,
  at p = 0.10, and the one hit that motivated the claim collapsed from 0.16 W to −0.03 W
  and flipped sign (§11). A *different* placement effect is real and larger: two buffers
  holding bit-identical data at different addresses differ by about 230 mW (§8.2). The
  polarity control bounds its mean at +0.7 ± 3.1 mW, so it is a variance term rather than a
  bias, and it is the leading candidate for the ~100 mW between-run spread this chapter
  reports throughout.

These coefficients are not only tabulated but assembled into a single predictor
(`analysis.model`) and tested against victims outside the fit. It reproduces every
coefficient it is built from, predicts single-word operands across five independent
sessions to within 11 mW, predicts static mixtures of them within the noise floor, and
under-predicts high-entropy data by a known +0.65 W. Phase 1 therefore delivers a leakage
*model* with a stated domain of validity and a measured out-of-sample error, not only a
list of coefficients valid at the points where each was taken.

For the chapters that follow, the operationally important number is not the largest
effect but the best-conditioned one. Converting an effect into a bit rate needs the
*receiver's* decision rule, though, and the mean-threshold detector of §2 is not it: it
fits an absolute threshold on held-out blocks and reads one window against it, whereas the
covert receiver of the next chapter carries no training data and makes a **paired**
decision — differencing the two chips of one Manchester symbol and taking the sign, which
is what lets it survive drift (Phase 2 §6). Quoting the threshold detector's curve as a
bit rate, as an earlier draft of this section did, measures a rule the receiver does not
use. `analysis.detector` recomputes the curve under the rule it does.

Two things change, and both lower the rate rather than cancelling. A Manchester bit spends
two chips, so the raw rate is 1/(2·n·T) rather than 1/(n·T); the paired decision needs
somewhat fewer samples per chip for the same accuracy — its per-chip requirement is d′/√2
against the threshold detector's d′/2 — but not few enough to offset the doubled chip count,
so the receiver-rule rate comes out roughly half the threshold detector's. Pooled over
the Hamming-weight sweep (one curve per operand, repeats averaged), `ws_l3_x8` under
Config-A reaches 95% per-bit accuracy at **100–250 bit/s raw** under the receiver's rule,
against 125–501 under the threshold detector, at a ~1 ms RAPL period — while `ws_dram_x8`,
which has the highest energy per byte in the whole set, needs substantially more integration
for the same accuracy (reaching 95% only around 39 bit/s receiver-rule). Largest Δ power and
best detectability are still not the same property, and the covert-channel chapter picks its
transmitter on the second.

Both figures are steady-state ceilings, and so upper bounds: the operand is held for a
whole block, so neither sees the ~1 ms RAPL boxcar that Phase 2 §8 measures as removing
about two-thirds of the separation at a 1 ms chip. The cleanest check is the all-zero ↔
all-ones contrast that is also the tier-1 transmitter's: in its best-conditioned session it
reaches 95% at a single sample per chip, i.e. **500 bit/s raw** under the receiver's rule
(1000 under the threshold detector). That 500 is exactly tier 1's measured 2 ms-symbol
ceiling, and once the boxcar is paid it delivers the 241–311 bit/s of *capacity* Phase 2
actually reports. The receiver-rule conversion is therefore both the right quantity and, unlike
the threshold detector's ~1000 bit/s, in the range the channel is observed to carry.
