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
# Three sections, concatenated without headers, which is how this session's
# summaries were built. The hwfit settles the shape of the curve near zero, so
# these points pool with the 2026-09-01 sweep -- the driver settings match it
# deliberately.
#
# --labels 'hw*' keeps anchor_hw16 out of the fit, and it is load-bearing.
# The anchor is there for cross-session comparison, not as a point on the
# axis: it sits at HW 16 and +1.23 W, five weights above anything else here,
# so letting it in extends the x-range fourfold and turns a deliberately
# low-end fit into a whole-range one. It reads +57.15 mW/bit at R^2 0.912
# with the anchor against +61.59 at R^2 0.680 without it -- which looks like
# the better fit and answers the wrong question. The point of this session is
# that the low end alone is too short a lever to fit (SD 18.4 over three
# repeats); the pooled fit across both sessions is what carries the result.

./venv/bin/python3 -m analysis.report "$r"
./venv/bin/python3 -m analysis.aggregate "$r"
./venv/bin/python3 -m analysis.hwfit "$r" --labels 'hw*'
echo
echo "### analysis.modelcompare (pooled with the 2026-09-01 sweep, 18 operands) $r"
echo
# The pooled fair model comparison §6/§8 quote (critique C6): ΔAICc √HW 13.7,
# log 30.9; held-out RMSE linear 90 mW vs √HW 139, log 232. Pools both sessions,
# --labels hw* keeping the anchor out exactly as the hwfit line above does.
./venv/bin/python3 -m analysis.modelcompare results/20260901-213211-phase1_hamming_weight "$r"
