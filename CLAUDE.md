# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Thesis research on power side channels from AVX instructions on an Intel i7-12700H (see `Research Proposal.pdf`). Victim threads run a tight AVX loop over an attacker-chosen operand; a monitor pinned to a different core samples package energy through Intel RAPL MSRs plus the APERF/MPERF frequency ratio. The question is whether operand Hamming weight is recoverable from power.

The working plan is at `~/.claude/plans/resilient-squishing-spindle.md`: Phase 0 measurement infrastructure, Phase 1 leakage characterisation, Phase 2 covert channel, Phase 3 ML inference leakage, Phase 4 mitigations.

Hardware facts that constrain everything: P-cores are logical CPUs 0-11 (SMT pairs), E-cores 12-19. `/proc/cpuinfo` shows `avx avx2 avx_vnni` — **no AVX-512** (fused off on consumer Alder Lake). RAPL MSRs and `/sys/class/powercap/.../energy_uj` are root-only; `scaling_cur_freq` is world-readable. Kernel cmdline has `isolcpus=0`.

## Where things stand (last updated 2026-08-24)

**Phase 0 is complete.** The measurement pipeline was rebuilt and validated; see *Findings so far* below for results and *Validity gates* for what every claim must pass.

**Phase 1 is in progress, with a changed target.** The plan's Phase 1 was written to characterise the *instruction mix* (Hamming weight sweep, HW vs Hamming distance, per-instruction leakage table). Phase 0 found the register-only `vpmuludq` victim does not leak at all, while victims that move the same operand through memory do — so Phase 1 now characterises **operand movement** first. The instruction-family work still matters, but it should be done on a victim that has memory traffic, or it will measure a null.

Done so far in Phase 1: the load/store 2×2 (`experiments/phase1_memory_traffic.json`), replicated with randomised run order (`phase1_memory_replication.json`); the traffic-volume and working-set-depth sweep (`phase1_traffic_volume.json`, analysed in `results/20260824-213524-*`); and the polarity control (`phase1_polarity.json`, `results/20260824-222234-*`) that cleared the A/A anomaly that sweep raised. **Leakage tracks distance travelled, not instruction count** — see *Findings so far*.

Next, roughly in order:
1. Hamming-weight sweep and HW-vs-Hamming-distance, on a traffic-bearing victim. Use a working-set victim (`ws_l3_x8` is the best-conditioned: +1.85 W, detector 1.000), not a register-resident one.
2. Per-instruction table, likewise.
3. Then Phase 2 (covert channel) as planned — the `ctl->selector` live-switch is already the transmitter primitive. `ws_l3_x8` under Config-A hits 95% detector accuracy at n=1–8 samples and 99% at n=2–13 across eight runs, i.e. roughly 125–1000 bit/s raw at a ~1 ms RAPL period. That is the ceiling figure to quote, and it is an order of magnitude better than `ws_dram_x8` (n=13–89 for 95%), which is worth remembering when picking a Phase 2 transmitter: largest Δ power is not the same as best detectability.

Worth doing when convenient, neither blocking: replicate the unexplained `ws_dram_x8` A/A offset (below) on its own, and add a run-level warmup discard for large-working-set victims.

Known gaps deliberately left open:
- `isolcpus=0` only isolates the attacker core; victim cores 2,4,6,8,10 still take stray work. Extending it needs a GRUB edit and reboot, and has not been done.
- `analysis/stats.py`'s detector is a mean threshold. It cannot see effects that live in variance rather than mean, which `avx2_load` briefly looked like it might have.
- `perf` is unusable unprivileged here (`perf_event_paranoid=4`), so cache residency is not measured directly. It is now corroborated indirectly: achieved bandwidth matches each level's expected ceiling (68 B/cycle/core for the L1 variant against Golden Cove's 3×32 B peak, 34 B/cycle for L2, 42 GB/s aggregate for DRAM ≈ 55% of DDR5-4800 dual-channel). That is weaker than a counter but it is not nothing.
- **The `ws_dram_x8` A/A offset is unexplained.** Its three repeats in the traffic-volume sweep read +18.6, +10.1, +17.5 mW with both selectors identical, and one repeat failed the `aa_*` gate. It is real energy (in `ticks`, not `dtsc`), uniform across the run (dropping the first 20 blocks leaves it unchanged, so it is not the startup transient below), and absent from eight `ws_l3_x8` A/A runs. So it is victim- or session-specific, not a bias in the measurement path — the polarity control settles that. It is 1.8% of that victim's effect. Replicating it alone, ~6 repeats in a fresh session, would close it.
- **Large working sets can carry a run-level startup transient that `settle` cannot remove.** `ws_init` faults in 2 × 32 MB per thread under `MAP_POPULATE` — 256 MB for a 4-thread `ws_dram_x8` run. In `results/20260822-234811-*` that showed as +2.75 W across the first ~4 blocks, and moved that run's A/A estimate anywhere from +8 to −37 mW depending on how many blocks were dropped. `settle` discards *samples per block*, not blocks per run, so it is no defence. Later runs happen not to show it, which is luck. A `--warmup-blocks` flag, or touching both slots in `ws_init` before sampling starts, would fix it.

## Running an experiment

Two halves, deliberately split by privilege. The runner is stdlib-only so it needs no venv; analysis needs numpy/matplotlib so it runs unprivileged afterwards.

```bash
sudo python3 src/experiment_runner.py experiments/phase0_validate.json
./venv/bin/python3 -m analysis.report results/<run_id>
```

Experiments are declarative JSON in `experiments/`. Any key in `DRIVER_FLAGS` (`src/experiment_runner.py`) may be set experiment-wide under `driver` and overridden per run. `config: "A"` disables turbo (pins frequency, isolates power leakage from DVFS); `config: "B"` leaves turbo on (**required** for frequency- and timing-based receivers, which have nothing to observe under Config-A).

Each run writes `results/<timestamp>-<name>/` containing one CSV per run plus `manifest.json` (git commit, every driver argument, turbo/governor state, PL1/PL2, package temperature before and after, per-CPU frequencies). Ownership is handed back to `SUDO_UID` on exit.

### Driver by hand

```bash
cd src && make
printf '0\n4294967295\n' > input.txt
sudo ./bin/driver --victim avx2_mul --threads 4 --blocks 100 --samples 100
./bin/driver --list-victims     # no root needed
./bin/driver --help
```

`make clean` removes `bin/` and `obj/` only — never measurement output. `bin/hybrid_detect` reports per-CPU core type via CPUID leaf 0x1A.

`python3 tests/test_runner_cleanup.py` checks that an interrupted run still restores turbo and hands its output back. Stdlib-only, unprivileged, and every machine-touching function is stubbed, so it is safe to run while an experiment is in flight.

`cd src && make check` runs `tests/victim_smoke.c` against every victim — no root needed. It verifies each one actually spins, picks up a live `ctl->selector` write, and exits cleanly when `ctl->run` clears. This is the only pre-flight check that does not need MSR access, and it is what catches mis-assembled instructions (see the AVX-VNNI note below). The validity gates cover experiment correctness.

## Architecture

**`util/util.{c,h}`** — `struct ctl_t` is the shared control block, and the reason the design works: victims are cloned with `CLONE_VM`, so writing `ctl->selector` re-tunes every running victim within one burst (~0.6 µs) with no thread teardown. That is both the condition-interleaving mechanism and the covert-channel transmitter primitive Phase 2 needs. Also holds `parse_args` (getopt_long), selector-file parsing, and a seeded xorshift PRNG so block order is reproducible from the logged seed.

**`util/victim-utils.c`** — every victim is a `DEFINE_VEC_VICTIM` macro instantiation wrapping an inline-asm loop, 8 independent destinations deep so the loop is throughput-bound rather than latency-bound. Victims re-read `ctl->selector` between bursts of `8 * AVX_BURST` instructions. Adding one is a single table edit; `NUM_VICTIMS` is computed from the table, so the old three-places-to-edit footgun is gone. `avx2_vnni` is guarded by `#ifdef __AVXVNNI__`.

Deliberate contrasts in the victim set: `vpand`/`vpor` are identity on equal inputs (result Hamming weight tracks the operand) while `vpxor` always yields zero (result HW pinned at 0) — comparing them separates input- from output-driven leakage. `scalar_rol` preserves Hamming weight indefinitely, varying only bit position.

**`util/freq-utils.c`** — APERF/MPERF ratio scaled by `MSR_PLATFORM_INFO`; `set_frequency_units()` must run before any frequency read. `frequency_cpufreq()` reads world-readable sysfs and is the basis of the Phase 2 unprivileged receiver.

**`util/rapl-utils.c`** — general RAPL wrapper, still linked but unused by the driver, which preads `MSR_PKG_ENERGY_STATUS` inline to keep the sampling loop tight.

**`src/driver.c`** — the monitor is the main thread (pinned, priority −20); victims are `clone(CLONE_VM | SIGCHLD)` children on 64 KB stacks, each with its own `victim_args_t`. Two things matter most:

- *Interleaving.* A run is `blocks_per_condition × conditions` short blocks in seeded-shuffled order, not one long block per condition. This makes thermal drift common-mode. Getting this wrong is what made `src/data/out-1207-2115` unusable.
- *Edge-triggered sampling.* Rather than a fixed busy-wait window, the sampler idles for 7/8 of the estimated RAPL period then tight-polls until the counter changes, recording the exact energy increment and its TSC interval. Staying below 1.0 of the period means an edge can never be slept through. `--mode fixed` restores the old fixed-window sampler for comparison.

TSC frequency is calibrated once against `CLOCK_MONOTONIC`; without it the analysis cannot convert energy per edge into watts. Progress goes to stderr, a JSON summary to stdout which the runner folds into the manifest.

**Output schema** — `block,cond,ticks,dtsc,daperf,dmperf`, one row per sample. `ticks` is raw RAPL energy units (`uint32` subtraction, so counter wraparound is handled); power is `ticks * energy_unit / (dtsc / tsc_hz)`.

**`analysis/`** — numpy-only (this venv has no scipy or sklearn). `stats.py` resamples **blocks, not samples** throughout: samples within a block share a thermal and frequency state, so treating 300k correlated samples as independent will "prove" anything. Contains block bootstrap, block permutation test, `temporal_balance`, and a threshold detector whose accuracy-vs-*n* curve converts directly into covert-channel bit rate.

## Findings so far (2026-08-24)

**The leakage is in operand movement, not the vector ALU.** Same instruction (`vpmuludq`), same operands (0 vs 0xFFFFFFFF), same interleaved methodology — only the surrounding memory traffic differs. Replicated over 3 repeats with victim order reshuffled each repeat (`results/20260822-215306-phase1_memory_replication`, all 39 gates pass):

| victim | traffic | Δ power | between-run SD | sign | detector |
|---|---|---|---|---|---|
| `avx2_mul` | none (register-resident) | −0.06 W | 0.034 | all − | 0.57 |
| *(A/A control)* | *none — true zero* | *−0.06 W* | *0.075* | *flips* | *0.52* |
| `avx2_load` | loads only, **no ALU** | +0.21 W | 0.017 | all + | 0.85 |
| `avx2_mul_st` | multiply + stores | +0.32 W | 0.049 | all + | 0.93 |
| `avx2_mul_ld` | loads + multiply | +0.34 W | 0.025 | all + | 0.94 |
| `avx2_mul_ldst` | loads + multiply + stores | **+0.51 W** | 0.049 | all + | **0.98** |

Read `avx2_mul` against the A/A row, not against zero: the A/A control has no effect by construction and still lands at −0.06 W, so the register-only victim is at the harness noise floor. **Register-resident operands do not measurably leak.**

`avx2_load` does no arithmetic whatsoever and still leaks +0.21 W with the tightest between-run spread of any victim. Loads and stores are roughly additive: 0.21 (loads) + 0.32 (stores+mul) ≈ 0.51 (both).

This reframes Phase 1 toward characterising *operand movement*, not instruction mix. It also explains the proposal's Eigen/TensorFlow sparsity figures — those stream large matrices through memory — while a register-resident microbenchmark shows nothing.

**Leakage per byte scales with how far the operand travels.** The traffic-volume sweep (`results/20260824-213524-phase1_traffic_volume`, 3 repeats, 24 runs) holds the instruction stream fixed at 8 `vmovdqa` loads per iteration and varies only the working set. Because the variants do not move data at equal rates, the comparable quantity is energy per byte, not watts — and `mW/(GB/s)`, the column `analysis.aggregate` already prints, *is* pJ/byte:

| victim | working set | GB/s | Δ power | **pJ/byte** | detector |
|---|---|---|---|---|---|
| `ws_l1_x8` | 16 K (L1) | 741 | +0.23 W | **0.31** | 0.76 |
| `ws_l2_x8` | 512 K (L2) | 330 | +1.10 W | **3.35** | 0.96 |
| `ws_l3_x8` | 4 M (L3) | 148 | +2.05 W | **13.88** | 0.996 |
| `ws_dram_x8` | 32 M (DRAM) | 41 | +0.87 W | **21.02** | 0.998 |

A ~68× rise from L1-resident to DRAM-resident, monotone, and reproduced across two sessions with different thermal histories and different shuffles (the 22nd's partial sweep gives 0.40 / 3.36 / 14.80 / 22.73). **The absolute watt difference peaks at L3, not DRAM** — throughput falls faster than per-byte cost rises — which is exactly why the per-byte normalisation is the right frame and why ranking victims by watts alone would mislead.

Note the package RAPL domain does not include the DIMMs (this part exposes `package-0`, `core`, `uncore` only, no `dram`), so the DRAM figure is measured at the memory controller with the DRAM chips' own energy excluded. The true operand-movement cost at that depth is larger than the table says.

The volume axis is the weaker one and is non-monotone — 0.50 / 1.10 / 1.05 / 0.31 pJ/byte for 1/2/4/8 loads per iteration — because throughput saturates (217 → 741 GB/s, not 8×), so "loads per iteration" is not a clean dose axis. Build on the depth axis.

**The condition-index bias is zero; the effect is a genuine operand effect.** That sweep's `ws_dram_x8` A/A failed its gate (+15.4 mW, all three repeats positive), and because every experiment so far used selectors `[0, HW32]` in that order, a real effect *E* and a harness bias *B* tied to the condition index were perfectly confounded — both add to cond 1. `experiments/phase1_polarity.json` separates them by also running the reverse mapping: forward gives *+E + B*, reverse gives *−E + B*, so `(fwd−rev)/2 = E` and `(fwd+rev)/2 = B` (`results/20260824-222234-phase1_polarity`, 4 repeats, 40/40 gates pass):

| | value |
|---|---|
| `l3_forward` | +1.841 W |
| `l3_reverse` | −1.855 W |
| **E** = (fwd−rev)/2 | **+1.848 W** |
| **B** from 8 A/A runs | **+0.7 ± 3.1 mW**, 95% CI [−5.4, +6.8] |

The effect flips sign cleanly and symmetrically; the sweep's +15.4 mW sits 4.7 SE outside the A/A interval. Both A/A flavours (all-zero and all-ones operands) come out at zero and flip sign across repeats, so *B* does not depend on the operand value either. **No bias correction is warranted** — the depth table above stands as measured. Design note for reuse: estimate *B* from A/A runs, not from `(fwd+rev)/2`. On a victim with *E* ≈ 2 W the between-run SD is ~100 mW, so that average has SE ≈ 27 mW over four repeats and cannot resolve 15 mW, while A/A runs (between-run SD 4.6–10 mW) reach ~3 mW.

Methodology notes carried forward:
- **Within-run CIs are optimistic, and badly so for large effects.** This note previously said between-run spread was *smaller* than within-run CIs (ratio 0.26–0.84); that generalised from small effects and does not hold. The traffic-volume sweep gives ratios of 0.19–2.42, and the polarity runs give **5.8–6.8** for `l3_forward`/`l3_reverse` — a single run's ±15 mW bootstrap CI sits inside a between-run spread of ±100 mW. The bigger the effect, the worse the ratio, presumably because the effect itself scales with a thermal/frequency state that varies between runs while being constant within one. Never quote a single run's CI as the error bar; `analysis.aggregate` over ≥3 repeats is the minimum.
- **Randomise victim order across repeats.** Interleaving cancels drift *within* a run; comparing effects *across* runs is separately confounded with position in the session. The first, fixed-order run had `mul_load_store` last, and it also had the largest effect. Reshuffling reproduced the ordering, so it was not an artifact — but the check was needed. `repeats` + `shuffle_runs` in the experiment spec handle this.
- A deliberately sequential A/A (`experiments/phase0_artifact_demo.json`, `--order sequential`) did **not** reproduce a spurious effect on a warm machine under Config-A (−0.013 W, p=0.74), so the thermal-step story does not by itself explain the old +0.8 W. The `interleaving` gate still correctly failed that run's design.
- **Sampler overshoots are harmless — question closed.** They are bimodal per run (either ~0.1% or ~4% of edges, never between) and uncorrelated with victim, so the sampler phase-locks to the RAPL update in one of two regimes and stays there for a whole run. Within a run they are balanced across conditions (worst imbalance ±0.43%, sign flips) and mean `dtsc`/period is 1.00 for both conditions, so they are common-mode and cannot bias an A/B difference. They cost time resolution, not correctness. `rapl_overshoots` is still in every manifest.

## Validity gates

`analysis/report.py` enforces these and exits non-zero on failure. No result belongs in the thesis without them:

| Gate | Threshold | Catches |
|---|---|---|
| `zero_ticks` | ≤1% of samples read zero energy | Sampler aliasing against the RAPL update interval (was 9.2%) |
| `interleaving` | temporal imbalance ≤0.10 | Conditions measured at different times, letting drift pose as effect. A sequential design scores ~0.50 |
| `aa_*` | CI contains 0 **and** accuracy ≤0.60 | The whole measurement path manufacturing an effect from nothing |

An A/A control is just an experiment with the same selector in both conditions — no special code path. Every A/B claim should ship with one.

## Gotchas

- **A "significant" result is not a real one.** A drift-confounded dataset will pass a permutation test with p<0.001 while having no true effect. The `interleaving` gate, not the p-value, is what rules that out.
- **`isolcpus=0` only isolates the attacker core.** Victim cores 2,4,6,8,10 still take stray work. Extending to `isolcpus=0,2,4,6,8,10` needs a GRUB edit and reboot.
- **`-O2` is safe only because every victim hot loop is inline asm.** Do not add a plain-C victim without making its result `volatile`, or the compiler will delete the work being measured.
- **A killed run used to leave the laptop throttled and the results root-owned.** Ctrl-C was always fine; a plain `kill`, a closed terminal or a session teardown was not, because SIGTERM had no handler and skipped both `restore()` and `give_back()`. Fixed — see `tests/test_runner_cleanup.py` for the exact boundary. SIGKILL still cannot be caught by anything, so `sudo src/experiment_runner.py --restore-only` remains the recovery path: it re-enables turbo and hands ownership back, and refuses to touch turbo while a `driver` is still running.
- **`settle` is samples-per-block, not blocks-per-run.** It does not protect against a run-level startup transient, which large working sets do produce — see *Known gaps*. When adding a victim that allocates hundreds of MB, check the first few blocks against the run's steady state before trusting anything.
- **Config-A and Config-B are not interchangeable.** Pinning frequency removes the DVFS response that the Phase 2 tier-2/tier-3 receivers depend on entirely.
- **AVX-VNNI must be assembled as VEX, not EVEX.** `vpdpbusd` exists in both AVX-VNNI (VEX) and AVX512-VNNI (EVEX); gas defaults to EVEX, which SIGILLs here. Hence the `%{vex%}` prefix in `util/victim-utils.c` — spelled with `%` escapes because bare braces mean dialect alternatives to GCC. Any new dual-encoded instruction needs the same treatment; verify with `objdump -d util/victim-utils.o` (VEX starts `c4`, EVEX `62`).
- `legacy/` holds the superseded pipeline; see `legacy/README.md` for why it no longer runs. `src/data/` and `src/plot/` are pre-2026 outputs kept for provenance.
