./venv/bin/python3 -m analysis.report "$r"
./venv/bin/python3 -m analysis.aggregate "$r"
# Critique E2. analysis.mixture is the verdict: the static (scattered) arm against
# the per-transfer and per-stream models, the paired per-repeat test, the
# switching term (blocked - scattered), and iid against the static arm plus Phase
# 1's switching. Read its docstring on WHICH ARM IS WHICH: the spec's pre-committed
# text assigns the roles backwards, and a post-hoc note there says why.
./venv/bin/python3 -m analysis.mixture "$r"
# The static arm fitted exactly as Phase 1's weight sweep was, on mean Hamming
# weight (--axis density). The --labels glob is load-bearing: mixing the arms
# would fit switching into the slope. Blocked second, without a figure, so the
# static arm's figure is the one kept.
./venv/bin/python3 -m analysis.hwfit --axis density --labels 'scat_*' "$r"
./venv/bin/python3 -m analysis.hwfit --axis density --labels 'blk_*' --no-figure "$r"
# The B1.3 sub-domain split, recorded by the driver since 2026-09-29.
./venv/bin/python3 -m analysis.domains "$r"
