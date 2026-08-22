"""Analysis for the AVX power side-channel experiments.

Runs unprivileged, against the results directories written by
`src/experiment_runner.py`. Deliberately numpy-only: this venv has no scipy
or sklearn, and every statistic used here is short enough to state directly,
which is worth more in a thesis than a library call anyway.
"""
