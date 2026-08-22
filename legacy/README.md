# Legacy pipeline (superseded)

These are the pre-2026 shell/Python scripts. They are kept for provenance —
the figures in `Research Proposal.pdf` came from them — but they no longer
run against the current driver:

- `run-driver.sh` invokes the driver with 4 positional arguments; the driver
  now takes long options and requires `--victim`.
- `plot-hd.py` is called without its required `sample_count` positional.
- `configurations` names `avx512mul`, a victim that was removed (this CPU has
  AVX-512 fused off — `/proc/cpuinfo` shows only `avx avx2 avx_vnni`).
- `plot-hd.py` calls `seaborn.distplot`, removed in seaborn 0.14, and
  seaborn's KDE needs scipy, which is not installed in `venv/`.

Two methodological problems in this pipeline are the reason it was replaced,
both documented in `analysis/` and enforced as gates there:

1. **Conditions ran sequentially**, one long block each, so thermal drift was
   confounded with the condition under test. `src/data/out-1207-2115` shows a
   15 W "effect" that is entirely the package heating and hitting its power
   limit, pointing opposite to the hypothesis.
2. **Sampling aliased against the RAPL update interval.** 9.2% of samples in
   `src/data/out-1228-1527` read exactly zero energy; the analysis discarded
   those while keeping the double-length windows that followed, biasing the
   mean up and inflating variance.

Current pipeline: `src/driver.c` + `src/experiment_runner.py` + `analysis/`.
