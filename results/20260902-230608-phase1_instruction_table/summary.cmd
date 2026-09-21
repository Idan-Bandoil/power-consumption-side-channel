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
# Six sections, and this is the directory regen-summary.sh exists for: a
# uniform report+aggregate pass over it would delete four of them. Every row
# here carries the same ~2 W of load traffic and differs only in the
# instruction on top, so the unpaired spread is dominated by run-to-run
# variation in that shared term and the table cannot be read from it.
#
#   1. report                          -- the gates, 112/112 pass.
#   2. aggregate                       -- unpaired, for the shared term itself.
#   3. --against loads_only            -- the instruction's own cost in watts.
#   4. --against loads_only --per-byte -- the same in pJ/byte, which is the
#      column to quote: the match on traffic is only within 5% (vpand, vpor and
#      vpaddd settle at 140 GB/s against 147 for loads alone), and the
#      correction works against the finding, since the slow rows move less data
#      and still leak more.
#   5. --against op_xor --per-byte     -- the result-Hamming-weight contrast.
#      vpand/vpor track the operand where vpxor pins the result at 0.
#   6. --against aa_op                 -- the register-resident rows, which are
#      paired against the session A/A rather than against the load stream.

./venv/bin/python3 -m analysis.report "$r"
./venv/bin/python3 -m analysis.aggregate "$r"
./venv/bin/python3 -m analysis.aggregate "$r" --against loads_only
./venv/bin/python3 -m analysis.aggregate "$r" --against loads_only --per-byte
./venv/bin/python3 -m analysis.aggregate "$r" --against op_xor --per-byte
./venv/bin/python3 -m analysis.aggregate "$r" --against aa_op
