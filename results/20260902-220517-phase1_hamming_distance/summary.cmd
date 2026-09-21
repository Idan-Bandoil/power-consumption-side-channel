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
# Three sections, concatenated without headers.
#
# The hwfit reads this sweep against Hamming *distance*, not weight, and
# --labels keeps the fit to the hd* runs. That restriction is not cosmetic:
# anchor_hw16, ab_equiv and placement all sit at distance 0, so fitting them as
# points on the distance axis would put three unrelated values on one x.

./venv/bin/python3 -m analysis.report "$r"
./venv/bin/python3 -m analysis.aggregate "$r"
./venv/bin/python3 -m analysis.hwfit "$r" --axis hd --labels 'hd*'
