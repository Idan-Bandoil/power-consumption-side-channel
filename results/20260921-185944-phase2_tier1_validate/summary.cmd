# How this run's summary.txt is built. Run from the repo root with the run
# directory in $r; see analysis/regen-summary.sh, which is what executes it.
#
# This session was ABORTED after 9 of 70 runs and is kept only as the evidence
# for a sampler fix. The note below is hand-written and no analysis entry point
# reproduces it, so it is carried verbatim -- same convention as the frequency
# table in 20260903-134424-phase2_tier2_feasibility.

cat <<'NOTE'
==============================================================================
aborted session -- kept as evidence, not as a measurement
==============================================================================
  Stopped 9 runs into 70. Nothing here is quotable: no condition has its
  repeats, and the instrument was in the state described below for part of it.

  What it caught. util/sampler.c seeded its RAPL period estimate from the
  first observed interval, which is a fragment of a period rather than a
  period -- the sampler opens at an arbitrary phase within one, so that first
  dtsc is uniform in (0, T]. A separate change had moved the EWMA update into
  the else-branch of the overshoot test, so an edge classified as late no
  longer fed the estimator. Those two together make an absorbing state: seed
  under about two thirds of T and every subsequent real edge trips the
  overshoot test, which is also the branch that cannot correct it. The
  estimate froze at the seed for the whole run.

  Five of these nine runs latched, at 0.17-0.23 ms against a true 0.96-0.97 ms,
  and reported 99.99% of their edges late. Recomputed from the recorded
  intervals the same runs read 0.16-0.29% -- so the sampler was recording
  correctly and was only wrong about itself. Recorded energies are unaffected
  either way, because the poll loop runs until the counter actually moves; what
  the estimate drives is the guard window, so a latched run polls about 80% of
  each period instead of 12%, and its overshoot figure means nothing.

  This regression postdates the published corpus. At c0939cb -- the commit the
  tier-1 rate sweep ran at -- the EWMA ran unconditionally on every edge and
  the overshoot test only incremented a counter, so no latch was possible and
  no published number moves.

  Fixed in util/sampler.c by discarding the opening fragment and learning
  unconditionally through warmup, which makes the absorbing state unreachable
  rather than unlikely. analysis/covert.py now recomputes the overshoot
  fraction from the recorded dtsc and fails a run whose sampler disagreed with
  its own trace, so a gate no longer rests on the instrument's opinion of
  itself. tests/test_covert_decode.py pins both.

  The decode below therefore runs on the FIXED analysis against traces taken
  with the BROKEN sampler. The estimator-disagreement failures are the point.
NOTE

./venv/bin/python3 -m analysis.covert "$r"
