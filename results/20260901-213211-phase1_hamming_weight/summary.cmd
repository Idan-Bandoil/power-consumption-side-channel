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
# Three sections. The hwfit is the experiment: the sweep read as a slope, one
# fit per repeat with the between-repeat spread as the error bar. It is fitted
# over every hw* run, the A/A being kept out of the fit by hwfit itself.
#
# The committed text has no "figure ->" line because hwfit did not print one
# yet on 2026-09-01; the figure was written all the same, and regenerating now
# adds the line.

echo "### analysis.report $r"
echo
./venv/bin/python3 -m analysis.report "$r"
echo
echo "### analysis.aggregate $r"
echo
./venv/bin/python3 -m analysis.aggregate "$r"
echo
echo "### analysis.hwfit $r"
echo
./venv/bin/python3 -m analysis.hwfit "$r"
echo
echo "### analysis.modelcompare $r"
echo
# §6's model comparison (critique C6): every functional form fitted with a free
# intercept and ranked on AICc + held-out RMSE, not raw R². Single-session here;
# the pooled comparison §6/§8 also quote is wired into the low_end run.
./venv/bin/python3 -m analysis.modelcompare "$r"
