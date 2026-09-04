# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Thesis research on power side channels from AVX instructions on an Intel i7-12700H (see `Research Proposal.pdf`). Victim threads run a tight AVX loop over an attacker-chosen operand; a monitor pinned to a different core samples package energy through Intel RAPL MSRs plus the APERF/MPERF frequency ratio. The question is whether operand Hamming weight is recoverable from power.

The working plan is at `~/.claude/plans/resilient-squishing-spindle.md`: Phase 0 measurement infrastructure, Phase 1 leakage characterisation, Phase 2 covert channel, Phase 3 ML inference leakage, Phase 4 mitigations.

Hardware facts that constrain everything: P-cores are logical CPUs 0-11 (SMT pairs), E-cores 12-19. `/proc/cpuinfo` shows `avx avx2 avx_vnni` — **no AVX-512** (fused off on consumer Alder Lake). RAPL MSRs and `/sys/class/powercap/.../energy_uj` are root-only; `scaling_cur_freq` is world-readable. Kernel cmdline has `isolcpus=0`.

## Where things stand (last updated 2026-09-04)

**Phase 0 is complete.** The measurement pipeline was rebuilt and validated; see *Findings so far* below for results and *Validity gates* for what every claim must pass.

**Phase 1 is in progress, with a changed target.** The plan's Phase 1 was written to characterise the *instruction mix* (Hamming weight sweep, HW vs Hamming distance, per-instruction leakage table). Phase 0 found the register-only `vpmuludq` victim does not leak at all, while victims that move the same operand through memory do — so Phase 1 now characterises **operand movement** first. The instruction-family work still matters, but it should be done on a victim that has memory traffic, or it will measure a null.

Done so far in Phase 1: the load/store 2×2 (`experiments/phase1_memory_traffic.json`), replicated with randomised run order (`phase1_memory_replication.json`); the traffic-volume and working-set-depth sweep (`phase1_traffic_volume.json`, analysed in `results/20260824-213524-*`); and the polarity control (`phase1_polarity.json`, `results/20260824-222234-*`) that cleared the A/A anomaly that sweep raised. **Leakage tracks distance travelled, not instruction count** — see *Findings so far*.

The operand-structure sweeps are done: `phase1_hamming_weight.json` (11 runs × 3 repeats), `phase1_nonzero_baseline.json` which ruled out a per-contrast offset, and `phase1_low_end.json` which settled the shape of the curve near zero. Read a sweep with `analysis.hwfit`, which fits the slope per repeat and takes the between-repeat spread as the error bar; `--axis hd` reads a `ws_*_ab` sweep as a Hamming *distance* instead. **Leakage is linear in Hamming weight above a discontinuity at zero** — see *Findings so far*.

`experiments/phase1_hamming_distance.json` separated the weight and switching models, which every earlier measurement confounds: **both are real** — see *Findings so far*. Read that kind of sweep with `analysis.hwfit --axis hd --labels 'hd*'`.

`experiments/phase1_instruction_table.json` closed the last item: **the instruction matters, but far less than movement does**, and register-resident leakage turns out to be instruction-dependent — see *Findings so far*. Read a table whose rows share a large common term with `analysis.aggregate --against LABEL [--per-byte]`.

**Phase 1's experiments are done.** Items 5–7 of the plan (width, core type, thread scaling) remain optional against the time budget; core type is the cheapest novelty of the three. `thesis/phase1-leakage.md` is a full first draft, 13 sections, no stubs.

**Phase 2 (covert channel): all three receiver tiers work.** `src/covert/tx.c` is the unprivileged transmitter; `rx_rapl.c` (root, Config-A), `rx_freq.c` (unprivileged, Config-B) and `rx_timing.c` (**reads nothing at all**, Config-B) are the receivers; `analysis/covert.py` decodes and `tests/test_covert_decode.py` pins the framing maths to synthetic traces. Tier 1 carries 241 bit/s of capacity, tier 2 1.1 bit/s at 2 bit/s raw, tier 3 without observed errors at 1 bit/s and level with tier 2 at 2 — see *Findings so far*. Run any of them with the runner's `"kind": "covert"` mode (`rx.tier` picks the receiver) and read with `analysis.covert`.

**`thesis/critique.md` is the work order, and items 1, 2 and 5 of it are done.** Item 5 (gates and instrumentation) was taken ahead of item 3 deliberately, so the next measurement session records the new gates rather than needing a re-run to acquire them.

**The next thing to run is item 3**, and it is written and validated but not executed: `experiments/phase1_depth_operand.json`, about 90 minutes.

```bash
sudo -n /home/idan/Desktop/power-consumption-side-channel/src/experiment_runner.py experiments/phase1_depth_operand.json
```

It settles a contradiction the chapter currently carries. §4 says cost per byte rises ~68× from L1 to DRAM; §8 says the all-zero baseline is anomalously cheap by +349 mW and that every Δ against it carries that constant. Those compose only if you know how the step scales with depth, and it was measured on `ws_l3_x8` alone: constant in watts collapses the ladder to ~5.5× and drives the L1 row negative, constant in Δ pJ/byte leaves §4 as published. Both are arithmetically impossible at L1 (whose whole effect is +228 mW against a +349 mW step, or +1.75 W if it scaled with throughput), so the likely answer is the third — the step scales with depth like the rest of the effect — but that has to be measured, not inferred. The same grid is E1's interaction term, and the session also carries the two-buffer A/A and the sham-B that C4 asks for.

Then, in Phase 2:
1. **Placements**: cross-SMT-sibling, cross-P/E-core, cross-container. The container case is now less about whether the channel survives (tier 3 needs no interface, so it should) than about confirming that, and it is a one-command experiment.
2. **Re-run `phase2_tier1_rate` with 4 repeats** now that the overshoot gate exists. The first sweep had 7 of 30 runs in the bad sampler regime, and at 2 ms and 4 ms only 1 of 3 repeats acquired sync, so those two rows measure an acquisition probability rather than a channel. Worth pairing with a longer preamble, since acquisition — not integration — is what binds at the fast end.
3. **Tier 1 under Config-B**, so the tiers can be compared in one configuration. Today tier 1 is Config-A at 4 threads/stride 2 with 8 frames and 256-bit payloads, and tiers 2–3 are Config-B at 10 threads/stride 1 with 4 frames and 8-bit payloads — five differences at once, so 241 bit/s against 1 is each tier measured where it works rather than a controlled comparison.
4. Then the comparison against Liu et al. (CCS'22) and Hertzbleed.

Chapter drafts are written as phases complete, not deferred to the end. `thesis/phase0-measurement.md` and `thesis/phase1-leakage.md` are full first drafts. `thesis/phase2-covert.md` is a **partial** draft covering all three tiers; its §9 lists what is missing (the placement matrix, a controlled cross-tier comparison, the literature comparison, the transmitter's real operating point, and the receiver's isolated core). `thesis/critique.md` is a standing review of all three drafts with a work order at its foot; items 1 and 2 are done and its *Outcomes* section records which findings survived checking.

Known gaps deliberately left open:
- `isolcpus=0` only isolates the attacker core; victim cores 2,4,6,8,10 still take stray work. Extending it needs a GRUB edit and reboot, and has not been done.
- `analysis/stats.py`'s detector is a mean threshold. It cannot see effects that live in variance rather than mean, which `avx2_load` briefly looked like it might have.
- `perf` is unusable unprivileged here (`perf_event_paranoid=4`), so cache residency is not measured directly. It is now corroborated indirectly: achieved bandwidth matches each level's expected ceiling (68 B/cycle/core for the L1 variant against Golden Cove's 3×32 B peak, 34 B/cycle for L2, 42 GB/s aggregate for DRAM ≈ 55% of DDR5-4800 dual-channel). That is weaker than a counter but it is not nothing.
- **The `ws_dram_x8` A/A offset is unreproduced rather than explained.** Its three repeats in the traffic-volume sweep read +18.6, +10.1, +17.5 mW with both selectors identical (between-run SD 4.6 mW), and one repeat failed the `aa_*` gate. Six fresh A/A repeats in `results/20260901-212214-phase0_warmup_check` do not reproduce it: they scatter around zero (−25.9 and +26.1 mW by arm) with the sign flipping, so it is not a stable property of the victim. But that session's between-run SD is 59–71 mW, an order of magnitude wider than the sweep's, so it cannot resolve 15 mW either. Treat the offset as session-specific and treat `ws_dram_x8` as poorly conditioned; the polarity control already ruled out a bias in the measurement path.
- **`ws_dram_x8` is noisy at block level, and worst late in a run.** Per-block dispersion in the warmup check is 0.45–0.75 W over blocks 100+, against 0.11–0.22 W over the first 20. That is on top of its weaker detectability (n=13–89 for 95% versus n=1–8 for `ws_l3_x8`). Highest pJ/byte, worst instrument — prefer `ws_l3_x8` for anything that needs resolution.
- **The run-level startup transient is reduced, not eliminated.** `ws_init` faults in 2 × 32 MB per thread under `MAP_POPULATE` — 256 MB for a 4-thread `ws_dram_x8` run — and `settle` discards *samples per block*, so it was no defence. `--warmup-blocks N` now discards whole blocks at the head of a run, cycling every condition first. In `results/20260901-212214-phase0_warmup_check` the early-block deficit is −0.19 W (SD 0.19) without it and −0.10 W (SD 0.08) with 8 blocks; the clearest single case is −0.62 W in block 0 of a run that reached steady state by block 4. Three repeats per arm cannot separate those means, so the flag is justified but not calibrated. Size it in seconds, not blocks — 8 blocks is ~1 s of recording.

## Running an experiment

Two halves, deliberately split by privilege. The runner is stdlib-only so it needs no venv; analysis needs numpy/matplotlib so it runs unprivileged afterwards.

```bash
sudo python3 src/experiment_runner.py experiments/phase0_validate.json
./venv/bin/python3 -m analysis.report results/<run_id>
```

Experiments are declarative JSON in `experiments/`. Any key in `DRIVER_FLAGS` (`src/experiment_runner.py`) may be set experiment-wide under `driver` and overridden per run. `config: "A"` disables turbo (pins frequency, isolates power leakage from DVFS); `config: "B"` leaves turbo on (**required** for frequency- and timing-based receivers, which have nothing to observe under Config-A).

Each run writes `results/<timestamp>-<name>/` containing one CSV per run plus `manifest.json` (git commit, every driver argument, turbo/governor state, PL1/PL2, package temperature before and after, per-CPU frequencies). Ownership is handed back to `SUDO_UID` on exit.

**Results provenance is tracked in git; the raw CSVs are not.** `.gitignore` excludes `results/**/*.csv` and nothing else under `results/`, so every run's `manifest.json`, selector files, figures and `summary.txt` are committed. After a run, write a `summary.cmd` beside the manifest recording exactly how its summary is built, then run it:

```bash
r=results/<run_id>
cat > "$r/summary.cmd" <<'EOF'
./venv/bin/python3 -m analysis.report "$r"
./venv/bin/python3 -m analysis.aggregate "$r"
EOF
bash analysis/regen-summary.sh "$r"
```

**The `summary.cmd` is not ceremony.** Summaries are heterogeneous — some carry `analysis.hwfit` output, the instruction table carries two differently-flagged `aggregate` sections, the tier-2 sweep carries a second decode against a different watched CPU — and a uniform report+aggregate pass over all of them silently deletes that content. It did once, and was caught only because the diff came out net-negative. `regen-summary.sh` refuses to guess for a directory with no `summary.cmd`.

Chapter drafts live in `thesis/`, one per phase, written as the phase completes. Every number in a draft cites the run directory it came from.

### Covert-channel runs

A spec with `"kind": "covert"` runs the Phase 2 pair instead of the driver: keys under `tx` (any of `TX_FLAGS`) and `rx` set the transmitter and receiver, overridable per run exactly as `driver` keys are. `rx.tier` picks the receiver — `"rapl"` (default, root, Config-A), `"freq"` (unprivileged, **Config-B only**) or `"timing"` (reads nothing, **Config-B only**), each with its own flag set in `RX_RECEIVERS`. The runner drops the unprivileged tiers to `SUDO_UID`, which is the point rather than a detail, and hands the output directory over before the first run so a dropped-privilege receiver can write its own CSV. The receiver's duration is computed from the transmission, not configured. Read one with `analysis.covert`, which is also what writes its `summary.txt`:

```bash
sudo -n src/experiment_runner.py experiments/phase2_tier1_rate.json
r=results/<run_id>; ./venv/bin/python3 -m analysis.covert "$r" > "$r/summary.txt" 2>&1
```

The binaries also run by hand — `bin/tx`, `bin/rx_freq` and `bin/rx_timing` need no root, `bin/rx_rapl` does. Start the receiver first and give it a duration covering the whole transmission; it records blind and has no idea what is being sent.

Three per-run self-checks decide whether a result means anything. `late_chips` must be zero: a non-zero count means the transmitter could not hold its own schedule, so the BER describes the transmitter rather than the channel. `missed_chips` must also be zero, and it is the stronger of the two — victims count the distinct `ctl->epoch` values they observe, so it says whether the modulation reached the die rather than whether it was scheduled. And the overshoot gate must pass, for the reason in *Methodology notes* below.

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

`./venv/bin/python3 tests/test_covert_decode.py` checks the covert decoder against synthetic traces with known answers — the framing, the Manchester convention, that sync is recovered rather than assumed, that an A/A decodes at chance, and that BER tracks Q(d′/√2). It needs numpy, so unlike the runner test it runs in the venv. A synthetic trace has a right answer; a real one does not, which is the whole point of having it.

`cd src && make check` runs `tests/victim_smoke.c` against every victim — no root needed. It verifies each one actually spins, **observes** a live `ctl->selector` write (it asserts every victim's `epochs_seen` reached the one epoch published, rather than reading the selector back and proving only that the test can read its own store), and exits cleanly when `ctl->run` clears. This is the only pre-flight check that does not need MSR access, and it is what catches mis-assembled instructions (see the AVX-VNNI note below). The validity gates cover experiment correctness.

## Architecture

**`util/util.{c,h}`** — `struct ctl_t` is the shared control block, and the reason the design works: victims are cloned with `CLONE_VM`, so writing `ctl->selector` re-tunes every running victim within one burst (~0.6 µs) with no thread teardown. That is both the condition-interleaving mechanism and the covert-channel transmitter primitive Phase 2 needs. Also holds `parse_args` (getopt_long), selector-file parsing, and a seeded xorshift PRNG so block order is reproducible from the logged seed.

**`util/victim-utils.c`** — every victim is a `DEFINE_VEC_VICTIM` macro instantiation wrapping an inline-asm loop, 8 independent destinations deep so the loop is throughput-bound rather than latency-bound. Victims re-read `ctl->selector` between bursts of `8 * AVX_BURST` instructions. Adding one is a single table edit; `NUM_VICTIMS` is computed from the table, so the old three-places-to-edit footgun is gone. `avx2_vnni` is guarded by `#ifdef __AVXVNNI__`.

Every victim body opens with `VICTIM_TICK(a, ctl)`, which counts the burst *and* counts how many distinct `ctl->epoch` values this victim has observed. That second number is the receiving end of the driver's and transmitter's selector writes, and it is what gives `late_chips` teeth: without it, a transmitter whose control thread hits every deadline reports a clean transmission even if the victims coalesced two chips into one burst and nothing reached the die. Use `VICTIM_TICK` in any new victim; a bare `a->bursts++` silently opts out of the check.

The `ws_*` working-set victims fill their buffer with one repeated 32-bit word, so the Hamming *distance* between consecutive transfers is zero by construction — which confounds the weight and switching models of leakage. The `ws_*_ab` variants split the 64-bit selector into two words and alternate them, so two words of equal weight hold the mean weight of the stream fixed while varying how many bits flip per transfer. `ws_l3_x8_ab` alternates every 32 bytes (every `ymm` load differs from the last); `ws_l3_x8_ab64` every 64 (every cache line differs, at half the load-to-load toggle rate). With both halves equal the fill is bit-identical to the single-word one, so `ws_l3_x8_ab` holding `A|A` *is* `ws_l3_x8` holding `A`.

The `ws_op_*` victims are the per-instruction table's: the `ws_l3_x8` load stream followed by 8 independent operations on the loaded registers, into a separate destination bank (ymm8–15, re-zeroed once per burst for the accumulating instructions). Both sources of each op are the same loaded register, which makes the result Hamming weight predictable — `vpand`/`vpor` track the operand, `vpxor`/`vpsllvd` pin the result at 0 — and turns the family into an input-vs-output contrast. The loop must stay load-bound for the traffic to be matched; check the GB/s column, and read the table in pJ/byte.

Deliberate contrasts in the victim set: `vpand`/`vpor` are identity on equal inputs (result Hamming weight tracks the operand) while `vpxor` always yields zero (result HW pinned at 0) — comparing them separates input- from output-driven leakage. `scalar_rol` preserves Hamming weight indefinitely, varying only bit position.

**`util/freq-utils.c`** — APERF/MPERF ratio scaled by `MSR_PLATFORM_INFO`; `set_frequency_units()` must run before any frequency read. `frequency_cpufreq()` reads world-readable sysfs and is the basis of the Phase 2 unprivileged receiver.

**`util/rapl-utils.c`** — general RAPL wrapper, still linked but unused by the driver, which preads `MSR_PKG_ENERGY_STATUS` inline to keep the sampling loop tight.

**`util/sampler.c`** — the edge-triggered RAPL sampler, factored out of the driver so the covert receiver measures with the validated instrument rather than a copy of it. Holds the guard fraction, the EWMA period estimate and the overshoot count. Also `measure_tsc_hz()` and `busy_wait_until()`, which is what puts symbol boundaries on absolute TSC deadlines.

**`util/victim-pool.c`** — victim spawn/stop, shared by the driver and the transmitter. Keeps the two footguns that were already paid for once: a private `victim_args_t` per victim, and `PR_SET_PDEATHSIG` so an orphan cannot spin on a pinned core and poison later runs.

**`src/covert/`** — Phase 2. `tx.c` is the transmitter and needs no root: it spawns victims and modulates `ctl->selector` on absolute TSC deadlines, counting any chip it misses. It pre-warms both operands before the frame, which matters because a working-set victim fills a buffer per distinct selector value and caches `WS_SLOTS` = 2 of them — with exactly two values that makes a symbol transition a pointer swap rather than a refill, so the modulation is a change of *operand* and not of how much work is being done. `rx_rapl.c` is the tier-1 receiver: root, pinned, and deliberately ignorant of the symbol period, preamble and payload. `rx_freq.c` is tier 2: unprivileged, polling world-readable `scaling_cur_freq` on a fixed grid (a level, not an accumulator, so there is no counter edge to lock onto), able to watch several CPUs at once since an attacker need not know where the victim runs. `rx_timing.c` is tier 3: it reads nothing, timing its own fixed instruction stream against the invariant TSC, so throttling shows up as its own dilation. Its level is the raw workload duration, which inverts its polarity relative to tier 2 — higher means slower means throttled. Every receiver is a separate process sharing no memory with the transmitter; the only thing they share is the invariant TSC, which any process can read.

**`src/driver.c`** — the monitor is the main thread (pinned, priority −20); victims are `clone(CLONE_VM | SIGCHLD)` children on 64 KB stacks, each with its own `victim_args_t`. Two things matter most:

- *Interleaving.* A run is `blocks_per_condition × conditions` short blocks in seeded-shuffled order, not one long block per condition. This makes thermal drift common-mode. Getting this wrong is what made `src/data/out-1207-2115` unusable.
- *Edge-triggered sampling.* Rather than a fixed busy-wait window, the sampler idles for 7/8 of the estimated RAPL period then tight-polls until the counter changes, recording the exact energy increment and its TSC interval. Staying below 1.0 of the period means an edge can never be slept through. `--mode fixed` restores the old fixed-window sampler for comparison.
- *Per-condition bookkeeping, at block boundaries only.* Bursts are latched per block and summed per condition (`victim_bytes_per_s_by_cond`), and a victim core's `scaling_cur_freq` is read once per block (`victim_freq_khz_by_cond`). These feed the `work_balance` and `frequency_balance` gates. Both are off the sampling path by construction; the sysfs read in particular must never migrate into the sample loop.

TSC frequency is calibrated once against `CLOCK_MONOTONIC`; without it the analysis cannot convert energy per edge into watts. Progress goes to stderr, a JSON summary to stdout which the runner folds into the manifest.

**Output schema** — `block,cond,ticks,dtsc,daperf,dmperf`, one row per sample. `ticks` is raw RAPL energy units (`uint32` subtraction, so counter wraparound is handled); power is `ticks * energy_unit / (dtsc / tsc_hz)`. The covert receiver writes `tsc,ticks,dtsc,daperf,dmperf` instead: it has no blocks or conditions, and it needs the *absolute* TSC so the trace can be aligned against the transmitter's schedule.

**`analysis/`** — numpy-only (this venv has no scipy or sklearn). `stats.py` resamples **blocks, not samples** throughout: samples within a block share a thermal and frequency state, so treating 300k correlated samples as independent will "prove" anything. Contains block bootstrap, block permutation test, `temporal_balance`, and a threshold detector whose accuracy-vs-*n* curve converts directly into covert-channel bit rate. Three entry points: `analysis.report` (one run, gates enforced, exits non-zero on failure), `analysis.aggregate` (between-run spread over repeats — the minimum before quoting anything; `--against LABEL` additionally differences every row against a reference row *within* each repeat, which is the only way to read a table whose rows share a large common term, and `--per-byte` does that in pJ/byte so rows at different throughputs are comparable — check the paired SD against the unpaired one, since pairing hurts when rows are anti-correlated), and `analysis.hwfit` (a sweep read as a slope: fits `dP = a + b·x` once per repeat and takes the spread across repeats as the error bar, and reports whether operands at the same point on the axis but with different bit placement differ). `hwfit` takes `--axis hw` (default, popcount of the selector) or `--axis hd` (popcount of the XOR of the selector's two halves — the bits a `ws_*_ab` victim flips per transfer), and `--labels GLOB` to restrict which runs are fitted, which a mixed session needs so that an anchor run sitting at distance 0 is not fitted as a point on the distance axis. It accepts several result directories and pools them, which is how the two Hamming-weight sessions are read together.

`analysis.covert` is the fourth entry point and reads a covert run instead of a driver run. It integrates the trace over each chip window — symbol boundaries never line up with RAPL update instants, so windowed integration over a cumulative-energy function is what keeps that exact — and finds the frame by sliding the preamble along the trace, summing the correlation over all frames at a candidate offset. **Sync is recovered, not supplied**: the transmitter's `tsc_start` is read only afterwards, to report how far the recovered sync landed from the truth, and `--sync-span-symbols` restricts the search around it as a diagnostic when you want to separate a sync failure from a dead channel. It reports per-chip Δ, noise and d′ alongside the BER, because those say whether a bad run was a bad channel or a bad instrument. It handles both receivers: a tier-2 trace is a sampled *level* rather than an energy accumulator (`Trace.from_levels`), the channel polarity is **recovered from the preamble rather than supplied** (tier 2 is inverted — the heavier operand throttles the part, so the ON state reads as a lower frequency), and `--freq-column` selects which watched CPU to decode. Compare BER against the `Q(pair)` column, not `d-marg`; see *Findings so far*.

## Findings so far (2026-09-04)

**An unprivileged receiver recovers the message at 2 bit/s.** This is the Phase 2 security result: `src/covert/rx_freq.c` reads world-readable `scaling_cur_freq` and nothing else — no MSR, no `perf`, no root, no shared memory — and the runner drops it to the invoking user so the claim is enforced rather than asserted. `experiments/phase2_tier2_covert.json` under Config-B, ten victim threads, 200 µs polling (`results/20260903-143109-phase2_tier2_covert`, 6 conditions × 3 repeats, zero late chips):

| bit/s | 31.2 | 15.6 | 7.8 | 3.9 | **2.0** | A/A @ 3.9 |
|---|---|---|---|---|---|---|
| BER | 0.516 | 0.492 | 0.500 | 0.365 | **0.083** | 0.490 |
| after vote | 0.563 | 0.469 | 0.458 | 0.375 | **0** | 0.583 |
| p vs chance | 0.82 | 0.40 | 0.53 | 5e-3 | **2e-18** | 0.46 |

Zero errors after a 4-frame majority vote at 2 bit/s, control at chance. Tier 2's capacity is 1.1 bit/s against tier 1's 241 — privilege buys rate, not access. Watching a *victim's* core rather than the receiver's own extends it one step (BER 0.229 at 3.9 bit/s, 0.297 at 7.8, where own-core is at chance); both are in the run's `summary.txt`, and `--freq-column 1` selects the second watched CPU. Polling another core costs 0.35 µs and issues no IPI, so it does not perturb the victim. **Caveat on the victim-core column: its A/A pools to 0.4375 (one repeat at 0.375) against 0.4896 on the own-core column**, so that control has little power to confirm it; needs a longer A/A before it is a finding.

**The tier-2/3 transmitter is not the victim Phase 1 characterised.** Pooling every `ws_l3_x8` run in `results/` via `analysis.instrument --check throughput`: 145.8 GB/s at 4 threads/stride 2 (n=130, Phase 1's config) against **91.9 GB/s at 10 threads/stride 1** (n=7, Phase 2 tier 2/3), falling to 65.4 at 18 threads. Phase 1 measured L3 at 148 and DRAM at 41, so at 10 threads (40 MB active against a 24 MB L3, SMT-paired) the transmitter is partly DRAM-resident and the detectability ranking that selected it does not apply.

**A receiver that reads nothing at all does just as well.** Tier 3 (`src/covert/rx_timing.c`) times its own fixed instruction stream against the invariant TSC — no file, no MSR, nothing a container can decline to mount — and infers throttling from its own dilation (`experiments/phase2_tier3_covert.json`, `results/20260903-194816-phase2_tier3_covert`, 5 conditions × 3 repeats):

| bit/s | tier 2 (reads a file) | | | tier 3 (reads nothing) | | |
|---|---|---|---|---|---|---|
| | BER | cap | vote | BER | cap | vote |
| 7.8 | 0.500 | 0.0 | 0.458 | — | — | — |
| 3.9 | 0.365 | 0.2 | 0.375 | **0.237** | **0.8** | **0.198** |
| 2.0 | 0.083 | 1.1 | 0.000 | 0.109 | 1.0 | 0.000 |
| 1.0 | — | — | — | **0.000** | **1.0** | **0.000** |

Both A/A controls dead at chance with power behind them: pooled 0.500 over 384 bits (p = 0.52) and 0.516 over 192 (p = 0.69). **The whole cost of the ladder is the step from root to unprivileged** — 241 bit/s of capacity to ~1 — and giving up the last readable interface costs nothing. Carry this into Phase 4: restricting `scaling_cur_freq` is the obvious defence against tier 2 and buys nothing, because tier 3 never reads it. Any mitigation aimed at the *interface* rather than the throttling is defeated before it starts.

**Balanced payloads, or the A/A means nothing.** Tier 3's first pass failed its A/A gate at BER 0.219 — not the channel, the payload. `--random-bits` drew i.i.d. bits, one repeat drew 7 ones in 8, those 8 bits repeated over 4 frames, and a decoder whose output leans the same way then scores well on a transmission carrying nothing. Payloads are balanced by construction now (exactly half ones, shuffled), which makes the expected BER exactly 0.5 for any decode that is biased but *independent of the message*. The decoder reports the ones-fraction of both transmitted and decoded payloads and warns on a skewed one. The A/A gate is now judged **pooled across repeats**: a single A/A can carry as few as 32 bits, where the SD of BER is 0.09 and a 3σ excursion is a 1-in-140 event, so a per-run gate fires on noise about as often as on a fault.

**Tier 2 exists only while the part is throttling.** PL1 is 200 W and PL2 80 W here, against ~16 W for a four-thread victim at ~50 °C — nothing limits, so nothing clocks down and there is nothing to read. Ten P-core threads are needed. That is a precondition of the attack, not a tuning detail: an idle machine does not carry this channel.

**And ten threads make it throttle on average, not in every run.** `analysis.covert` now reports the p5–p95 band of the receiver's own level over the transmission window and warns below 10%. Three of the eighteen tier-2 runs sat at their ceiling and never throttled: `sym_256ms_r0` (3840–3900 MHz, 1.5%) and **two of the three repeats at 31.2 bit/s** (2.0% and 3.4%), against 14–40% for the other fifteen. So the 31.2 bit/s row is largely a measurement of a machine that was not limiting rather than of the channel at that rate, and the 3.9 bit/s row mixes one such run with two working ones (0.500 / 0.313 / 0.281). The 2 bit/s headline is untouched. Two traps here, both fallen into once: per-chip noise does **not** identify these runs — `sym_256ms_r0` reads 7.87 MHz against 200–700, which looks like a parked CPU and is actually white noise averaging down over a 256 ms chip — and the band must be taken over the transmission window, since the recording brackets it and a watched *victim* core idles at 400 MHz outside, which makes every column look lively.

**A null on a proxy is not a null on the mechanism.** `experiments/phase2_tier2_feasibility.json` measured the frequency difference between operands directly and found **no load distinguishable from its A/A control** (|t| ≤ 1.56 at every thread count; the control itself reads −48 MHz with the sign flipping) — on a channel that demonstrably works. It compared means over interleaved 0.1 s blocks where the channel needs a 256 ms chip, so it was underpowered, not contradictory. An interrupted first pass of that sweep had shown a single run at −804 MHz with a tight within-run CI; that run's die climbed 42 → 77 °C while it was measured. Textbook instance of the rule this project already had: a single run's CI is optimistic, worst for large effects, never an error bar. **Run the real receiver, not something correlated with it.**

**BER is predicted by the *paired* separation, not the marginal one.** A Manchester bit is decided by differencing two adjacent chips, so the statistic that matters is `|mean(P₀−P₁)| / sd(P₀−P₁)` over symbols, with BER = Q(d′_paired). When chip noise is white this equals d′/√2 — which is why that simpler form fits tier 1. It fails when noise is dominated by drift slower than a symbol, because differencing cancels it: tier 2 decodes at BER 0.09 with a *marginal* d′ of 0.08, a value the simple model calls chance, while the paired one reads 1.07 and predicts 0.14. `analysis.covert` reports both plus `Q(pair)`; compare BER against the paired column. A corollary worth keeping: Manchester was chosen to reject thermal drift, and it is what makes tier 2 exist at all, cancelling a governor wander hundreds of MHz deep on a ~50 MHz signal.

**The tier-1 covert channel carries 241 bit/s of capacity at a 3 ms symbol.** `experiments/phase2_tier1_rate.json` on `ws_l3_x8` under Config-A, Manchester-coded, 8 frames of [13-bit Barker preamble | payload] per run, transmitter and receiver in separate processes sharing no memory (`results/20260903-115606-phase2_tier1_rate`, 10 conditions × 3 repeats, zero late chips in all 30 runs). **Aggregated over three repeats — an earlier reading of this sweep quoted the best repeat at each rate and is superseded:**

| bit/s | 500 | 333 | 250 | 167 | 125 | 83 | 62.5 | 41.7 | 31.2 |
|---|---|---|---|---|---|---|---|---|---|
| BER | 0.342 | 0.048 | 0.339 | 0.060 | 0.017 | 0.011 | 0.009 | 0.016 | 0.003 |
| capacity b/s | 36.8 | **241** | 19.1 | 112 | 110 | 76 | 58 | 37 | 30 |
| acquired | 1/3 | 3/3 | 1/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |

**No rate is error-free once aggregated** — at 83 bit/s the repeats read 0.030 / 0.002 / 0.000. Quote capacity, not "error-free at rate X". The 8-frame vote clears 167 bit/s and below to zero observed errors, but delivers raw/8 = 20.8 bit/s and below. The A/A control reads BER 0.500 (SD 0.012), and on the true chip grid still 0.507 — nothing there to find.

**The majority vote breaks ties on the summed margin, not toward zero.** Both frame counts are even, so a vote can split exactly, and `mean > 0.5` used to send every such split to 0 — a systematic bias toward zero bits, applied to exactly the bits the vote was least sure of. Four frames split evenly 37.5% of the time on a channel at chance, so this was not a rounding detail: 157 of 408 voted bits in the tier-2 sweep, 341 of 5760 in tier 1. Fixing it moves vote figures in both directions (tier 1 to zero errors at 167 rather than 125 bit/s; tier 2 at 3.9 bit/s from 0.250 to 0.375), none of the movements resolvable at 24–96 distinct bits where theory puts both rules at 0.14, and no headline changes. The tie count is now a reported column and a diagnostic in its own right: at every rate that decodes cleanly, no bit is tied.

**The 2 ms and 4 ms rows are acquisition failures, not weak channels.** `analysis.covert` now reports `BER|snc`, the decode on the *true* chip grid: `sym_04ms_r1` reads 0.519 free-running and **0.087** with sync supplied. Across 30 runs sync lands either within a chip (23 runs, BER ≤ 0.17) or hundreds of chips away (7 runs, BER ≈ 0.5), with nothing between. A receiver can tell which happened from the absolute correlation peak (0.26–0.71 failed vs 0.64–1.00 acquired); peak-over-sidelobe does **not** separate them (1.00–1.19 vs 1.04–1.43). Same story on tier 2: 7.8 bit/s reads 0.500 free and 0.339 on the true grid, so its cliff is partly an acquisition cliff with headroom in a longer preamble.

**BER is set by the per-chip d′, and the run-to-run spread is the instrument, not the channel.** The first read of that sweep looked like a rate limit: 4 ms failed in two repeats of three while 3 ms worked in all three. It is not. Forcing sync to the true offset leaves the bad runs failing, and the per-chip numbers say why — Δ power is the same in every repeat (1.06 / 1.12 / 1.15 W at 4 ms) while per-chip *noise* varies fourfold (1.61 / 0.72 / 0.40 W). A Manchester decision differences two chips, so BER should track Q(d′/√2), and it does: d′ 0.69 gives 0.346 against a predicted 0.313, d′ 3.43 gives 0.009 against 0.008.

What moves the noise is the sampler's overshoot regime (r = +0.63 against per-chip SD over 30 runs). `analysis.covert` gates on it at 1% of edges and prints settled-only alongside unfiltered numbers, since the overshoot rate is measured by the receiver and knows nothing about the decode, but a filtered number quoted alone is how selection bias gets in. **The gate is necessary and not sufficient**: `sym_04ms_r1` passes it at 0.29% and still carries 0.69 W of per-chip noise against 0.34 W for the clean repeat at the same rate, so overshoots are the dominant cause and not the only one.

Note that `analysis.covert` measures Δ, noise and d′ on the **true** chip grid, not the recovered one — a sync failure would otherwise misalign every window and report a dead channel where there is a live one the receiver merely failed to find, which is the distinction those numbers exist to draw. Like the sync-error column they read ground truth, so they are diagnostics and not something a real receiver could compute. Across the sweep the true-grid BER (`BER|snc`) tracks Q(d′/√2) with a log-log correlation of +0.816 over the 20 runs where the prediction is resolvable, at a median BER/Q of 1.15 — compare against `BER|snc`, not the free-running BER, since these statistics assume sync; above d′ ≈ 6 the prediction underflows while measured BER settles on a floor of 2–5 × 10⁻³.

Two things about it remain open. The bad regime gets rarer across a session — 1.66%, 0.89%, 0.09% of edges by repeat, so repeat 0 is reliably the noisiest — and nothing here explains why. And Δ falls as symbols shorten (1.54 W at 8 ms, 1.13 at 4 ms, 0.64 at 2 ms), which is the ~1 ms RAPL integration window low-passing the modulation; that one is expected and is the real ceiling on rate.

## Earlier findings (2026-09-02)

**Leakage is linear in operand Hamming weight above a step at zero.** `experiments/phase1_hamming_weight.json` on `ws_l3_x8`, 11 runs × 3 repeats, every run contrasting the test operand against an all-zero working set (`results/20260901-213211-phase1_hamming_weight`, 70/71 gates pass — see below):

| HW | operand(s) | Δ power | between-run SD | detector |
|---|---|---|---|---|
| 0 | *(A/A control)* | −0.014 W | 0.028 | 0.54 |
| 1 | `0x00000008` | +0.42 W | 0.039 | 0.84 |
| 4 | two patterns | +0.46 / +0.56 W | 0.085 / 0.055 | 0.90 / 0.86 |
| 8 | two patterns | +0.71 / +0.88 W | 0.107 / 0.047 | 0.85 / 0.99 |
| 16 | two patterns | +1.13 / +1.22 W | 0.120 / 0.022 | 1.00 / 1.00 |
| 24 | two patterns | +1.57 / +1.61 W | 0.044 / 0.014 | 1.00 / 1.00 |
| 32 | `0xFFFFFFFF` | +1.90 W | 0.051 | 1.00 |

`dP = 0.362 + 0.0500·HW` watts, **R² = 0.974**, slope **+50.0 mW/bit** (SD 0.25 over three repeats). Alternative shapes were tested and rejected: √HW gives R² 0.951, log(1+HW) 0.874, a pure power law through the origin 0.928, and adding a quadratic term, a cyclic bit-transition count, or a non-zero-byte count each buys ≤0.003 of R² for an extra parameter. The `hw32` figure also reproduces `phase1_polarity`'s `l3_forward` (+1.904 vs +1.841 W) across sessions.

**The intercept is the interesting part.** The line extrapolates to +362 mW at HW = 0, but dP(0) is zero by construction and the A/A control confirms it (−14 mW). A single set bit already costs +0.42 W, 22% of the full-range effect. Two readings fitted this session equally well: a set bit is disproportionately expensive at the low end, or **the all-zero baseline is anomalously cheap**, which would make every Δ in this table an overestimate by a constant. They are not separable from this design, because every contrast here uses the all-zero operand as its baseline. The non-zero-baseline arm settles that the step is a property of the operand rather than of the measurement; the low-end sweep below settles that it sits entirely at the 0 → 1 boundary.

**The step belongs to the operand, not to the act of switching operands.** `experiments/phase1_nonzero_baseline.json` (`results/20260901-225254-phase1_nonzero_baseline`, 7 runs × 3 repeats, 48/48 gates pass) contrasts non-zero operands against each other, so nothing in it uses the all-zero baseline. This was the dangerous alternative: if alternating a victim between *any* two distinct working sets cost a fixed ~0.36 W, every A/B result in this project would be inflated by it, and no A/A could have caught it, because an A/A holds the same value in both conditions and never swaps buffers.

| contrast | ΔHW | measured | predicted | mW/bit |
|---|---|---|---|---|
| `hw04_a → hw08_a` | 4 | +0.326 W | +0.251 | 81 |
| `hw08_a → hw16_a` | 8 | +0.388 W | +0.418 | 48 |
| `hw16_a → hw32` | 16 | +0.774 W | +0.771 | 48 |
| `hw01 → hw32` | 31 | +1.705 W | +1.482 | 55 |
| `hw16_a → hw16_b` | 0 | +0.071 W | +0.085 | — |
| `0 → hw16_a` (anchor) | 16 | +1.215 W | +1.133 | 76 |

Regressing measured on predicted gives an **intercept of +14 ± 23 mW** — reading B's +362 mW sits **15 SE away** — and the intercept is identical whether or not the session is rescaled by the anchor. **No per-contrast offset exists; the depth table and every earlier A/B result stand.** The A/A on a non-zero operand is also clean (+10.6 mW, sign flipping, accuracy 0.506), which closes a real gap: every previous A/A held all-zeros or all-ones, so none could have revealed anything peculiar to the zero buffer.

Two loose ends from it, one now closed. The contrasts touching the lowest weights came in above prediction (+57 mW at z=+2.7 and +115 mW at z=+2.5, both positive), and the session's internal chain implies `hw01 → hw04` = +0.217 W where the sweep puts it at +0.043 W, which suggested the transition might be super-linear across the first few bits rather than a clean discontinuity. The low-end sweep above settles that: it is a discontinuity. And the anchor run reproduced the sweep's `hw16_a` at +1.215 vs +1.133 W, a difference of 0.082 ± 0.088 — not distinguishable, but only because an anchor was included. **Put an anchor run in every session**; cross-session comparison has no other check.

**Bit placement matters a little, and is not explained.** At equal Hamming weight, two patterns differ by 0.01–0.16 W. Pooling both sweep sessions gives eight weights with more than one pattern, and at two of them the spread exceeds the between-repeat SD: HW 8 (0.162 W against 0.077) and HW 2 (0.102 W against 0.018). The rest are within noise. Neither cyclic adjacent-bit transitions nor the number of non-zero bytes accounts for it, and the two that exceed noise share no structure. So Hamming weight is a good predictor but not a complete one — worth a sentence in the thesis, not a chapter.

**One A/A repeat failed its gate**, marginally: its 95% CI was [−100.2, −0.6] mW, excluding zero by 0.6 mW, with p = 0.072 and a decile spike of +0.3 W in one condition. Across the three repeats the A/A is −14.1 mW with the sign flipping, so the aggregate is clean. This is the documented pattern — a single run's CI is optimistic — and it is why `analysis.aggregate` over ≥3 repeats is the reporting unit, but the failure is recorded rather than waved away.

**The step at zero is a discontinuity, not a climb over the first few bits.** `experiments/phase1_low_end.json` samples HW 1, 2, 3, 4 and 6 at two bit patterns each, against the same all-zero baseline and at driver settings identical to the sweep, so the points pool with it (`results/20260902-211017-phase1_low_end`, 78/78 gates pass; A/A +8.4 mW, sign flipping, accuracy 0.516; anchor +1.228 W against +1.133 and +1.215 in the two earlier sessions).

The new points land *on* the existing line rather than filling in the gap beneath it. Per-weight means over the two patterns: HW 1 +0.41 W, HW 2 +0.44, HW 3 +0.46, HW 4 +0.52, HW 6 +0.72. Pooling both sessions — 18 operands from HW 1 to 32, one fit per repeat:

**dP = 0.349 + 0.0508·HW watts, R² = 0.967, slope +50.75 mW/bit (SD 1.35), intercept +349 mW (SD 26, 23 SE from zero)**

against dP(0) = 0 by construction and −2.8 mW over six pooled A/A repeats. Setting one bit per 32-bit word — 3.1% of the bits in the buffer — costs +0.41 W, eight times the 51 mW every subsequent bit costs. So **the all-zero operand is anomalously cheap**, and every Δ quoted against an all-zero baseline carries a constant ≈0.35 W belonging to the baseline, not the test operand. Rankings are unaffected (every row carries it); absolute per-byte figures are overestimates by that much.

This resolves the super-linearity question against it: pooled, `hw01 → hw04` is +0.102 W where the line predicts +0.152 and the non-zero chain implied +0.217. **Do not fit a slope to the low end alone** — those points span 0.3 W and give +61.6 mW/bit with SD 18.4 over three repeats, an interval wide enough to contain almost anything. It is the pooled fit that carries the result.

Worth remembering for Phase 3: a disproportionately cheap zero is exactly what makes *sparsity* detectable, which is the property the ML chapter is after. Post-ReLU activations are 50–90% zero. No mechanism is claimed — zero-detection or data-path clock gating would produce this signature, but nothing here identifies which.

**Hamming distance leaks too, at two-thirds the cost of a set bit.** Every working-set victim fills its buffer with one repeated word, so HD was pinned at 0 throughout and the static-weight and switching models were indistinguishable. `experiments/phase1_hamming_distance.json` on `ws_l3_x8_ab`, which alternates two equal-weight words every 32 bytes so mean weight per word (16) *and* per ymm register (128) are identical in both conditions (`results/20260902-220517-phase1_hamming_distance`, 11 runs × 3 repeats, 72/72 gates pass):

| HD | Δ power | between-run SD | detector |
|---|---|---|---|
| 0 *(A/A)* | +0.019 W | 0.014 | 0.51 |
| 2 | +0.086 W | 0.141 | 0.77 |
| 4 | +0.203 W | 0.023 | 0.85 |
| 8 | +0.343 W | 0.030 | 0.99 |
| 16 | +0.550 / +0.632 W | 0.071 / 0.049 | 0.97 / 1.00 |
| 32 | +1.139 W | 0.048 | 0.99 |

**dP = 0.048 + 0.0341·HD watts, R² = 0.974, slope +34.14 mW/bit (SD 2.96), intercept +48 ± 28 mW.** So **both models are true**: weight leaks at +50.75 mW/set bit on a stream whose distance is 0 (where switching predicts nothing), and distance leaks at +34.14 mW/flipped bit at fixed weight (where a weight model predicts nothing). Note the intercepts differ in kind — the weight line misses the origin by 23 SE, the distance line by 1.75 SE — so the zero-operand step belongs to the zero *value*, not to some generic "buffer holds two things" artifact, which would have shown as a step at HD 1.

Three controls carry it: the `placement` run (`A → ~A`, both homogeneous, both HW 16) is +0.024 W with the sign flipping, bounding the composition confound at ~12 mW or 2% of the HD-32 effect; `ab_equiv` (`0 → A|A` through the dual fill) reads +1.198 W against +1.254 W for the same contrast on plain `ws_l3_x8` in the same session, so the new code path is the old one; and the A/A is +19.1 mW at accuracy 0.509.

**Switching on the load path dominates the line-fill path.** `ws_l3_x8_ab64` alternates every 64 bytes: half the load-to-load transitions toggle but every line-to-line one does. It reads +0.781 W against `ab`'s +1.139 W, where 0.5 × 1.139 = 0.569 W would be expected if only load-to-load mattered. Solving the two gives a load-path term of 1.139 W and a line-path term of 0.211 W, ~16% of the total. Two points plus an additivity assumption — an estimate, not a decomposition to lean on.

Caveat: HD 2 is at the noise floor (SD 0.141, sign flips, predicted +0.068 W). It is in the fit but nothing rests on it; dropping it moves the slope by less than its own SE.

**The instruction matters, but movement matters far more.** `experiments/phase1_instruction_table.json` puts nine instructions behind the identical `ws_l3_x8` load stream — same 8 × 32-byte loads per iteration, 8 independent ops on what was loaded — so traffic is matched and only the instruction varies (`results/20260902-230608-phase1_instruction_table`, 13 runs × 4 repeats, 112/112 gates pass; A/A −8.5 mW, accuracy 0.521; anchor `loads_only` +1.943 W against +2.054, +1.841, +1.904 in three earlier sessions).

Rows are read **paired** (`--against loads_only`) because they share ~2 W of load traffic and the unpaired spread is dominated by run-to-run variation in that shared term. They are read **per byte** because the match is only within 5%: `vpand`, `vpor` and `vpaddd` settle at 140 GB/s against 147 for loads alone (within-victim SD ≈ 1 GB/s, so systematic).

| instruction | Δ power | pJ/byte | vs loads-only, paired | |
|---|---|---|---|---|
| *(none)* | +1.943 W | 13.2 | — | reference |
| `vpsllvd` | +1.849 W | 13.0 | −0.21 [−0.76, +0.34] | indistinguishable |
| `vmovdqa` r–r | +2.036 W | 13.8 | +0.65 [−0.12, +1.41] | indistinguishable |
| `vpdpbusd` | +2.109 W | 14.6 | +1.37 [+0.09, +2.65] | differs |
| `vpmuludq` | +2.195 W | 15.1 | +1.88 [+0.06, +3.70] | differs |
| `vfmadd231ps` | +2.225 W | 15.2 | +2.03 [+1.43, +2.63] | differs |
| `vpaddd` | +2.176 W | 15.5 | +2.36 [+1.39, +3.34] | differs |
| `vpxor` | +2.259 W | 15.6 | +2.45 [+1.36, +3.55] | differs |
| `vpor` | +2.290 W | 16.2 | +3.07 [+2.27, +3.87] | differs |
| `vpand` | +2.284 W | 16.3 | +3.16 [+1.18, +5.13] | differs |

The whole spread is 3.4 pJ/byte against the 13.2 the loads alone cost — so instruction choice moves the leak 10–24%, where operand *depth* moved it 68×. Note the traffic correction works against the finding: the slow rows move less data and still leak more.

**Result Hamming weight explains none of the ordering.** `vpand`/`vpor` (result tracks operand) sit +0.70 and +0.62 pJ/byte above `vpxor` (result pinned at 0), both intervals containing zero — while `vpsllvd`, whose result is *also* pinned at 0, sits 2.66 pJ/byte **below** `vpxor` [−3.87, −1.45]. Results never leave the register file, so they do not leak; this is the cheap form of the plan's operand-HW-vs-result-HW item and it comes back negative.

**Register-resident leakage is instruction-dependent** — the one place this table corrects an earlier finding. Paired against the session A/A: `vpmuludq` null (from the 2×2 below), `vfmadd231ps` **+0.045 W** [+0.021, +0.069], `vpdpbusd` **+0.200 W** [+0.151, +0.249] at detector 0.94. The more the execution unit does per operand bit, the more it leaks with no traffic at all. `vpdpbusd` is what quantised int8 inference issues, so Phase 3 has two independent channels into the same victim, not one.

## Earlier findings (2026-08-24)

**The leakage is in operand movement, not the vector ALU.** Same instruction (`vpmuludq`), same operands (0 vs 0xFFFFFFFF), same interleaved methodology — only the surrounding memory traffic differs. Replicated over 3 repeats with victim order reshuffled each repeat (`results/20260822-215306-phase1_memory_replication`, all 39 gates pass):

| victim | traffic | Δ power | between-run SD | sign | detector |
|---|---|---|---|---|---|
| `avx2_mul` | none (register-resident) | −0.06 W | 0.034 | all − | 0.57 |
| *(A/A control)* | *none — true zero* | *−0.06 W* | *0.075* | *flips* | *0.52* |
| `avx2_load` | loads only, **no ALU** | +0.21 W | 0.017 | all + | 0.85 |
| `avx2_mul_st` | multiply + stores | +0.32 W | 0.049 | all + | 0.93 |
| `avx2_mul_ld` | loads + multiply | +0.34 W | 0.025 | all + | 0.94 |
| `avx2_mul_ldst` | loads + multiply + stores | **+0.51 W** | 0.049 | all + | **0.98** |

Read `avx2_mul` against the A/A row, not against zero: the A/A control has no effect by construction and still lands at −0.06 W, so the register-only victim is at the harness noise floor. **Register-resident `vpmuludq` operands do not measurably leak.** That is instruction-specific and does not generalise — `vpdpbusd` leaks +0.20 W register-resident; see the instruction table above. What this 2×2 shows is that the ALU is not *necessary* for the leak, not that no instruction produces one on its own.

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
- **Sampler overshoots are harmless to a mean difference — but not to a per-symbol decision.** They are bimodal per run (either ~0.1% or ~4% of edges, never between) and uncorrelated with victim, so the sampler phase-locks to the RAPL update in one of two regimes and stays there for a whole run. Within a run they are balanced across conditions (worst imbalance ±0.43%, sign flips) and mean `dtsc`/period is 1.00 for both conditions, so they are common-mode and **cannot bias an A/B difference** — that part stands, and it is why every Phase 0/1 result is unaffected. What does not carry over is the conclusion that they cost only time resolution. A per-symbol decision has no averaging to hide behind: one overshoot is a single RAPL sample spanning two chips, which smears them together and costs those bits outright. Phase 2 therefore gates on `rapl_overshoots` (see *Findings so far*). `rapl_overshoots` is in every manifest.

## Validity gates

`analysis/report.py` enforces these and exits non-zero on failure. No result belongs in the thesis without them:

| Gate | Threshold | Catches |
|---|---|---|
| `zero_ticks` | ≤1% of samples read zero energy | Sampler aliasing against the RAPL update interval (was 9.2%) |
| `interleaving` | temporal imbalance ≤0.10 | Conditions measured at different times, letting drift pose as effect. A sequential design scores ~0.50 |
| `work_balance` | per-condition GB/s within 1% | A condition doing *more work* rather than moving a *different operand*. The assumption every operand claim rests on; argued architecturally until 2026-09-04, measured since |
| `frequency_balance` | per-condition victim MHz within 1%, **Config-A only** | Config-A's premise failing per run. Not applied under Config-B, where a frequency difference between conditions is the tier-2 channel and not a fault |
| `aa_*` | CI contains 0 **and** accuracy ≤0.60 | The whole measurement path manufacturing an effect from nothing |

An A/A control is just an experiment with the same selector in both conditions — no special code path. Every A/B claim should ship with one.

`analysis/covert.py` enforces the Phase 2 equivalents:

| Gate | Threshold | Catches |
|---|---|---|
| `late_chips` | 0 | The transmitter missing its own deadlines, so the BER measures it and not the channel |
| `missed_chips` | 0 | Chips the transmitter *scheduled* but no victim observed. `late_chips` cannot see this — the control thread does one store and a fence per chip and essentially cannot be late — so this is the check that the modulation reached the die |
| `level_range` (warn) | receiver's p5–p95 band ≥10% of itself, over the transmission window, **freq/timing tiers only** | A Config-B run in which the part never throttled, so there was nothing to modulate whatever the rate. Caught three runs in the tier-2 sweep, including two of three repeats at 31.2 bit/s |
| `trace_values` | ≥8 distinct values in the receiver trace | A genuinely dead trace, which still produces a BER |
| `zero_ticks` | ≤1% of samples | As above |
| `aa_ber` | A/A **pooled** across repeats decodes at BER ≥0.40, or p ≥ 0.01 | The decoder finding structure in a transmission that carries none |
| `payload_balance` (warn) | payload within 30–70% ones | A skewed payload letting a skewed decode score well by coincidence |
| `overshoot` (warn) | ≤1% of edges seen late | The unsettled sampler regime, which smears adjacent chips and costs bits |

The A/A here is a transmission with `--on` equal to `--off` — again no special code path, and again every claim should ship with one. The overshoot check warns rather than fails, because the run is still evidence about the channel; it is the *decode* that is degraded.

## Gotchas

- **A "significant" result is not a real one.** A drift-confounded dataset will pass a permutation test with p<0.001 while having no true effect. The `interleaving` gate, not the p-value, is what rules that out.
- **`isolcpus=0` only isolates the attacker core.** Victim cores 2,4,6,8,10 still take stray work. Extending to `isolcpus=0,2,4,6,8,10` needs a GRUB edit and reboot.
- **`-O2` is safe only because every victim hot loop is inline asm.** Do not add a plain-C victim without making its result `volatile`, or the compiler will delete the work being measured.
- **A killed run used to leave the laptop throttled and the results root-owned.** Ctrl-C was always fine; a plain `kill`, a closed terminal or a session teardown was not, because SIGTERM had no handler and skipped both `restore()` and `give_back()`. Fixed — see `tests/test_runner_cleanup.py` for the exact boundary. SIGKILL still cannot be caught by anything, so `sudo src/experiment_runner.py --restore-only` remains the recovery path: it re-enables turbo and hands ownership back, and refuses to touch turbo while any of `driver`, `smoke`, `tx`, `rx_rapl`, `rx_freq` or `rx_timing` is still running.
- **Do not `git add -A` while an experiment is in flight.** The runner appends to `manifest.json` as each run finishes, so a commit made mid-run captures a partial manifest and the next commit shows a spurious several-thousand-line diff. Commit before launching, or stage explicit paths.
- **`settle` is samples-per-block, not blocks-per-run.** It does not protect against a run-level startup transient, which large working sets do produce. Use `--warmup-blocks N` (`warmup_blocks` in an experiment spec) for that: it runs N whole blocks before recording starts, cycling every condition so each one's buffers are faulted in and filled first, and it excludes them from the throughput figure too. Cheap enough to set by default on any working-set victim.
- **Config-A and Config-B are not interchangeable.** Pinning frequency removes the DVFS response that the Phase 2 tier-2/tier-3 receivers depend on entirely. A consequence worth stating in the chapter rather than hiding: tier 1's 241 bit/s of capacity and tier 2's 1.1 are each measured where that tier works, so they are not a controlled comparison of receivers.
- **`rdtsc` is not serialising.** It has no dependency on the work you are timing, so the closing read executes while that work is still in flight. In `rx_timing.c` this reported 64 adds in ~6 TSC ticks — an implied 26 GHz — with a 32% CV that was pure artefact. `LFENCE` on both reads; the CV falls to 3%. Any new self-timing code needs the same.
- **A chain of `add $1, reg` is not one cycle per add on this part.** It retires ~5 per core cycle (ALU width), verified at 12.8M adds in 480 µs against `CLOCK_MONOTONIC`. Harmless for a frequency probe — any fixed non-memory stream is proportional to 1/f_core however it issues — but do not attach a nominal cycle count to one.
- **`frequency_cpufreq()` used to leak a descriptor per call** — its "open once" guard tested a local initialised to `NULL`, so it reopened every time and never closed. Nothing called it, so it never bit; a polling receiver would have run out of descriptors in seconds. Use `cpufreq_open`/`cpufreq_read` in any loop.
- **AVX-VNNI must be assembled as VEX, not EVEX.** `vpdpbusd` exists in both AVX-VNNI (VEX) and AVX512-VNNI (EVEX); gas defaults to EVEX, which SIGILLs here. Hence the `%{vex%}` prefix in `util/victim-utils.c` — spelled with `%` escapes because bare braces mean dialect alternatives to GCC. Any new dual-encoded instruction needs the same treatment; verify with `objdump -d util/victim-utils.o` (VEX starts `c4`, EVEX `62`).
- `legacy/` holds the superseded pipeline; see `legacy/README.md` for why it no longer runs. `src/data/` and `src/plot/` are pre-2026 outputs kept for provenance.
