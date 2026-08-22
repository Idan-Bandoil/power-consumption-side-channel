# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Thesis research on power side channels from AVX instructions on an Intel i7-12700H (see `Research Proposal.pdf`). Victim threads run a tight AVX loop over an attacker-chosen operand; a monitor pinned to a different core samples package energy through Intel RAPL MSRs plus the APERF/MPERF frequency ratio. The question is whether operand Hamming weight is recoverable from power.

The working plan is at `~/.claude/plans/resilient-squishing-spindle.md`: Phase 0 measurement infrastructure, Phase 1 leakage characterisation, Phase 2 covert channel, Phase 3 ML inference leakage, Phase 4 mitigations.

Hardware facts that constrain everything: P-cores are logical CPUs 0-11 (SMT pairs), E-cores 12-19. `/proc/cpuinfo` shows `avx avx2 avx_vnni` — **no AVX-512** (fused off on consumer Alder Lake). RAPL MSRs and `/sys/class/powercap/.../energy_uj` are root-only; `scaling_cur_freq` is world-readable. Kernel cmdline has `isolcpus=0`.

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

## Findings so far (2026-08-22)

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

Methodology notes carried forward:
- **Between-run spread is smaller than within-run CIs** for every traffic-bearing victim (ratio 0.26–0.84 in `analysis.aggregate`). An earlier worry that it exceeded them came from `avx2_mul` flipping sign between runs, which is just a null effect behaving like one. Still replicate: `analysis.report` alone cannot show this.
- **Randomise victim order across repeats.** Interleaving cancels drift *within* a run; comparing effects *across* runs is separately confounded with position in the session. The first, fixed-order run had `mul_load_store` last, and it also had the largest effect. Reshuffling reproduced the ordering, so it was not an artifact — but the check was needed. `repeats` + `shuffle_runs` in the experiment spec handle this.
- A deliberately sequential A/A (`experiments/phase0_artifact_demo.json`, `--order sequential`) did **not** reproduce a spurious effect on a warm machine under Config-A (−0.013 W, p=0.74), so the thermal-step story does not by itself explain the old +0.8 W. The `interleaving` gate still correctly failed that run's design.
- Sampler overshoots are usually <1% of edges but hit 2.7–3.6% in 3 of 18 runs, uncorrelated with victim. Worth watching; `rapl_overshoots` is in every manifest.

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
- **Config-A and Config-B are not interchangeable.** Pinning frequency removes the DVFS response that the Phase 2 tier-2/tier-3 receivers depend on entirely.
- **AVX-VNNI must be assembled as VEX, not EVEX.** `vpdpbusd` exists in both AVX-VNNI (VEX) and AVX512-VNNI (EVEX); gas defaults to EVEX, which SIGILLs here. Hence the `%{vex%}` prefix in `util/victim-utils.c` — spelled with `%` escapes because bare braces mean dialect alternatives to GCC. Any new dual-encoded instruction needs the same treatment; verify with `objdump -d util/victim-utils.o` (VEX starts `c4`, EVEX `62`).
- `legacy/` holds the superseded pipeline; see `legacy/README.md` for why it no longer runs. `src/data/` and `src/plot/` are pre-2026 outputs kept for provenance.
