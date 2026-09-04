# How this run's summary.txt is built. Run from the repo root with the run
# directory in $r; see analysis/regen-summary.sh, which is what executes it.
./venv/bin/python3 -m analysis.covert "$r"
