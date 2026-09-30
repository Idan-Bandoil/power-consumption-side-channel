# Recovering Sparsity: Leakage from Machine-Learning Inference

*Draft — Phase 3 chapter, **methodology skeleton**. Unlike the Phase 0–2 chapters, the
results sections here are not yet written over measured data: the victims, experiments and
analysis exist (`util/victim-utils.c`, `experiments/phase3_sparsity.json`,
`experiments/phase3_vnni_sparsity.json`, `analysis/sparsity.py`) but have not yet been run.
Every table headed **PENDING** is a placeholder that names the run directory it will cite
and the quantity it will report; every number stated as a prediction is labelled as one.
The design, method and hypotheses below are finished prose. All measurements are planned
under Config-A (turbo disabled, frequency pinned) unless stated otherwise.*

## 1. What this chapter asks

The proposal set three goals: a covert channel (Chapter 2 in this thesis, Phase 2), a
characterisation of the AVX-power relationship (Chapter 1, Phase 1), and the inference of
secret data — "specifically matrix properties" — from a co-located program. This chapter
is the third. The victim is real inference work; the attacker is a separate process that
shares no memory with it and observes only package power on another core.

The proposal's own framing of goal (2) was to "extract as much information as possible
about a matrix." That is not achievable in the time and at the effect size this platform
offers, and pretending otherwise would be the wrong kind of ambition — at the ~5% power
modulation Phase 1 measured, reconstructing matrix *content* is out of reach. This chapter
narrows the target to a *property* of the data that is both recoverable and consequential:
**sparsity** — the fraction of the operand stream that is zero. The narrowing is a
deliberate, defended scope decision, not a retreat, and §2 argues it is the right property
to have chosen.

Two results from Phase 1 make sparsity the natural target rather than an arbitrary one:

1. **The all-zero operand is anomalously cheap** (Phase 1 §8). Leakage is linear in
   operand Hamming weight at +50.75 mW per set bit, but the line does not pass through the
   origin: there is a discontinuity at the boundary between weight 0 and weight 1, so the
   *first* set bit costs several times what each subsequent bit costs. A channel built like
   that reports the *presence* of non-zero data far more loudly than a linear model would.
   Sparsity is precisely the property such a channel is best at reading.
2. **`vpdpbusd` leaks with its operand never leaving a register** (Phase 1 §10.1). The
   int8 dot-product instruction that quantized inference issues on this part is the one
   instruction in the set with a register-resident channel, so the application has two
   independent ways into the same victim: the movement of the activation stream, and the
   compute the MAC array does on it.

The chapter therefore asks two questions, one per channel:

- **§5:** Given an operand stream at a controlled density, is that density recoverable from
  package power — as a classification of the sparsity level and as a regression of the
  density itself?
- **§6:** Does the int8 dot product draw measurably differently when more of its input is
  zero, on top of the movement channel — i.e. is activation sparsity visible through the
  MAC array specifically?

## 2. Scope, and why sparsity is the right property

**Sparsity is not a toy target; it is where real inference leaks.** Post-ReLU activations
in a trained network are 50–90% zero, and — this is what makes them worth attacking — the
zero pattern is *input-dependent*: a blank input and a busy one produce activation tensors
of very different density at the same layer. Sparsity therefore carries information about
the input and about the model's internal state, and it is exactly the quantity Phase 1's
cheap-zero discontinuity is tuned to detect. The proposal's preliminary Eigen/TensorFlow
figures — which this project could not reproduce because their code was never committed
(confirmed absent from git history) — were sparsity figures. Recovering sparsity rigorously
is the honest reconstruction of that lost result.

**What is in scope for this chapter:**

- Recovery of the *density* of an operand stream, as both a level classifier and a
  continuous regression (§5).
- The activation-sparsity channel of the int8 dot product specifically, separated from the
  movement channel (§6).

**What is explicitly de-scoped, and why:**

- *Matrix content recovery.* Out of reach at this effect size in this time budget; stated
  as a scope decision rather than attempted and failed.
- *Model fingerprinting* (classifying which of several networks is being served from the
  power trace over time) and *input-class inference on real images.* Both are in the
  working plan and both are deferred: they require either a real inference framework (this
  machine's analysis environment is deliberately numpy-only — no PyTorch, ONNX Runtime or
  oneDNN) or a new measurement mode that samples an external process, and neither is built.
  They are the plan's own first-to-drop items. §8 records them as future work rather than
  gaps in a claim.

The consequence of that de-scoping is that this chapter's victims are **synthetic operand
streams at controlled density**, not a running neural network. §7 is candid about the gap
that opens between "sparsity is recoverable from a controlled stream" and "sparsity is
recoverable from PyTorch"; the controlled stream is what buys the rigor — an exactly known
independent variable and a work-balanced contrast — and the gap to real inference is named
rather than hidden.

## 3. Method common to every experiment

This chapter inherits the method of Chapter 1 unchanged, and the reader is referred there
for the detail: every experiment is a set of runs; within a run two conditions are
interleaved as short blocks in seeded-shuffled order so drift is common-mode; each
experiment is repeated at least three times with run order reshuffled; between-repeat
spread, not a single run's bootstrap interval, is the error bar; and every session ships
an A/A control and an anchor run. The same automated gates apply and the analysis exits
non-zero if any fails — zero-energy samples ≤1%, temporal imbalance ≤0.10, and the A/A's
interval containing zero at detector accuracy ≤0.60. Config-A holds frequency fixed, so the
`frequency_balance` gate applies.

One property of the design is specific to this chapter and is what makes it a clean
sparsity measurement rather than a repackaged Hamming-weight sweep.

**Work is balanced across densities by construction.** The victim streams a fixed-size,
L3-resident working set with a fixed load pattern; the density knob changes only *which
bytes are in the buffer*, never how many bytes are moved, which addresses are touched, or
how many instructions issue. Two conditions at different densities are therefore a contrast
in operand *content* alone, and the `work_balance` gate — the assumption every operand
claim in Chapter 1 rests on — holds here by the same argument, now one level up. This is
the same discipline that let Chapter 1 attribute an effect to the operand rather than to
the amount of work; it carries over exactly because density was designed not to move the
byte count.

### 3.1 The density-controlled victim

The Phase 1 working-set victims (`ws_l3_x8` and family) fill their buffer with a single
repeated 32-bit word taken from the selector. The Phase 3 victims (`ws_sparse_l3_x8`, with
L2 and DRAM variants for a depth cross) reuse that victim's load stream unchanged — eight
32-byte loads per iteration from a 4 MB working set — but fill the buffer to a *density*
instead: a fraction *f* of the 32-bit words carry a nonzero pattern and the rest are zero,
spread uniformly across the buffer by a Bresenham accumulator so that every streamed
256-byte read sees a representative mix rather than a run of zeros followed by a run of
non-zeros. Because only the C fill path is new and the inline-asm load loop is the byte-
for-byte identical one Phase 1 validated, the victim inherits Phase 1's throughput
behaviour and its `make check` selector-observation guarantee.

The selector encodes the density: its low 16 bits are the number of nonzero words per 1024
(0 to 1024), and its high 32 bits are the nonzero pattern, with zero taken as the default
all-ones pattern `0xFFFFFFFF`. Two buffers are cached, one per selector value seen, so a
condition switch during a run is a pointer swap rather than a refill — the same mechanism
Phase 1 used to keep buffer-fill traffic out of the measurement window.

**The all-ones pattern makes this a Hamming-weight measurement in disguise, deliberately.**
When the nonzero pattern is `0xFFFFFFFF`, a word is either weight 0 or weight 32, so the
*mean* Hamming weight per word is exactly density × 32. A pure weight model therefore
predicts dP as a linear function of density with a slope fixed by Chapter 1's +50.75
mW/bit, which is a strong, quantitative prediction the experiment can confirm or break
(§4). It also means the density sweep and the Chapter 1 weight sweep can be placed on the
same axis and cross-checked directly — the density victim at density 1.0 is bit-identical
to `ws_l3_x8` holding `0xFFFFFFFF`, which is the chapter's anchor to Phase 1.

## 4. Predictions

Stating the predictions before the measurement is deliberate: the analysis is written to
adjudicate between named models, not to fit whatever curve appears. Three models are on the
table, and they make different claims about the shape of dP against density *d*.

**Model A — linear in mean Hamming weight.** If leakage is the Phase 1 weight effect and
nothing else, and if a mixed stream leaks as the mean of its per-word contributions, then
with the all-ones pattern

  dP(d) ≈ 50.75 mW/bit × (32 d) = **1.62 · d watts**,

a straight line from the origin reaching ≈1.62 W at d = 1. Concretely: ≈0.16 W at 10%
density, ≈0.41 W at 25%, ≈0.81 W at 50%, ≈1.62 W at 100%.

**Model B — per-word zero-step.** Phase 1 §8 found the zero *value* cheap out of
proportion to its weight, with the whole step at the 0 → 1 boundary. If that step is a
per-word property, every nonzero word in the mixed stream carries it, and low-density
streams — which are almost all zero words with a few heavy ones — should sit *above* Model
A's line at the sparse end: the presence of any non-zero data is what the channel reports
loudest. This is the model the application cares about, because it is the one under which
high sparsity is easy to detect.

**Model C — weight plus distance.** A sparse stream does not only raise mean weight; it
also raises the Hamming *distance* between consecutive words, because a 0 → pattern
boundary flips bits. Phase 1 §9 measured distance leakage at +34.14 mW per flipped bit.
The observed slope should therefore sit *above* Model A's pure-weight prediction by the
distance term's contribution, which depends on the alternation structure the Bresenham
fill produces.

The experiment distinguishes these. The predicted numbers above are the null (Model A);
the scientific content is the sign and size of the departure from it, which selects B, C or
their combination. **No claim is made here as to which; that is what the run decides.**

> **Note, 2026-09-30 — partly decided already, by critique E2.** The Phase 1 session
> `results/20260930-132043-phase1_sparsity_mixture` ran this chapter's density victim
> (`ws_sparse_l3_x8`) at *d* = 1/8, 1/4, 1/2, 3/4, 1 as Phase 1 §8.3, before this
> chapter's own sessions. On that victim:
>
> - **Model C's distance term does not exist for this fill.** The Bresenham spread has a
>   period of 1–8 words at every density swept, so every 32-byte load carries the same
>   256-bit pattern and nothing toggles between consecutive loads or lines
>   (`tests/fillcheck.c`, measured on the victim's own fill code). The 0 → pattern
>   boundaries the model counts are inside one transfer, not between transfers, and §9 of
>   Phase 1 prices switching between transfers. Model C reduces to A plus nothing here; a
>   fill that groups zeros into lines (`ws_sparse_l3_x8_blk`) is what does add switching.
> - **Model B's shape is what the data show, and Model A's line through the origin is
>   disfavoured.** The mixture follows Phase 1's single-word line in *mean* Hamming
>   weight — +47 mW/bit with a +304 mW intercept — so the lowest densities sit well above
>   1.62·*d* (+0.49 W at 1/8 against 0.20). A paired per-repeat test disfavours the
>   through-the-origin reading at *p* = 0.11 over three repeats; it does not reject it.
> - **Read B for what it is.** The step is paid once, by any non-all-zero stream. It makes
>   *all zero* versus *anything else* loud, and adds nothing to telling 50%-sparse from
>   90%-sparse — the range post-ReLU activations actually occupy. That range is read
>   through the per-bit weight term, and what it recovers is **mean bit density**, which
>   equals sparsity only when every non-zero word has the same weight. §2's framing and
>   this chapter's axis labels should follow that.
> - **Random words leak ~0.65 W more than the weight-plus-distance model predicts** (Phase 1
>   §8.3), so any predicted number in this chapter derived from Phase 1's coefficients is a
>   floor rather than an expectation.
>
> This chapter's own sessions remain to be run; the note only records which of its
> predictions an earlier session has already tested, so the draft does not contradict
> Phase 1.

## 5. Experiment 1 — sparsity-level recovery

**Design.** `ws_sparse_l3_x8` under Config-A. Condition 0 is density 0 (an all-zero
stream) in every run; condition 1 is the test density, so each run reads as "power at
density *d* against an all-zero stream", exactly as Chapter 1's weight sweep read against an
all-zero operand. Nine densities span the range with resolution concentrated at the
high-sparsity (low-density) end that inference actually occupies: 0, 1%, 5%, 10%, 25%, 50%,
75%, 90% and 100% (`experiments/phase3_sparsity.json`, 9 runs × 3 repeats). Density 0 is
the A/A gate; density 100% is the anchor to Phase 1's `hw32`.

**What will be reported (PENDING — `results/<phase3_sparsity>`):**

| density | sparsity | mean HW | Δ power vs all-zero | between-run SD | detector |
|---|---|---|---|---|---|
| 0.00 | 1.00 | 0.0 | *(A/A)* | | |
| 0.01 | 0.99 | 0.32 | | | |
| 0.05 | 0.95 | 1.6 | | | |
| 0.10 | 0.90 | 3.2 | | | |
| 0.25 | 0.75 | 8.0 | | | |
| 0.50 | 0.50 | 16.0 | | | |
| 0.75 | 0.25 | 24.0 | | | |
| 0.90 | 0.10 | 28.8 | | | |
| 1.00 | 0.00 | 32.0 | | | |

Read by `analysis.sparsity`, which additionally:

- fits dP against mean Hamming weight once per repeat and reports the slope in mW per
  mean-set-bit, to be compared against Phase 1's +50.75 mW/bit — the cross-check that this
  is the same leakage one level up rather than a new artifact (a slope at or above 50.75
  selects Models B/C over A);
- runs a **leave-one-repeat-out nearest-centroid classifier** over the density levels,
  using per-block power baselined against each run's own density-0 condition so that
  run-level drift is removed and the samples are comparable across runs. Leaving out a whole
  repeat rather than random blocks keeps the run-level thermal state — Chapter 1's real
  error bar — out of the training fold. It reports the confusion matrix, overall accuracy,
  and the level MAE in density points, for a few observation lengths (blocks averaged per
  decision), which is the sparsity analogue of the covert channel's accuracy-versus-*n* to
  bit-rate curve.

**Expected confusion structure (prediction, not result):** far-apart densities separate
cleanly and adjacent ones (10% vs 25%, say) confuse, with the confusion concentrating in
the dense end and the sparse end well resolved if Model B holds. The honest deliverable is
the confusion matrix itself, which shows exactly which sparsity levels are and are not
distinguishable at a given observation length — that structure is the result, not a single
headline accuracy.

**Negative control.** The density-0 A/A must decode at chance and pass the gate, as every
A/A in this thesis has; a classifier that reports structure there is reading the harness.

## 6. Experiment 2 — activation sparsity through the int8 MAC

**Design.** Two victims measured in one interleaved session so they can be read paired:
`ws_sparse_op_vnni` (the sparse activation stream followed by eight `vpdpbusd` per
iteration) and `ws_sparse_op_mov` (the identical stream followed by eight register-to-
register `vmovdqa`, i.e. movement with no compute). The load stream, addresses and op count
are identical across densities and across the two victims, so the paired difference
dP(vnni, *d*) − dP(mov, *d*) per repeat is the compute channel with the movement channel
differenced out — the same paired reading Chapter 1's instruction table used against its
loads-only reference. Six densities (0, 10%, 25%, 50%, 75%, 100%) against the doubled victim
count (`experiments/phase3_vnni_sparsity.json`, 12 runs × 3 repeats). Density 100% anchors
`vnni` to Phase 1's `ws_op_vnni` (+2.109 W) and `mov` to its `loads_only` (+1.943 W).

For this first pass the activation pattern is the default all-ones; `vpdpbusd` treats
operand *a* as unsigned and *b* as signed and both sources are the same loaded word, so
`0xFF` bytes give products of 255 × (−1) and the per-burst accumulator stays well inside
int32 — the exact case Phase 1's instruction table already ran. A follow-up can set a small
realistic int8 activation pattern and a fixed dense weight through the selector's high bits
with no code change; §8 records that as the obvious refinement.

**What will be reported (PENDING — `results/<phase3_vnni_sparsity>`):**

| density | movement dP (mov) | compute dP (vnni − mov, paired) | between-run SD | verdict |
|---|---|---|---|---|
| 0.00 | *(A/A)* | | | |
| 0.10 | | | | |
| 0.25 | | | | |
| 0.50 | | | | |
| 0.75 | | | | |
| 1.00 | | | | |

**Prediction.** The movement channel (both victims) should rise steeply with density, as
in §5. The open question is whether the compute term vnni − mov is (a) non-zero at all —
Phase 1 measured it at +0.20 W register-resident, so a positive term is expected — and (b)
whether it *grows* with density, which would mean the MAC array's draw depends on how many
of its inputs are zero, i.e. that activation sparsity has a compute channel distinct from
the load channel. A flat compute term that does not track density would mean the int8 unit's
data-dependence is in the operand *magnitude* it was fed, not in the sparsity, and the
sparsity signal is carried entirely by movement. Either outcome is a result; the paired
design is what lets them be told apart at this effect size.

## 7. Threats to validity (anticipated)

Written before the data, so these are the hazards the design is built to survive, and the
ones it does not.

**The synthetic stream is not a running network.** This is the largest threat and it is
structural. A controlled density victim establishes that *density is recoverable from a
work-balanced operand stream*; it does not by itself establish that density is recoverable
from PyTorch, where the sparsity co-varies with layer size, memory-access pattern, and the
scheduler. The controlled stream is what makes the independent variable exact and the
contrast work-balanced — the rigor this chapter trades for realism — and the gap to real
inference is named here rather than papered over. Closing it needs the deferred external-
process measurement mode (§8); until then the claim is scoped to the operand stream.

**Weight and distance are confounded in the density knob.** Raising density raises both the
mean Hamming weight and the mean Hamming distance of the stream (§4, Model C), and this
victim moves both at once by construction. For *recovering density* that is not a problem —
both are monotone in density and the channel is welcome to use either — but it means the §5
slope must not be read as a pure weight coefficient. A control that fixes distance while
varying density (clustering the nonzero words contiguously versus spreading them) would
separate them and is noted as future work; it is not needed for the recovery claim.

**The nonzero pattern is all-ones, which is the maximum-magnitude case.** It maximises
SNR, which is right for a first pass, but real activations are small int8 values, not
`0xFF`. The magnitude of the nonzero activations is a second variable this pass holds at
its extreme; §6's follow-up varies it. Any absolute per-density number here is therefore an
upper bound on the effect a realistic activation magnitude would produce.

**Drift, and the classifier's baseline.** The classifier operates on per-block power
baselined against each run's density-0 condition, which removes the run's DC offset but not
within-run drift; interleaving makes the latter common-mode, as in Chapter 1, but the
admitted-bias caveat from Chapter 1 §12 applies here too and will be quoted per session
once measured. Leave-one-repeat-out cross-validation is used specifically so that the
run-level state Chapter 1 identifies as the real error bar is held out of training rather
than leaking into it.

**One machine, one microarchitecture, and no direct cache-residency measurement**, as in
every chapter: i7-12700H, no AVX-512, `perf` unusable unprivileged, cache residency
inferred from achieved bandwidth.

## 8. What this chapter does not yet cover

Recorded as future work, in the manner of the Phase 2 chapter's gap section, so the
boundaries of the claim are visible in the chapter and not only in the plan:

- **The runs themselves.** The victims, experiments and analysis are written and pass
  static review; they have not been compiled, `make check`ed, or measured. Every PENDING
  table above is a placeholder. This is the first thing the next session must do.
- **Real-inference confirmation.** A bridge that measures package power over a window while
  an external numpy/BLAS matmul at controlled sparsity runs on a victim core would confirm
  the leakage on genuine GEMM without a heavy framework install; it needs a new measurement
  mode and is deferred.
- **Model fingerprinting and real-image input inference** (working-plan items 3 and 4).
  Deferred with the real-inference bridge; both need a running framework.
- **Realistic activation magnitude and a fixed dense weight** in the VNNI experiment (§6).
- **A distance-fixed density control** to separate the weight and distance contributions to
  the density slope (§7).

## 9. What this chapter will establish (on completion)

*To be written once §5 and §6 are measured.* The intended claims, each contingent on the
data:

- Whether operand-stream density is recoverable from package power, quantified as a level
  classifier (confusion matrix, accuracy at a stated observation length) and a density
  regression (MAE in density points).
- Which of the three models of §4 the density response follows, and in particular whether
  the sparse end is easier to read than a linear weight model predicts — the property the
  application depends on.
- Whether the int8 dot product carries an activation-sparsity channel distinct from
  movement, and whether that channel grows with sparsity.
- A cross-check that the density leakage is the Phase 1 movement/weight effect one level up,
  via the mean-Hamming-weight slope against +50.75 mW/bit.

Until then this chapter stands as a methodology and a set of falsifiable predictions, which
is the honest state of Phase 3 as of this draft.
