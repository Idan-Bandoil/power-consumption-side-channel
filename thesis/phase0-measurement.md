# Measuring Package Power Well Enough to Trust It

*Draft — Phase 0 chapter. Every number here is reproducible from `results/` plus the
run manifests; the provenance for each claim is cited inline as a run directory.*

## 1. Why this chapter exists

The experiments in the rest of this thesis all reduce to the same measurement: run a
known workload on one core, read the package energy counter from another, and ask
whether the counter can tell which of two workloads was running. The quantity of
interest is a few hundred milliwatts on a package that idles above ten. At that scale
the instrument is not a detail of the method — it *is* the method, and an instrument
that is wrong in the right direction will manufacture whatever result the experimenter
expects.

This chapter documents the instrument. It has three parts: an account of two specific
ways the preliminary measurements for this work were wrong (§3), the rebuilt
measurement pipeline that replaced them (§4), and the set of automated validity gates
that every subsequent chapter's data must pass before it is reported (§5). §6 reports
what the rebuilt pipeline measures when pointed at the original preliminary claim.

The most useful result in this chapter is negative. The preliminary finding that
motivated the project — a ~4% package-power difference between AVX multiplies on
all-zero and all-ones operands — does not survive the corrected methodology in the form
it was originally stated. Establishing that, and establishing *why*, is what made the
much sharper result in the following chapter findable.

## 2. Platform and configurations

All measurements are taken on a single machine: an Intel Core i7-12700H (Alder Lake-H,
hybrid), running Linux 6.8. Six P-cores present as logical CPUs 0–11 in SMT pairs; eight
E-cores present as logical CPUs 12–19 without SMT. `/proc/cpuinfo` advertises `avx`,
`avx2` and `avx_vnni`. **There is no AVX-512** — it is fused off on consumer Alder Lake —
so instruction-width scaling is bounded at 256 bits throughout, and this is stated as a
limitation rather than worked around. AVX-VNNI *is* present, which matters in the ML
chapter because it is the instruction quantized int8 inference actually issues.

Energy is read from `MSR_PKG_ENERGY_STATUS` (0x611) via `/dev/cpu/N/msr`. The energy
unit on this part is 2⁻¹⁴ J = 61.035 µJ. The package RAPL domain here exposes
`package-0`, `core` and `uncore` but **no `dram` domain**, so DIMM energy is outside
every measurement in this thesis; DRAM-resident workloads are measured at the memory
controller with the memory devices' own consumption excluded. Both the MSR interface and
`/sys/class/powercap/.../energy_uj` require root. `scaling_cur_freq` does not, which is
what makes the unprivileged receiver of the covert-channel chapter possible.

Two standard configurations are used throughout and are not interchangeable:

- **Config-A (isolated leakage).** Turbo disabled, frequency pinned (2.3 or 2.7 GHz).
  DVFS is removed from the system, so a power difference is a power difference and not a
  frequency response to one. Every leakage-characterisation number in this thesis is
  Config-A.
- **Config-B (realistic).** Turbo on, default `powersave` governor with `intel_pstate`.
  Throttling is permitted. Required for the frequency- and timing-based receivers, which
  observe the DVFS response that Config-A deliberately removes.

The kernel is booted with `isolcpus=0`, isolating the monitor core. Victim cores
(2, 4, 6, 8, 10) are *not* isolated and still receive stray system work; extending the
isolation requires a boot-parameter change and is listed among the open gaps in §7.

## 3. Two ways the preliminary measurements were wrong

The preliminary data for this project consists of two campaigns, both retained
unmodified under `src/data/`. Re-analysing them with the tooling built for this chapter
identified two independent defects. Both are generic hazards of RAPL-based power
measurement rather than mistakes peculiar to this work, which is why they are documented
here at length.

### 3.1 Sequential conditions turn thermal drift into signal

In `src/data/out-1207-2115` (turbo on, ~30 000 samples per condition) the two operand
conditions were measured as two separate long runs, one after the other. Splitting each
condition into deciles of its own chronological order shows what that costs. Mean energy
per sample, in joules, by decile:

| condition | d1 | d2 | d3 | d4 | d5 | d6 | d7 | d8 | d9 | d10 |
|---|---|---|---|---|---|---|---|---|---|---|
| operand `0x00000000` | .0640 | .0565 | .0536 | .0503 | .0483 | .0480 | .0462 | .0456 | .0454 | .0450 |
| operand `0xFFFFFFFF` | .0459 | .0414 | .0323 | .0324 | .0325 | .0325 | .0326 | .0326 | .0327 | .0326 |

and the corresponding core frequency, in MHz:

| condition | d1 | d2 | d3 | d4 | d5 | d6 | d7 | d8 | d9 | d10 |
|---|---|---|---|---|---|---|---|---|---|---|
| operand `0x00000000` | 3843 | 3804 | 3807 | 3800 | 3796 | 3774 | 3752 | 3723 | 3714 | 3700 |
| operand `0xFFFFFFFF` | 3564 | 3479 | 3121 | 3118 | 3123 | 3130 | 3132 | 3134 | 3126 | 3136 |

Neither condition is stationary. The first drops 30% in energy per sample across its own
run; the second starts 280 MHz below where the first started, because it inherited a hot
package, and settles 560 MHz lower. The between-condition "effect" is 31% — vastly larger
than anything measured since, in the *opposite* direction to the hypothesis, and fully
accounted for by the chip heating into its power limit while the experiment ran. Any
statistical test applied to this dataset will report a large, highly significant effect.
It is entirely an artifact of measurement order.

This is the single most important negative lesson of the project: **statistical
significance offers no protection against this class of error at all.** A permutation
test on a drift-confounded dataset returns p < 0.001 while the true effect is zero,
because the test is asking whether the labels are exchangeable and, temporally, they are
not. The defence has to be in the experimental design — the conditions must be
interleaved, so that drift is common-mode — and it has to be *checked* mechanically,
which is what the `interleaving` gate in §5 does.

### 3.2 Sampling aliased against the RAPL update period, then the evidence was discarded

The second campaign, `src/data/out-1228-1527`, was taken under Config-A at 2.3 GHz
pinned, and it is credible: its deciles are flat to ±0.7% and the two conditions differ
by +4.33% (0.016680 J vs 0.017402 J per sample) uniformly across the run. This is the
"+0.8 W" preliminary result, and it is the one worth taking seriously.

It is nonetheless mis-sampled. The old driver waited a fixed number of cycles and then
read the energy counter. **27 458 of 300 000 samples (9.15%) read exactly zero** — the
sampling window was at or below the counter's update interval, so a window could fall
entirely between two updates and observe no change, and the following window then
collected two updates' worth. The per-sample coefficient of variation is 0.386, which at
these means is a standard deviation of roughly 6.4 W on a 16.6 W signal: the "noise" in
the preliminary data is almost entirely this aliasing, not any physical fluctuation.

The analysis script then made it worse by discarding the zero samples while keeping the
double-length windows that follow them. That is not a neutral filter. Recomputing both
ways: discarding zeros raises the mean energy per sample by **+10.1%** in each condition,
and it raises it in each condition by very nearly the same amount, so the *ratio* between
conditions happens to survive — but the mean is biased, the variance is misstated, and
nothing about that outcome was guaranteed. Had the two conditions aliased at different
rates, the discard alone would have produced a difference from nothing.

Two conclusions carry forward. Zero-energy samples are the visible symptom of a sampler
that is not synchronised to the counter, so their rate belongs in the validity gates.
And the fix is not a longer window — it is to stop guessing the window.

## 4. The rebuilt pipeline

### 4.1 Live operand switching through a shared control block

Victim threads are created with `clone(CLONE_VM | SIGCHLD)`, so they share the monitor's
address space and, with it, a single `struct ctl_t` control block. Each victim re-reads
`ctl->selector` between bursts of instructions rather than latching the operand once
before entering its loop. Writing one word from the monitor therefore re-tunes every
running victim within about 0.6 µs, with no thread teardown, no re-pinning, and no
allocation.

This is the mechanism the rest of the pipeline is built on. It is what makes
condition interleaving cheap enough to do at block granularity (§4.2), and it is also,
unchanged, the transmitter primitive for the covert channel in the later chapter — the
attacker modulates a shared word, and the victims' power draw follows it.

### 4.2 Interleaved, seeded-shuffle block design

A run is not one long block per condition. It is `blocks_per_condition × conditions`
short blocks — typically 150 blocks of 120 samples per condition, so ~0.12 s per block —
emitted in an order produced by a seeded shuffle. Thermal state, frequency state and
whatever the operating system is doing at that moment are shared by adjacent blocks, so
they enter both conditions in the same proportion and cancel in the difference. The seed
is recorded in the manifest, so block order is reproducible.

Every output row carries its block index and condition, and all downstream statistics
resample **blocks, not samples** (§4.5).

### 4.3 Edge-triggered sampling

Instead of sampling on a fixed schedule, the monitor idles for 7/8 of the current
estimate of the RAPL update period, then tight-polls `MSR_PKG_ENERGY_STATUS` until the
value changes, and records the energy increment together with the TSC interval over
which it accrued. Because the idle fraction stays strictly below one period, an update
can never be slept through. Energy is attributed to the interval it was actually
measured over, rather than to an assumed window.

This eliminates the aliasing of §3.2 by construction: **the zero-sample rate is 0.00% on
every run taken with this sampler**, against 9.15% before, and per-sample dispersion falls
from a coefficient of variation of 0.386 to 0.055 — in absolute terms from about 6.4 W
to 0.65–0.79 W standard deviation, on means of 16.6 W and 12.1 W respectively. The old
fixed-window sampler is retained behind `--mode fixed` so the
comparison can be re-run.

The sampler also measures the counter's update period directly, which is a small result
in its own right. Across the 82 runs analysed for this thesis the measured period is
**0.9990 ms (SD 0.0065, range 0.9816–1.0171)**. The datasheet-implied period of 2⁻¹⁰ s =
0.9766 ms is 2.3% shorter, far outside the observed spread, and the TSC is calibrated
against `CLOCK_MONOTONIC` at each run, which is accurate to parts per million. The update
interval on this part is therefore ~1.000 ms and not 2⁻¹⁰ s.

A residual behaviour worth recording: the sampler occasionally overshoots, polling
through one update into the next. Overshoots are bimodal per run — either ~0.1% or ~4% of
edges, never in between — and uncorrelated with the victim, so the sampler appears to
phase-lock to the counter in one of two regimes and stay there for the whole run. Within
a run they are balanced across conditions (worst observed imbalance ±0.43%, sign varying)
and the mean sampled interval is 1.00 periods for both conditions, so they are
common-mode and cannot bias a difference. They cost time resolution, not correctness.
The count is reported in every manifest as `rapl_overshoots`.

### 4.4 Manifests and reproducibility

Each run writes `results/<timestamp>-<name>/` containing one CSV per condition set and a
`manifest.json` recording the git commit and dirty state, every driver argument including
the operands and the shuffle seed, turbo and governor state, PL1/PL2 limits, package
temperature before and after, per-CPU frequencies, the calibrated TSC frequency, the
measured RAPL period and overshoot count, and achieved victim throughput. Experiments
themselves are declarative JSON under `experiments/`, so a run is described by a file
under version control rather than by a shell history.

Privilege is deliberately split: the runner is stdlib-only and runs as root; the analysis
package needs numpy and runs unprivileged afterwards, and the runner hands output
ownership back on exit. Interruption handling is explicit — turbo is restored and
ownership returned on SIGINT and SIGTERM — because an earlier version could leave the
laptop throttled and the results root-owned if a session was torn down mid-run.

### 4.5 Statistics on blocks

Samples within a block share a thermal state, a frequency state and a scheduling state.
Treating 300 000 such samples as independent will produce a confidence interval narrow
enough to "prove" anything at all. All inference is therefore at block level: a block
bootstrap for confidence intervals on the mean difference, a block permutation test that
relabels whole blocks, and a threshold detector evaluated on block-level means.

The detector is reported as an accuracy-versus-*n* curve, where *n* is the number of
samples averaged before thresholding. This is chosen deliberately: *n* to reach a target
accuracy converts directly into a symbol period, and hence into a covert-channel bit
rate, so the characterisation chapter's output is already in the units the channel
chapter needs. The detector is a mean threshold and is by construction blind to effects
that live in variance rather than in mean — an acknowledged limitation (§7).

## 5. Validity gates

Three gates are enforced automatically by `analysis/report.py`, which exits non-zero on
failure. No result appears in this thesis without them.

| Gate | Threshold | What it catches |
|---|---|---|
| `zero_ticks` | ≤1% of samples read zero energy | Sampler aliasing against the RAPL update interval (§3.2; was 9.15%) |
| `interleaving` | temporal imbalance ≤0.10 | Conditions measured at different times, letting drift pose as effect (§3.1; a sequential design scores ~0.50) |
| `aa_*` | CI contains zero **and** detector accuracy ≤0.60 | The measurement path manufacturing an effect out of nothing |

The A/A control needs no special code path: it is an ordinary experiment with the same
operand in both conditions, so it exercises the entire pipeline — the same victim, the
same interleaving, the same statistics — on a difference that is zero by construction.
Every A/B claim in this thesis ships with one.

That the gates are *design* checks rather than *outcome* checks is worth demonstrating.
`experiments/phase0_artifact_demo.json` runs an A/A control with `--order sequential`,
i.e. the flawed design of §3.1, on a warm machine under Config-A. The measured difference
is −0.013 W with p = 0.74: the design did not, on this occasion, produce a spurious
effect. The `interleaving` gate fails it anyway (imbalance 0.502; conditions occupy mean
positions 0.25 and 0.75 of the run), because the design is unsound whether or not it
happened to bite. The shuffled A/A run in the same session scores 0.061 and passes. The
gate is therefore not redundant with the p-value; it rejects a class of experiment, not a
class of number.

One further methodological result belongs here, established in the characterisation work
that followed. **A single run's bootstrap confidence interval is optimistic, and the
larger the effect, the worse it is.** Comparing each run's own 95% CI half-width against
the standard deviation of the same quantity across repeated runs gives ratios of 0.19–2.42
across the traffic-volume experiments, and 5.8–6.8 for the two largest effects measured
(≈1.85 W), where a ±15 mW within-run interval sits inside a ±100 mW between-run spread.
The plausible explanation is that the effect itself scales with a thermal and frequency
state that is constant within a run and varies between them. The operational rule adopted
for the rest of the thesis is that **no single run's CI is quoted as an error bar**;
every reported effect is aggregated over at least three repeats, with victim order
reshuffled between repeats so that position in the session is not confounded with victim.

## 6. What the rebuilt pipeline measures

Validation run `results/20260822-192822-phase0_validate`, Config-A, 2.3 GHz pinned,
`avx2_mul` (`vpmuludq` on `ymm` registers), four victim threads, 36 000 samples over 300
blocks per condition:

| run | operands | Δ power | 95% CI | perm. p | detector | gate |
|---|---|---|---|---|---|---|
| `aa_control` | `0xFFFFFFFF` vs `0xFFFFFFFF` | −0.052 W | [−0.119, +0.007] | 0.10 | 0.541 | A/A **PASS** |
| `hw_0_vs_32` | `0x00000000` vs `0xFFFFFFFF` | **+0.0025 W** | [−0.065, +0.075] | 0.95 | 0.498 | — |

Both runs report 0.00% zero-tick samples and an interleaving imbalance of 0.022. The A/A
control behaves: its interval contains zero and its detector is at chance. And the
preliminary effect is gone. The operand Hamming-weight difference that measured +4.33%
in `out-1228-1527` measures +0.02% here, at a detector accuracy of 0.498, with roughly
nine times less measurement noise than the run that originally showed it. The correct
comparison is not against zero but against the A/A control on the same victim, which
lands at −0.052 W; the register-resident victim is at the harness noise floor and cannot
be distinguished from it.

The preliminary result was therefore not reproducible **as stated** — as a property of
the AVX multiplier acting on register-resident operands. §3.2 explains part of the
original number (a mis-sampled, zero-discarding measurement path), but not all of it: the
1228 run's deciles are flat and its conditions do differ. The resolution is that the two
victims are not the same workload. The original was compiled at `-O0` and spilled its
operands to memory every iteration; the rebuilt victim keeps them in registers. What
differs between them is not the arithmetic but the operand movement — which is the
subject of the next chapter, and a considerably more specific claim than the one it
replaces.

## 7. Known gaps

Stated rather than worked around:

- **Victim cores are not isolated.** `isolcpus=0` covers the monitor core only; victim
  cores 2, 4, 6, 8, 10 still receive stray system work. Extending it requires a
  bootloader change and a reboot.
- **Cache residency is not measured directly.** `perf_event_paranoid=4` makes hardware
  counters unavailable unprivileged on this machine, so working-set claims are
  corroborated indirectly, by checking achieved bandwidth against each cache level's
  expected ceiling. That is weaker than a counter.
- **The detector is a mean threshold** and cannot see an effect that lives purely in
  variance.
- **`turbostat` cross-validation of the RAPL integration is not yet done.** It is in the
  verification plan and remains open.
- **A run-level startup transient is possible for large working sets.** The `settle`
  parameter discards samples at the start of each *block*, not blocks at the start of a
  *run*. A victim that faults in hundreds of megabytes under `MAP_POPULATE` can therefore
  carry a transient into the first few blocks: one observed instance drew +2.75 W across
  the first ~4 blocks and moved that run's A/A estimate anywhere between +8 and −37 mW
  depending on how many blocks were dropped. Runs since have not shown it, which is luck
  rather than a fix.
- **No DRAM RAPL domain** on this part, as noted in §2.

## 8. What this buys

The pipeline reduced per-sample dispersion by roughly a factor of nine, removed a 9% class
of corrupt samples entirely, made thermal drift common-mode by design, and put three
mechanical gates between an experiment and a reported result. It also cost the project
its headline preliminary finding, which is the strongest evidence available that the
gates do something: the first result the corrected instrument was pointed at was the
project's own, and it did not survive.

Everything reported after this chapter passes all three gates, ships with an A/A control,
and is aggregated over at least three repeats with randomised run order.
