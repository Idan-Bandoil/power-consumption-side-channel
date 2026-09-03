# Critique of the Phase 0–2 drafts

*Review dated 2026-09-03, against `thesis/phase0-measurement.md`, `phase1-leakage.md`,
`phase2-covert.md` at commit `218b474`. A list of what an examiner will find and what it
would cost to close each item.*

> **Status, 2026-09-04.** Items 1 and 2 of the work order are done — see *Outcomes* at the
> foot of this document. Two of the findings below (B4, and C3's magnitude) were **refuted**
> by the checks they prompted, and are struck through in place rather than deleted; three
> others (A1, A3, D1) were confirmed and turned out to be larger than estimated. Remaining
> items are unchanged.

Ground rule used throughout: a finding is only listed if there is a concrete experiment,
gate, or reanalysis that would settle it. Items are ordered by how much of the thesis
they move, not by how hard they are.

The three drafts are unusually honest documents — the §12/§9 "threats to validity"
sections pre-empt a lot of this, and the negative results are reported rather than
buried. The problems below are mostly not *missing caution*; they are places where the
caution is stated in one section and then contradicted by the number quoted in another.

---

## A. Results that do not survive as stated

### A1. The tier-1 headline table is a per-rate minimum over repeats

**`phase2-covert.md` §5, §10, and the opening claim of §1.**

The chapter says it reads "off the repeat whose sampler stayed in the good regime". That
is not what the table contains. Against `results/20260903-115606-phase2_tier1_rate/summary.txt`:

| rate | repeats (BER) | all settled? | chapter quotes | `analysis.covert` aggregate | settled-only |
|---|---|---|---|---|---|
| 500 b/s | 0.492, 0.496, **0.037** | no (2 unsettled) | 0.037 | 0.342 | 0.037 (n=1) |
| 333 b/s | 0.113, 0.018, **0.012** | **yes, all three** | 0.012 | 0.048 | 0.048 (n=3) |
| 250 b/s | 0.489, 0.519, **0.008** | 1 unsettled | 0.008 | 0.339 | 0.263 (n=2) |
| 125 b/s | 0.043, 0.005, **0.002** | **yes, all three** | 0.002 | 0.017 | 0.017 (n=3) |
| **83 b/s** | 0.030, 0.002, **0.000** | **yes, all three** | **0** | **0.0107** | **0.0107 (n=3)** |
| 62.5 b/s | 0.007, 0.019, **0.000** | yes, all three | 0 | 0.0085 | 0.0085 (n=3) |
| 31.2 b/s | 0.008, 0.000, 0.000 | yes, all three | 0 | 0.0026 | 0.0026 (n=3) |

At 333, 125, 83, 62.5 and 31.2 bit/s **every repeat is settled**, so the stated selection
rule selects nothing — and the table still reports the single best repeat at each rate.
The tooling prints the aggregate and the settled-only mean side by side for exactly this
reason, and both disagree with the chapter.

Consequences:

- **"Error-free at 83 bit/s" is not supported.** One repeat of three was error-free; the
  other two were 0.0303 and 0.0020. Aggregated, tier 1 is error-free at *no* rate in the
  sweep. The best aggregated raw BER anywhere is 0.0026 at 31 bit/s.
- This violates the project's own most-repeated methodological rule ("no single run is
  quoted; `analysis.aggregate` over ≥3 repeats is the reporting unit"), stated in
  `phase0` §5, `phase1` §2, and `phase2` §6 — and tiers 2 and 3 *do* follow it. So the one
  tier that breaks the rule is the one carrying the headline.
- §7 confesses part of this ("the fast end … rests on a single usable repeat per rate"),
  but frames it as an overshoot problem confined to 2–4 ms. It is not confined there.

**Fix.** Quote the aggregate everywhere, with the settled-only column beside it as the
tooling already prints. Re-run at 4 repeats (already on the plan) and re-derive §5 from
`summary.txt` mechanically rather than by hand. The honest headline is not worse, just
different — see A3 and D4.

### A2. §4's depth ladder and §8's zero-step are quantitatively inconsistent

**`phase1-leakage.md` §4 vs §8.**

§8 establishes a **+349 mW step in watts** that belongs to the all-zero baseline, and then
dismisses it for §4: *"The rankings in §4 are unaffected, since every row carries it;
absolute per-byte figures are overestimates by that much."*

That is true in watts and false in pJ/byte — and §4 is reported in pJ/byte. Dividing a
constant **watt** offset by throughputs spanning 41–741 GB/s gives a wildly different
per-byte correction per row:

| victim | GB/s | Δ pJ/B (as published) | step share (0.349 W ÷ GB/s) | Δ pJ/B corrected |
|---|---|---|---|---|
| `ws_l1_x8` | 741 | 0.31 | 0.47 | **−0.16** |
| `ws_l2_x8` | 330 | 3.35 | 1.06 | 2.29 |
| `ws_l3_x8` | 148 | 13.88 | 2.36 | 11.52 |
| `ws_dram_x8` | 41 | 21.02 | 8.51 | 12.51 |

Under the chapter's own §8 result the "68×, monotone" ladder becomes ~5.5× with **L3 and
DRAM indistinguishable**, and the L1 row's entire effect is smaller than the constant
belonging to its baseline — i.e. L1-resident operand movement may leak nothing at all
beyond the zero anomaly.

This does not mean §4 is wrong. It means the step's *scaling* is the load-bearing
unknown, and it was never measured: the +349 mW was established on `ws_l3_x8` alone. If
the step is constant in watts, the table above applies. If it is constant in pJ/byte, §4
stands as published. **Nothing in the thesis distinguishes those, and they give opposite
headlines.**

**Fix.** One session: HW ∈ {0, 1, 8, 32} × {`ws_l1_x8`, `ws_l2_x8`, `ws_dram_x8`}, driver
settings identical to `phase1_hamming_weight`, plus the usual anchor. Twelve runs × 3
repeats. This also closes E1 below — it is the single highest-value experiment left in
Phase 1.

### A3. The 2–4 ms failures are sync-acquisition failures, not noise-limited demodulation

**`phase2-covert.md` §6 and §7.**

§7 attributes the run-to-run spread to per-chip noise σ and says "forcing sync to the true
offset leaves the bad runs failing". The summary says otherwise. Every run in the sweep
falls into one of two buckets with nothing in between:

- |sync error| < 1 chip → BER 0.000–0.043
- |sync error| > 60 chips → BER 0.489–0.519

There are no intermediate cases in 30 runs. And §6's own sync-constrained numbers show
the demodulator working where the free-running decode failed: `sym_04ms_r0` reads
BER 0.489 free and **0.346** constrained; `sym_02ms_r1` reads 0.496 free and **0.312**
constrained. `sym_04ms_r1` is *settled* (0.29% overshoots), has d′_paired 1.16 predicting
BER 0.122, and decodes at 0.519 — that is an acquisition failure, full stop.

So the chapter's causal chain (overshoots → σ → BER) is right about σ but wrong about the
last link: at the fast end σ destroys **the preamble correlation**, and the bits are lost
to acquisition rather than to per-symbol noise. That matters because the two have
completely different remedies, and because it means the reported 500 bit/s ceiling is an
*acquisition* ceiling that a better sync would move.

There is also a presentation hazard: §5, §6 and §7 quote three different BERs for the same
run (`sym_04ms_r0`: 0.008 in §5's row, 0.346 in §6, 0.498 in §7) without labelling which
is free-running and which is sync-constrained.

**Fix.** Report two numbers per run: acquisition probability (peak-to-runner-up above a
stated threshold, decided without ground truth) and BER given acquisition. Label every BER
in the chapter as free or constrained. Then the obvious improvements are on the table:
a longer or coded preamble, coherent accumulation across more frames (already done —
extend it), and a matched filter that accounts for the RAPL boxcar rather than a square
chip template.

### A4. Phase 0 credits the validity gates with a result they did not produce

**`phase0-measurement.md` §1, §6, §8.**

§8: *"It also cost the project its headline preliminary finding, which is the strongest
evidence available that the gates do something: the first result the corrected instrument
was pointed at was the project's own, and it did not survive."*

But §6's own analysis says the 1228 dataset was **drift-free** (deciles flat to ±0.7%) and
its conditions genuinely differed. No gate rejected it. What killed the claim was that the
rebuilt victim was a *different workload* — `-O0` with stack spills versus register-resident
— which §6 identifies and Phase 1 §3 confirms. The gates rejected the 1207 dataset's
*design*; they had nothing to say about 1228.

The stronger and more accurate statement is available and better: the preliminary result
was **mis-attributed, not wrong**, and the instrument is what made the correct attribution
findable. As written the chapter overclaims for the gates in a way that is easy to
puncture, and undersells the actual sequence of events.

**Fix.** Rewrite §8's last paragraph and §1's framing. Keep the gates' credit where it is
earned (§3.1, the `artifact_demo` run, the zero-tick class) and separate it from the
`-O0` discovery.

---

## B. Instrument validity

### B1. RAPL is never cross-validated against anything physical

`phase0` §7 lists "turbostat cross-validation not yet done" as one bullet among six. It is
not one bullet among six — **the entire thesis is one counter on one part**, and RAPL on
client Intel silicon is a partly-modelled quantity. The alternative explanation an examiner
will raise is not "the effect is not real" but "the effect is in RAPL's activity model
rather than in the die", and nothing in Phase 0 or Phase 1 excludes it. Phase 1's whole
leakage model is RAPL-only under Config-A.

Three corroborations exist or are nearly free:

1. **Tier 3 already is one.** `rx_timing` infers throttling from its own dilation and
   never reads RAPL; if the part actually clocked down, the power was physically real.
   This is currently buried in Phase 2 §8.3 as a security result. It is also the best
   independent evidence in the thesis that RAPL is not hallucinating, and Phase 0 should
   forward-reference it.
2. **Battery discharge rate.** `power_now` on a laptop resolves a couple of hundred
   milliwatts over a 60 s window. The `hw32` contrast is 1.9 W. Two 60-second arms,
   alternated four times, would confirm the largest effect in the thesis on an instrument
   that shares nothing with RAPL. This is an afternoon.
3. **The `core` and `uncore` sub-domains are exposed on this part and never read.**
   `phase0` §2 says so. Splitting the operand effect between core and uncore is a free
   *mechanism* result — if operand movement leaks in uncore, that is a much sharper claim
   than "package power" — and it needs no new victim, just two more MSR reads per edge.

**Fix.** Do (2) once and cite it in Phase 0 §5 as an external check. Do (3) as a Phase 1
addendum. Promote (1) into Phase 0.

### B2. The recorded frequency is the monitor's, not the victim's

`util/sampler.c:142` reads `frequency_msr_raw(s->core)` where `s->core` is
`cfg.attacker_core`. So `daperf`/`dmperf` in every driver CSV — and the per-condition
"MHz" column `analysis/report.py:50-55` prints — describe **core 0's** clock, which is
running a `pause` poll loop on an isolated core, not the victims'.

That undercuts the load-bearing sentence of Config-A:

> *"DVFS is removed from the system, so a power difference is a power difference and not a
> frequency response to one."* (`phase0` §2)

This is asserted from the fact that turbo was disabled, never verified per run from the
recorded data, and the recorded data could not verify it even in principle. There is also
no gate on it: the MHz column is printed and ignored, so a run where the two conditions
sat at different victim clocks would pass everything.

**Fix.** Add a second APERF/MPERF read on one victim core per edge (a second `pread`;
the sampler already does one) or read `scaling_cur_freq` for a victim core once per block.
Then add a `frequency_balance` gate: fail if the two conditions' mean victim frequency
differs by more than a stated bound. Cheap, and it converts the chapter's central
assertion into a per-run check.

### B3. Work rate is never measured per condition

`src/driver.c:141-195` latches `victims_bursts()` once at the start of the measured blocks
and once at the end. `victim_bytes_per_s` in every manifest is therefore **pooled over both
conditions**, and `analysis/aggregate.py:73-80` divides a *between-condition* Δ power by
that pooled denominator to get pJ/byte.

Two things follow:

- The claim that the modulation changes the operand and not the amount of work — made in
  `phase1` §10 ("the loop is meant to be load-bound so that every variant moves operands at
  the same rate") and again in `phase2` §3 ("a symbol transition costs a pointer swap … the
  modulation is a change of *operand*, not of how much work is being done") — is argued
  architecturally and never measured. It is almost certainly true; but "almost certainly"
  is the wrong standard for the assumption the whole thesis rests on.
- If it were ever false, the failure would be invisible: a throughput difference between
  conditions would show up as a power difference and be attributed to the operand.

**Fix.** Latch `a->bursts` per block (one 8-byte read at each block boundary, off the
sampling path), report GB/s per condition in the manifest, and add a `work_balance` gate.
This is maybe twenty lines and it retires an entire class of objection.

### ~~B4. The RAPL-period "finding" is probably an artifact of its own estimator~~ — REFUTED

> **Checked 2026-09-04 and wrong.** `analysis.instrument --check period` over all 322
> edge-sampled runs in `results/` gives **corr(overshoot fraction, reported period) =
> −0.009, R² = 0.000**. Extrapolating the fit to a zero-overshoot sampler gives 1.0002 ms,
> identical to the raw mean of 1.0002 ms and still +2.42% from 2⁻¹⁰ s. The contamination
> model predicts slope/intercept = 1.00; the measured value is −0.01.
>
> So the reported period does not track the sampler's own overshoot rate, the mechanism
> below does not operate, and **`phase0` §4.3's finding stands as written**. The reasoning
> that follows was sound and the arithmetic coincidence (2.3% overshoots would give exactly
> the observed 2.42% excess) was just that. It is left in place because the check was worth
> running and because the *bistability* half of it is still unexplained — the feedback path
> exists in the code even though it demonstrably does not move the period estimate.
>
> The one change worth making anyway is defensive: excluding flagged edges from the EWMA
> costs one `else` and removes the coupling entirely, so the question cannot come back.

*Original finding, retained for the record:*

`phase0` §4.3 reports the measured update period as **0.9990 ms (SD 0.0065, range
0.9816–1.0171)** across 82 runs and concludes *"The update interval on this part is
therefore ~1.000 ms and not 2⁻¹⁰ s"* — a 2.3% discrepancy presented as a small result in
its own right.

Look at `util/sampler.c:150-157`:

```c
else if (e.dtsc > s->period_est + s->period_est / 2)
        s->overshoots++;
/* EWMA, 1/16 weight */
s->period_est += ((int64_t)e.dtsc - (int64_t)s->period_est) / 16;
```

The sample flagged as an overshoot on one line is fed into the period estimator on the
next. An overshoot is an edge observed ~2 periods late, so at overshoot fraction *p* the
estimator converges to **(1 + p)·T**, not *T*. With *T* = 0.9766 ms, an overshoot rate of
2.3% gives exactly 0.999 ms. And the observed range 0.982–1.017 (±1.8%) matches the
observed spread of overshoot rates (0.1%–4%) far better than it matches a physical
constant measured against a TSC calibrated to parts per million.

The same loop is also a candidate mechanism for the bistability the thesis twice calls
unexplained (`phase0` §4.3, `phase2` §7): an overshoot raises `period_est`, which lengthens
the 7/8 guard, which brings the next poll closer to the following edge, which makes another
overshoot likelier. That is positive feedback with two fixed points.

**Fix.** Two steps, both cheap. (1) Test it on data already committed: correlate
`rapl_period_ms` against `rapl_overshoots / samples_written` across the 82 manifests. If
the correlation is strong, the §4.3 claim is an artifact and the bistability is explained.
(2) Exclude flagged edges from the EWMA (one `else`), re-measure the period on a clean run,
and re-state §4.3 — with a robust estimator (median `dtsc`, or a mode over the primary
cluster) rather than a mean over a mixture.

---

## C. Statistics and controls

### C1. p-values and confidence bounds count repeated frames as independent bits

`analysis/covert.py:378-391` computes an exact binomial p over `payload_bits × frames`,
and `phase2` §5 applies the rule of three the same way. But a frame is a *repetition of the
same payload*, so those bits share one truth.

Under the null that matters — a decoder that emits bits **independently of the message but
correlated with itself over time**, which is precisely the failure §8.4 identified and
fixed only halfway — the effective sample size is the number of *distinct* payload bits,
not the number of transmitted ones.

The headline unprivileged result is the sharp case:

- `phase2_tier2_covert.json` at 512 ms uses `random_bits: 8`, `frames: 4`, 3 repeats.
- That is **8 distinct payload bits per run, 24 across the session**, transmitted 96 times.
- The chapter and the tooling report **p = 2 × 10⁻¹⁸** over "96 bits".
- The seed varies per repeat (`experiment_runner.py:623`), so the payloads do differ across
  repeats — but with 8 balanced bits there are only C(8,4) = 70 distinct messages, so a
  permutation test over messages bottoms out around (1/70)³ ≈ 3 × 10⁻⁶ per session. Not
  10⁻¹⁸, by twelve orders of magnitude.

The A/A gate has the same structure: "pooled 0.500 over 384 bits (p = 0.52)" in §8.3 is
really ~32 distinct bits per condition. It passes, but it has far less power than the bit
count suggests, so the gate is weaker than the chapter implies.

Balancing the payload (§8.4) fixed the *expectation*. It did nothing to the *variance*, and
the variance is what a p-value is made of.

**Fix.** Three things, all cheap: report distinct-bit counts next to transmitted-bit counts
in every table; compute the p-value from a message-permutation null rather than a binomial
over transmissions; and at the slow rates, spend the run time on more distinct bits rather
than more repetitions of eight (keep enough frames for the vote, e.g. 32 distinct × 4
frames instead of 8 × 4).

### C2. A concrete arithmetic error in the same section

`phase2` §5: *"over 2048 payload bits per run at 83 bit/s and 512 at 31–42 bit/s … by the
rule of three … 5.9 × 10⁻³ for the 512-bit runs and 1.5 × 10⁻³ for the 2048-bit ones."*

`phase2_tier1_rate.json` gives `sym_12ms` (83.3 bit/s) `random_bits: 128` with 8 frames =
**1024 bits**, not 2048. Confirmed against the summary: `sym_12ms_r0` BER 0.0303 = 31/1024.
The 2048-bit runs are 500–125 bit/s. So the bound at 83 bit/s is **2.9 × 10⁻³**, and under
C1's distinct-bit reading it is 3/128 = 2.3 × 10⁻².

### C3. The interleaving gate does not bound the bias it admits — CONFIRMED as a design gap, ~~magnitude overstated~~

> **Checked 2026-09-04.** The diagnosis is right and the arithmetic below was pessimistic
> by an order of magnitude. `analysis.report` now prints imbalance × drift span per run.
> Over 269 Phase 0/1 runs the median admitted bias is **6.0 mW**, not the 100–200 mW
> estimated below, because achieved imbalance is 0.004–0.065 — far inside the 0.10 gate —
> and drift spans are mostly well under 1 W. The median effect is 75× its run's admitted
> bias.
>
> It is not uniformly comfortable. The Hamming-weight session drifted 2–3 W within a
> condition, so its low-weight rows admit 60–90 mW: `hw01` 81 mW against a +458 mW effect
> (5.7×) and `hw04_a` 61 mW against +513 mW (8.4×). Those are the rows carrying the §8
> zero-step, where 80 mW is 23% of the +349 mW discontinuity — so the step survives, but
> its error budget is dominated by admitted drift rather than by the between-repeat spread
> quoted beside it. Recorded in `phase1` §12.
>
> The metric validates on the run built to fail: `phase0_artifact_demo`'s deliberately
> sequential A/A admits 302 mW, two orders of magnitude above the median. Gating on the
> product (rather than only printing it) stays on the list as item 5.

*Original finding:*

`analysis/stats.py:94-114` measures the difference in mean chronological position between
conditions, and `report.py` fails above 0.10. The threshold is never converted into a bound
on the drift-induced bias it permits.

The report already computes the other half: `drift_table` prints the within-condition span
across deciles. Bias admitted ≈ imbalance × drift span. At the gate's limit of 0.10, a run
with a 1–2 W within-condition drift span admits **0.1–0.2 W of bias** — larger than several
effects the thesis reports as real (HD 2 = 0.086 W, the placement residual ≈ 0.1 W, the
whole L1 row of §4 = 0.23 W).

It is also a first-moment check: a shuffle that clumps conditions locally, or a drift that
is non-monotone, passes it.

**Fix.** Gate on `imbalance × drift_span` (both already computed) rather than on imbalance
alone, and print the admitted-bias figure per run so a reader can compare it against the
effect being claimed. Optionally add a lag-1 autocorrelation check on the condition
sequence.

### C4. The A/A control is calibrated in the wrong noise regime

Every A/A in the project holds the same operand in both conditions, so it sits at the
harness's *quiet* noise floor: `phase1` §5 gives A/A between-run SD of 4.6–10 mW, against
~100 mW for the 2 W effects it is being used to validate. The chapter's own most useful
methodological finding — that the error scales with the effect (§12, ratios up to 6.8) —
implies that a passing A/A certifies the pipeline at ~20× less noise than the regime where
claims are made, and is structurally blind to anything that scales with effect size.

A second gap follows from the code. In `util/victim-utils.c:444-460` the two conditions
draw from **two different `mmap`'d buffers** (`slot[0]` and `slot[1]`), so condition and
buffer address are perfectly confounded in every `ws_*` contrast — while an A/A requests
the same selector twice and therefore gets **one** slot, and cannot see an address effect at
all. §7 notes this for buffer *contents*; it does not note it for buffer *addresses* (cache
set/bank/page mapping). The only bound on it is the single `placement` run in §9 (24 mW,
one session), plus `(fwd + rev)/2` from `phase1_polarity`, which the chapter explicitly
declines to use as an estimator.

There is a perfect control available for free. `ws_get` keys its cache on the **full 64-bit
selector** while `ws_fill` uses only the **low 32 bits**. So selectors
`0x00000000_5A5A5A5A` and `0x00000001_5A5A5A5A` allocate **two distinct buffers with
bit-identical contents** on a plain `ws_l3_x8`. That is a two-buffer A/A: same data,
different addresses, everything else identical. It closes the address confound outright,
and it is one selector file.

**Fix.** Add the two-buffer A/A above and run it in every session alongside the existing
one. Separately, add a *sham-B* control at effect scale — two operands of equal Hamming
weight and equal Hamming distance, expected difference zero, magnitude of the surrounding
signal ~2 W — and quote its between-run spread as the noise floor for large effects. §9's
`placement` run is exactly this; it should be promoted from a footnote to a standing
control and run at every depth.

### C5. The bit-placement claim is eight uncorrected comparisons against a heuristic

`phase1` §11 concludes *"a placement term exists, is of order 0.1 W … appears at some
weights and not others"*, from `analysis/hwfit.py:200-212`, whose verdict is
`spread <= 2 * between` — not a test — with `between` estimated from **three** repeats, over
**eight** weights, with no multiplicity control. Two hits in eight at that threshold is
approximately what pure noise produces.

The chapter is appropriately hedged in tone but still states the effect as existing, and
§13 lists it as an established finding.

**Fix.** Either downgrade to "not resolved at three repeats" — which is the defensible
reading — or spend one session on HW 2 and HW 8 at 6 repeats with 3–4 patterns each and a
permutation test across patterns. The second is more valuable than it looks: a real
placement term at fixed weight would be a genuinely novel result, and the current design
cannot claim it either way.

### C6. §6's model comparison is not reproducible and is structurally rigged

`phase1` §6 rejects √HW (R² 0.951), log(1+HW) (0.874) and a power law through the origin
(0.928) against the linear fit's 0.974. Two problems:

1. **No code in the repo does this.** `analysis/hwfit.py` fits `a + b·x` and nothing else.
   For a thesis whose README promises every number is reproducible from `results/`, the
   model-selection paragraph has no artifact behind it.
2. **The comparison penalises the alternatives for being right about the origin.** √HW and
   log(1+HW) pass through (0,0), which is the one point known exactly and the one point the
   winning linear model violates by 23 standard errors. Comparing raw R² across models with
   different intercept freedom, on the same 11–18 points, is not a model-selection
   procedure.

**Fix.** Refit each alternative *with a free intercept*, one fit per repeat as `hwfit` does,
and compare on AIC (or on held-out prediction at the weights not used in the fit). Commit
the script. The linear model will probably still win — the point is that right now nobody
can check.

---

## D. Threat model and scope (Phase 2)

### D1. The Phase 2 transmitter is not the victim Phase 1 characterised

§5 justifies choosing `ws_l3_x8` on Phase 1's detectability ranking. That ranking was
measured at **4 threads on cores 2,4,6,8,10 (stride 2, one thread per physical core)**.
`phase2_tier2_covert.json` and the tier-3 spec run **10 threads on cores 2–11, stride 1**.

Two things change and neither is mentioned:

- **SMT.** Stride 1 over cores 2–11 puts two threads on each of five physical P-cores.
  They share L1, L2 and the load ports. That is a different microarchitectural experiment.
- **Cache residency.** 10 threads × 4 MB = **40 MB active against a 24 MB L3**. The
  transmitter is substantially DRAM-resident, so the victim named `ws_l3_x8` is not
  operating at the depth its name and its Phase 1 characterisation describe. (The
  `victim-utils.c` comment sizing this variant says so explicitly: *"With four victim
  threads the 4M variant totals 16M and still fits L3."*)

So the chapter's transmitter-selection argument does not apply to the runs it introduces.
The channel plainly works regardless — but the *reason given* for the choice is wrong at
that configuration, and a reader who checks the arithmetic will find it.

**Fix.** Report the transmitter's achieved GB/s at its actual configuration (the tx already
counts bursts; it just does not divide). Then either resize to a per-thread working set that
keeps 10 threads inside L3 (~2 MB), or re-justify the choice empirically at 10 threads. One
sweep of `{ws_l2_x8, ws_l3_x8, ws_dram_x8} × 10 threads` settles it and is worth doing
anyway, since it is the transmitter-selection experiment for the whole chapter.

### D2. The attacker is given an isolated core, and the chapter does not say so

`phase2_tier2_covert.json` sets `rx.core = 0`, and the kernel is booted `isolcpus=0`. So
the receiver — including the two *unprivileged* receivers whose whole point is that they
need nothing special — runs on a core the kernel has been configured to keep empty.

Phase 0 §7 lists `isolcpus=0` as a limitation because it fails to isolate the *victims*.
For Phase 2 it points the other way: it is a privilege granted to the attacker, and it is
exactly the privilege that matters most for tier 3, whose entire signal is the timing of
its own workload. Scheduling noise on a shared core is the first thing that would degrade
it.

**Fix.** One run per tier with the receiver unpinned, or pinned to a core carrying ordinary
background load, and report the BER cost. If the channel survives, that is a strictly
stronger result than the current one and it costs two runs. If it does not, that is the
most important caveat in the chapter and it is currently invisible.

### D3. A transmitter that pegs ten P-core threads is not covert

The chapter is candid that the channel *exists only while the part throttles* (§8.2) and
frames that as a precondition. It never confronts the corollary: the transmitter must hold
10 of 12 P-core hyperthreads at 100% for the entire duration of the message. At 2 bit/s
with 4 frames of 21 bits that is roughly **three minutes of full-machine load per 8 bits of
payload**. Any monitoring at all sees that.

This is the weakest point in the threat model and it is also the most convertible into a
result.

**Fix.** Sweep transmitter thread count (4/6/8/10) and duty cycle against BER, under
Config-B, tier 2 or 3. The output is a statement of the form "the channel requires ≥N
loaded threads / X% of package power headroom consumed", which is (a) an honest bound on
the attack, (b) directly what Phase 4 needs in order to argue that a headroom- or
throttle-based mitigation is the one that works, and (c) the natural continuation of §8.2,
which already did the thread-count sweep with the *wrong instrument* (a mean-difference
proxy instead of the real receiver — §8.2's own lesson).

### D4. The tier comparison is not like-for-like, in more ways than the chapter concedes

§9 admits the Config-A/Config-B problem. But the ladder differs in at least four other
dimensions at once:

| | tier 1 | tier 2 / 3 |
|---|---|---|
| threads | 4 | 10 |
| core layout | stride 2 (5 physical cores) | stride 1 (5 cores, SMT paired) |
| frames (vote depth) | 8 | 4 |
| distinct payload bits | 256 → 64 | 64 → 8 |
| config | A | B |

"83 bit/s against 2 bit/s, a factor of about 40" is quoted in §1, §8.1 and §10 as the
chapter's central finding. It is a comparison across five simultaneous changes.

There is also a metric problem. "Error-free after an 8-frame vote at 500 bit/s" is
**62.5 bit/s effective**; the vote's rate cost is never accounted anywhere. And §9 declines
to quote capacity — *"no capacity figure worth quoting"* — while `analysis/covert.py:346`
computes it and the summaries print it. Capacity is precisely the metric that makes tiers
comparable and the metric Liu et al. and Hertzbleed report, so declining it is also what
makes §9's "no comparison to the literature" gap unclosable.

From the committed summary, tier 1's aggregated capacity peaks at **251.8 bit/s at 333 bit/s
raw** (best single repeat: ~386 bit/s at 500 raw). That is a better and more defensible
headline than "error-free at 83 bit/s", and unlike it, it survives A1.

**Fix.** (a) One matched sweep — same threads, same layout, same frames, same payload
length, Config-B — for all three tiers; this is item 3 on the plan's next-steps list and it
should be promoted to first. (b) Add an effective-rate column (raw ÷ frames voted).
(c) Make capacity the headline metric per tier; it is already computed.

---

## E. The chapter-level gap

### E1. Phase 1 produces coefficients, not a model — nothing is predicted out of sample

This is the largest single "not good enough for a thesis" item, and it is structural rather
than a defect in any one number.

Phase 1 delivers four independent characterisations: a depth ladder (§4), a weight slope
plus a zero-step (§6–§8), a distance slope (§9), and an instruction table (§10). Each is
measured with one factor varied and the others held fixed. They are never combined, and no
combination is ever tested against a measurement that was not used to build it.

The chapter says as much without quite noticing: *"a victim processing real data modulates
both terms at once … any attempt to invert the channel has to carry both terms"* (§9.2) —
and then never writes the two terms down together.

The difference matters for what the thesis can claim. As it stands, §13 is a list of
coefficients valid at the operating points where each was measured. A model would be

  dP ≈ f(depth) · (α·HW + β·HD + step·1[HW=0]) + γ(instruction)

with one prediction on a victim outside the fit — say `ws_l2_x8_ab` at some (HW, HD)
combination never run — and a reported prediction error. That single number is what turns
"we measured these things" into "we can predict this platform", and it is the difference
between a characterisation chapter and a leakage-model chapter.

The depth × operand interaction it needs is the same experiment as A2, so the two fixes are
one session.

**Fix.** Run A2's grid. Fit the combined model on it plus the existing sessions. Hold out
one victim/operand combination, predict it, run it, report the residual. Add a §12
subsection on where the model breaks down.

### E2. Everything is measured on zero-entropy data, and Phase 3 depends on the extrapolation

Every `ws_*` victim fills its buffer with **one repeated 32-bit word** (or two alternating
words). So the entire leakage model is calibrated on maximally-compressible, zero-entropy
streams. Two consequences the drafts do not address:

1. **The Phase 3 premise is an extrapolation the design cannot support.** §8 argues that a
   disproportionately cheap zero is "the mechanism the application chapter depends on"
   because post-ReLU activations are 50–90% zero. But the measured discontinuity is between
   *a buffer that is entirely zero* and *a buffer of entirely-nonzero words*. Real sparse
   activations are a **mixture**, and nothing in the thesis measures one. If the step is a
   per-transfer effect, a 50/50 mixture inherits half of it. If it is a per-stream effect
   — an idle detector, a link state, a clock-gating condition that needs sustained zeros —
   a mixture inherits none of it, and the ML chapter's foundation is gone. These are
   distinguishable and currently indistinguished.
2. **The slope may not survive real data.** A constant repeated word is a degenerate operand.
   Whether +50.75 mW per set bit holds when the mean bit density is the same but the words
   differ is untested, and it is the form the coefficient would take in any application.

Both are one victim variant away, and the machinery is nearly there: `ws_fill_ab` already
alternates two words on a mask.

**Fix.** (a) A duty-cycle fill — a fraction *q* of words are zero and the rest are a fixed
heavy word — swept over *q* ∈ {0, ⅛, ¼, ½, ¾, 1} at fixed depth. If dP is linear in *q*, the
step is per-transfer and Phase 3 is on solid ground; if it is flat until *q* → 1, it is
per-stream and Phase 3 needs rethinking. This is the single most important experiment for
Phase 3 and it belongs at the end of Phase 1, not the start of Phase 3. (b) An i.i.d. fill
at controlled mean bit density, checked against the constant-word slope, as a
generalisation control.

### E3. "pJ/byte" names a quantity the thesis did not measure

`analysis/aggregate.py` computes `1000 × Δ_watts / GB_per_s`, which is the **difference** in
energy per byte between two operands. §4 and §13 report it as *"Cost per byte moved rises
monotonically with depth, 0.31 pJ/byte at L1 to 21.02 pJ/byte at DRAM"* — which is the cost
of moving a byte, a much larger and entirely unmeasured quantity.

This is not pedantry: it is what makes the DRAM figure look comparable to published DRAM
transport energies (~10–20 pJ/bit) when it is not the same quantity, and it is what makes A2
easy to miss.

**Fix.** Rename it consistently — Δ pJ/byte, or "operand-dependent transport energy per
byte" — in the tables, the prose, and `aggregate.py`'s column header.

### E4. Phase 1's detector→bit-rate conversion measures a different decision rule than Phase 2 uses

`phase1` §13: *"`ws_l3_x8` reaches 95% detector accuracy at n = 1–8 samples … roughly
125–1000 bit/s raw"*, presented as the input to the covert-channel chapter's design.

But `analysis/stats.py:117` scores an **absolute mean-threshold** detector, calibrated on
training blocks from the same run, on windows drawn inside a block. Phase 2's decoder makes
a **paired within-symbol** decision and has no training data — and `phase2` §6 is entirely
about why those two statistics differ, and by how much (tier 2: marginal d′ 0.08 versus
paired 1.07). The conversion also ignores the ~1 ms RAPL boxcar, which `phase2` §8 measures
as destroying two-thirds of the separation at a 1 ms chip.

The two errors happen to run in opposite directions and the bracket happened to contain the
answer. That is luck, and an examiner who notices §6 will ask why §13 was computed the other
way.

**Fix.** Either label §13 explicitly as an upper bound under a rule the receiver does not
use, or recompute the accuracy curve with the paired statistic on adjacent windows — which
is a small change to `accuracy_vs_n` and makes Phase 1's output directly the quantity Phase
2 consumes.

---

## F. Smaller, but load-bearing

- **`late_chips` has no teeth.** `phase2` §3 calls it "a self-check with teeth", but
  `tx.c:350-365` only checks whether the *control thread* reached each deadline — a loop
  that does one store and a fence, so it essentially cannot be late. Nothing verifies that
  the **victims** observed the selector change. `ctl->epoch` is incremented for this purpose
  and read by nobody. *Fix:* have each victim count observed selector changes and compare
  against chips sent; report the shortfall. That is the check §3 claims to have.
- **No gate catches a degenerate receiver trace.** `sym_256ms_r0` in the tier-2 sweep has
  `sd MHz = 7.87` against 200–700 for every other run — a 100× outlier, almost certainly a
  parked or fixed-frequency CPU — and it silently drags the 3.9 bit/s row. *Fix:* a
  dynamic-range / value-changes gate on the receiver trace (the runner already logs
  `value_changes` and warns only on exactly zero).
- **The cpu2 tier-2 result is quoted without noting its control.** §8.1 offers "BER 0.229 at
  3.9 bit/s" watching a victim core as strictly better. On that column the A/A pools to
  0.4375 with one repeat at 0.375, and the committed summary records a gate **FAIL** on it.
  The result may well be real; as written the chapter quotes the effect and not the control
  that sits beside it in the same file. *Fix:* a longer A/A on the victim-core column
  before the claim goes in, or state the control's weakness inline.
- **Tier 2's measured BER beats its own prediction in all three repeats** (0.094/0.063/0.094
  against Q(pair) 0.142/0.186/0.183). §8.1 presents this as agreement. A systematic 2× in
  one direction across every repeat is a finding, not agreement — most likely the paired
  statistic being computed over the Barker preamble as well as the payload, or non-Gaussian
  noise. *Fix:* compute d′_paired over payload chips only and re-check.
- **The majority vote resolves ties toward 0.** `covert.py:321` uses `mean > 0.5` with an
  even frame count. Small, systematic, and free to fix (odd frame counts, or break ties on
  the summed chip difference).
- **`MEASURING_PROCS` omits `rx_timing`** (`experiment_runner.py:157`), so neither preflight
  nor `--restore-only` guards against a stray tier-3 receiver — contrary to what `CLAUDE.md`
  states. A stray `rx_timing` spins a core at 100% and would poison the next run silently.
- **Provenance drift between the drafts and the committed summaries.** Beyond C2's 2048/1024:
  §5's A/A sync offsets are given as "68, 176 and 564 chips" where the summary reads
  +302.14, −176.29, −68.32; its A/A BER is given as 0.496 where the summary aggregates to
  0.4998. `thesis/README.md` still describes `phase2-covert.md` as "tier 1 only" when it
  covers three. For a thesis that promises every number cites its run directory, these need
  a mechanical check. *Fix:* a small script that re-extracts each cited figure from the
  committed `summary.txt` and diffs it against the draft.

---

## Suggested order of work

Ranked by (thesis value) ÷ (machine time). Items in one row are one session.

1. **Re-derive `phase2` §5 from the aggregate** (A1) and add the effective-rate and capacity
   columns (D4b, D4c). *Zero machine time, and it retires the largest correctness problem in
   the drafts.*
2. **Reanalysis only, on committed data**: period-vs-overshoot correlation across the 82
   manifests (B4.1); acquisition-vs-demodulation split of the tier-1 sweep (A3);
   admitted-bias figures from the existing drift tables (C3). *Zero machine time.*
3. **The depth × operand grid** (A2 + E1): HW {0,1,8,32} × {L1, L2, DRAM}, 3 repeats, with
   an anchor and the new two-buffer A/A (C4). *Settles the §4/§8 contradiction and gives the
   combined model its interaction term.*
4. **The sparsity mixture sweep** (E2a). *Phase 3's foundation. Belongs in Phase 1.*
5. **Gates and instrumentation**: per-condition throughput (B3), victim-core frequency +
   `frequency_balance` (B2), robust period estimator (B4.2), receiver-trace gate,
   `late_chips` with teeth. *One afternoon of code, and it retires four classes of
   objection permanently.*
6. **The matched tier sweep under Config-B** (D4a) with balanced distinct-bit payloads (C1),
   plus the unpinned-receiver run (D2) and the thread-count/duty-cycle sweep (D3). *Turns
   the ladder into a controlled comparison and the threat model's weakest point into a
   result.*
7. **Battery cross-validation of RAPL** (B1.2) and the core/uncore split (B1.3).
8. Placement at 6 repeats (C5) and the committed model-comparison script (C6), if time
   allows. Both are honest to drop to "not resolved" instead.

---

## Outcomes — items 1 and 2 (2026-09-04)

Both were zero-machine-time. No experiment was re-run; everything below came out of the
committed CSVs and manifests.

### Confirmed, and larger than estimated

**A1 — the tier-1 headline was a per-rate minimum.** Confirmed exactly. `phase2` §5 is
rewritten from the three-repeat aggregate. The consequences were bigger than the critique
said: *no* rate in the sweep is error-free once aggregated, so "error-free at 83 bit/s"
is gone entirely, replaced by a raw BER of 0.011 there. `analysis.covert` now leads with
the aggregate, labels it as the reporting unit, and prints capacity, the post-vote
effective rate, and distinct-versus-transmitted bit counts.

**The replacement headline is better.** Capacity peaks at **241 bit/s at a 3 ms symbol** —
a defensible number, aggregated over three repeats, in the metric Liu et al. and Hertzbleed
use, which also unblocks §9's literature comparison. The old headline picked 500 bit/s
(36.8 bit/s of capacity) and 83 bit/s (76.2) as the interesting points; neither is the
channel's best.

**A3 — the fast-end failures are acquisition, not demodulation.** Confirmed, and it turned
out to be cheap to measure: the true chip grid is already computed for the d′ diagnostics,
so demodulating from it costs one reshape. `sym_04ms_r1` reads BER 0.519 free-running and
**0.087** on the true grid — a working channel whose frame was never found. §7's stated
reason ("forcing sync to the true offset leaves the bad runs failing") was wrong and is
corrected in place. The causal chain is now overshoots → σ → *failed acquisition* → BER at
chance.

This extends past tier 1. Tier 2 at 7.8 bit/s reads exactly 0.500 free-running and
**0.339** on the true grid, so the "cliff above 2 bit/s" is partly an acquisition cliff and
there is unexploited margin in a longer preamble. Tier 3 acquires reliably but gives away a
factor of 1.5 to sub-chip misalignment (median residual 0.248 chips against tier 1's 0.078).

**A receiver can tell whether it acquired — but not the obvious way.** Peak-over-sidelobe,
the natural statistic, does not separate at all (1.00–1.19 failed, 1.04–1.43 succeeded).
The *absolute* normalised correlation does: 0.26–0.71 failed against 0.64–1.00 succeeded, so
a threshold at 0.72 admits no failed run. That is a real threat-model result — an attacker
can discard frames it did not acquire — and it fell out of the same reanalysis.

**D1 — the Phase 2 transmitter is not the Phase 1 victim.** Confirmed with data rather than
arithmetic. Pooling every run in `results/` that used `ws_l3_x8`: **145.8 GB/s** at Phase 1's
4 threads/stride 2 (n=130) against **91.9 GB/s** at Phase 2's 10 threads/stride 1 (n=7),
falling monotonically to 65.4 at 18 threads. Phase 1 measured L3 at 148 GB/s and DRAM at 41,
so the tier-2/3 transmitter sits between them. Table added to `phase2` §9.

### Refuted by the check they prompted

**B4 — the RAPL period is not an estimator artifact.** corr(overshoot, period) = −0.009 over
322 runs; the zero-overshoot intercept equals the raw mean. `phase0` §4.3 stands unchanged.

**C3 — real as a design gap, an order of magnitude smaller than estimated.** Median admitted
bias 6.0 mW, not 100–200 mW. Worth gating on (item 5); does not threaten any published
number, with the narrowest margin 5.7× under the §8 zero-step.

### New tooling

- `analysis/instrument.py` — cross-run checks from manifests alone, no CSV required.
  `--check period` is the B4 test; `--check throughput` is the D1 test and generalises to
  "was this victim run at one operating point across the corpus".
- `analysis/covert.py` — `BER|snc`, `acq`, `|pk|`, `cap b/s`, `vote b/s`, `bits d/tx`
  columns; aggregate-first presentation; rule-of-three bounds quoted over distinct bits as
  well as transmissions.
- `analysis/report.py` — admitted-bias figure per run.

### What this changed in the drafts

`phase2-covert.md`: §1 (results list), §5 (rewritten), §6 (refit against the true-grid BER;
+0.816 / 1.15 over 20 rows, replacing +0.895 / 0.91 over 17), new §6.1 on acquisition, §7
(causal chain corrected), §8 (acquisition is the binding ceiling, not integration), §8.1
(tier-2 acquisition cliff; victim-core caveat), §8.3 (capacity columns), §9 (five-dimension
comparison caveat, transmitter bandwidth table, receiver-isolation and covertness gaps,
distinct-bit p-value caveat), §10 (rewritten).
`phase1-leakage.md`: §12 gains the admitted-bias paragraph.
`phase0-measurement.md`: §1 and §8 rewritten for **A4** — the gates are credited with
rejecting `out-1207-2115`, and the `-O0` victim rather than a gate with dissolving
`out-1228-1527`. B4 required no change, since it was refuted.

### Found while doing items 1 and 2, not in the original review

**Config-B experiments need a tighter interleaving gate than Config-A ones.** The
admitted-bias scan turned up one session where the margin inverts completely:
`phase2_tier2_feasibility` drifts 29–48 W within a condition — two orders of magnitude
more than any Config-A session — at an imbalance of 0.093, which passes the gate by 7%.
Its admitted bias is 2.7–4.4 W against effects of 1.3–2.8 W, so **every run in it has an
effect smaller than its own admitted bias**. The session is already reported as a null, so
nothing published is affected; what is new is that it could not have concluded anything
either way, and the gate said it was fine. A single fixed imbalance threshold cannot serve
both configurations — the same imbalance buys vastly more drift when the part is free to
throttle. This belongs with item 5.

**A receiver cannot tell it acquired from the peak-to-sidelobe ratio, but can from the
absolute peak.** Recorded under A3 above; it was not anticipated and it is a threat-model
result rather than a methodological one.

### Process note

The first attempt at regenerating the Phase 0/1 summaries destroyed content: the script
re-ran `report` + `aggregate` uniformly, but the committed summaries are heterogeneous —
some carry `analysis.hwfit` output, the instruction table carries two differently-flagged
`aggregate` sections, and several have `###` section headers. It was caught by the diff
being net-negative and reverted with `git checkout`. **The Phase 0/1 summaries therefore
do not yet carry the admitted-bias line**; they will pick it up whenever they are next
regenerated, and the corpus-wide figures live in `results/_crosscheck/instrument.txt`
instead. The real lesson is that `summary.txt` regeneration needs a per-session record of
the commands that built it, rather than a convention that has quietly drifted.
