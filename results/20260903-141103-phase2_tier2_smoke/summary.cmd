# How this run's summary.txt is built. Run from the repo root with the run
# directory in $r; see analysis/regen-summary.sh, which is what executes it.
#
# A two-run smoke of the tier-2 pair, not a result -- but it carries the same
# two sections the tier-2 sweep does: the receiver's own core, then the second
# watched CPU.
./venv/bin/python3 -m analysis.covert "$r"
echo
echo "### watching cpu2 (a victim core) instead ###"
echo
./venv/bin/python3 -m analysis.covert "$r" --freq-column 1
