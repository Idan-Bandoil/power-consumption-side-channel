# How this run's summary.txt is built. Run from the repo root with the run
# directory in $r; see analysis/regen-summary.sh, which is what executes it.
#
# Backfilled: this run predates the summary.cmd convention (a3c8624). The
# commands below are the ones that built the committed summary.txt, read back
# from its section structure. That text came from the analysis code as it stood
# at the run's own commit, so regenerating now adds what has been learned
# since -- the per-condition work and frequency columns (which report as "not
# recorded" for a run this old), the admitted-bias line, and the platform
# power-state section. The measured numbers do not move.
#
# Two sections, and the second is the reason this file matters. The frequency
# table below was computed by hand for this session and no analysis entry point
# reproduces it -- the run is a driver run, so analysis.report reads it, but the
# per-operand MHz difference it was run to measure is not something report
# prints. The table is 13 lines of 676, so a regen that dropped it would shrink
# the summary by 2% and slip under regen-summary.sh's 15% guard unnoticed.
# Hence it is carried here verbatim rather than recomputed.
#
# It is also the null this project keeps citing: no load is distinguishable
# from its A/A control on this proxy, on a channel that demonstrably works at
# 2 bit/s. It compared means over interleaved 0.1 s blocks where the channel
# needs a 256 ms chip, so it was underpowered, not contradictory.

./venv/bin/python3 -m analysis.report "$r"

cat <<'TABLE'


Frequency difference between operands, per load, against the A/A control
(between-repeat spread is the error bar, as everywhere else in this project)

       run     dMHz      SD   vs A/A     SE      t
     t4_s2   -169.9   117.0   -121.6   77.7  -1.56
        t6    -60.1   126.5    -11.8   82.5  -0.14
        t8    -76.9   159.9    -28.5  100.0  -0.29
       t10   -189.9   146.8   -141.5   93.1  -1.52

  A/A itself: -48.4 MHz (SD 66.6), signs flip

  No load is distinguishable from its control. The -804 MHz seen in the
  interrupted first pass (results/20260903-132837) does not reproduce.
TABLE
