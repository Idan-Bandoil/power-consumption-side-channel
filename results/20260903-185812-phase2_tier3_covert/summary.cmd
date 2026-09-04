# How this run's summary.txt is built. Run from the repo root with the run
# directory in $r; see analysis/regen-summary.sh, which is what executes it.
#
# Superseded by 20260903-194816. This is tier 3's first pass, kept because it
# is the run that failed its A/A gate on an unbalanced payload -- the evidence
# for why payloads are balanced by construction now. analysis.covert exits
# non-zero on it, which is the point.
./venv/bin/python3 -m analysis.covert "$r"
