# How this run's summary.txt is built. Run from the repo root with the run
# directory in $r; see analysis/regen-summary.sh, which is what executes it.
#
# Two sections: the receiver's own core, then the same trace decoded from the
# second watched CPU. rx.watch put a victim core there deliberately -- an
# attacker need not know where the victim runs, and watching one is a step
# better at 3.9 and 7.8 bit/s than watching its own.
./venv/bin/python3 -m analysis.covert "$r"
echo
echo "### watching cpu2 (a victim core) instead ###"
echo
./venv/bin/python3 -m analysis.covert "$r" --freq-column 1
