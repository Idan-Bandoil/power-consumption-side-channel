# `_crosscheck` — corpus-wide checks on the instrument

Everything else under `results/` is one experiment. This directory holds checks
that only a whole corpus of runs can answer, so it is not a run directory: it
has no `manifest.json`, and `analysis/regen-summary.sh` skips it by design.

`instrument.txt` is built by:

```bash
./venv/bin/python3 -m analysis.instrument results/ > results/_crosscheck/instrument.txt
```

It carries three sections — whether the sampler's reported RAPL period is a
property of the part or of the estimator, the spread of achieved victim
throughput per victim, and the bias the `interleaving` gate admits in watts.
The first two read `manifest.json` only; `bias` reads the CSVs, so it covers
just the runs whose raw data is still on disk.

## It is a dated snapshot, not a live view

The committed text was generated on 2026-09-04 in commit `7b8dd0d`, over the
**322** edge-sampled runs in the corpus at that moment — the number
`thesis/critique.md` cites in item B4, in its Outcomes entry for B4, and in
B4.2. The `phase1_depth_operand` session landed later the same day and added
66 runs, so re-running the command now reads 388.

That re-run was done on 2026-09-21 and **all three verdicts are unchanged**:

- Period: the contamination model predicts slope/intercept = 1.00 if the
  reported period were an estimator artifact; measured is −0.10 at 388 runs
  against −0.01 at 322. Still no material dependence on overshoot rate, and
  the reported period still sits +2.4% above the datasheet 2⁻¹⁰ s.
- Throughput: the per-victim means hold (`ws_l3_x8` 139.9 GB/s over 171 runs
  against 139.3 over 147). One new outlier appears and is already known —
  `ws_l2_x8` now reads a minimum of 1.6 GB/s, which is the contaminated
  `l2_hw08_r2` cell that suspended mid-run.
- Admitted bias: median effect/bias improves from 104× to 130×.

The file is left at its 322-run state so those citations stay exact.
Refreshing it means re-running the command above and updating all three `322`
citations in `thesis/critique.md` in the same commit.
