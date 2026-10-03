./venv/bin/python3 -m analysis.report "$r"
# Critique C5, bit placement at HW 8. The verdict is the SIX-repeat pooled one:
# a background time limit cut this session to 4 repeats, so it is pooled with its
# 2-repeat top-up. analysis.placement takes both dirs and blocks by (session,
# repeat). work_balance fails on some patterns -- the ~1-2% two-buffer placement
# noise the sweep also shows, sign-flipping, which is the floor being measured,
# not an operand-content work difference. One aa_l3 repeat fails marginally; the
# pooled A/A is clean (see analysis.placement).
./venv/bin/python3 -m analysis.placement "$r" results/20260930-162959-phase1_placement_topup
