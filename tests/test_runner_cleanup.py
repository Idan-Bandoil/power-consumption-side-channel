#!/usr/bin/env python3
"""A killed run must still restore turbo and hand its output back.

    python3 tests/test_runner_cleanup.py

Stdlib-only and unprivileged, like the runner itself: every function that
touches the machine is stubbed, so this is safe to run while an experiment
is in flight. It checks the *ordering* of the cleanup path, which is what
broke -- results/20260822-234811-phase1_traffic_volume was killed mid-sweep
and came back root-owned with turbo still pinned, because give_back() sat
after the try/finally and SIGTERM had no handler at all.

What it does not cover: the real restore()/give_back() against root-owned
files, and whether sudo forwards a signal to the runner it spawned. Those
need a live run.
"""
import json
import os
import signal
import sys
import tempfile
from pathlib import Path

# RUNNER_SRC points the import elsewhere, so the pre-fix runner can be checked
# to confirm these assertions actually catch the bug:
#   git show 8cc4405:src/experiment_runner.py > /tmp/old/experiment_runner.py
#   RUNNER_SRC=/tmp/old python3 tests/test_runner_cleanup.py
# The pre-fix runner does not survive that: it exits 143, killed by the default
# SIGTERM disposition before any cleanup. SIGINT (Ctrl-C) always worked, which
# is why the gap went unnoticed. SIGKILL still cannot be caught by anything --
# `experiment_runner.py --restore-only` stays the recovery path for that.
sys.path.insert(0, os.environ.get(
    "RUNNER_SRC", str(Path(__file__).resolve().parent.parent / "src")))
import experiment_runner as R  # noqa: E402

SPEC = {
    "name": "sigterm_selftest",
    "config": "A",
    "repeats": 2,
    "cooldown_s": 0,
    "driver": {"threads": 1, "samples": 4, "blocks": 2},
    "runs": [{"label": "a", "victim": "avx2_mul", "selectors": [0, 1]},
             {"label": "b", "victim": "avx2_mul", "selectors": [0, 1]},
             {"label": "c", "victim": "avx2_mul", "selectors": [0, 1]}],
}


def run_case(sig, kill_after):
    """Drive main() to completion, delivering `sig` after `kill_after` runs."""
    calls, done = [], []

    def fake_run_driver(run_spec, driver_opts, out_dir, tag, repeat=0):
        done.append(tag)
        (out_dir / f"{tag}.csv").write_text("block,cond,ticks,dtsc,daperf,dmperf\n")
        if len(done) == kill_after:
            calls.append("signal")
            os.kill(os.getpid(), sig)
        return {"tag": tag, "repeat": repeat, "label": run_spec.get("label")}

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        spec_path = tmp / "spec.json"
        spec_path.write_text(json.dumps(SPEC))

        old = {k: getattr(R, k) for k in
               ("RESULTS", "preflight", "build", "apply_config", "restore",
                "give_back", "cooldown", "run_driver")}
        R.RESULTS = tmp / "results"
        R.preflight = lambda: None
        R.build = lambda: None
        R.apply_config = lambda c: None
        R.cooldown = lambda *a, **k: None
        R.restore = lambda: calls.append("restore")
        R.give_back = lambda paths: calls.append("give_back")
        R.run_driver = fake_run_driver
        argv, geteuid = sys.argv, os.geteuid
        sys.argv = ["experiment_runner.py", str(spec_path)]
        os.geteuid = lambda: 0
        try:
            R.main()
        finally:
            sys.argv, os.geteuid = argv, geteuid
            for k, v in old.items():
                setattr(R, k, v)

        out_dir = next((tmp / "results").iterdir())
        manifest = json.loads((out_dir / "manifest.json").read_text())
        on_disk = sorted(p.stem for p in out_dir.glob("*.csv"))
    return calls, done, manifest, on_disk


def check(name, cond, detail=""):
    print(f"  {name:<52} {'PASS' if cond else 'FAIL'}{'  ' + detail if detail else ''}")
    return bool(cond)


def main():
    ok = True
    print("SIGTERM mid-sweep")
    calls, done, manifest, on_disk = run_case(signal.SIGTERM, kill_after=2)
    ok &= check("run aborts at the killed run", len(done) == 2, f"ran {done}")
    ok &= check("restore() still runs", "restore" in calls)
    ok &= check("give_back() still runs", "give_back" in calls)
    ok &= check("restore() before give_back()",
                calls.index("restore") < calls.index("give_back"))
    ok &= check("cleanup happens after the signal",
                calls.index("signal") < calls.index("restore"))
    ok &= check("manifest keeps the completed runs",
                len(manifest["runs"]) == 1 and "finished" in manifest,
                f"{len(manifest['runs'])} of {len(on_disk)} csv(s)")
    # The killed run's own CSV is left on disk but never reaches the manifest.
    # analysis.load iterates the manifest rather than globbing, so a partial
    # run cannot be analysed as if it were whole -- assert that stays true.
    ok &= check("killed run's partial CSV is orphaned, not listed",
                set(on_disk) - {e["tag"] for e in manifest["runs"]} == {"a_r0"})
    ok &= check("every manifest entry still has its CSV",
                all(e["tag"] in on_disk for e in manifest["runs"]))

    print("SIGINT mid-sweep (worked before the fix too)")
    calls, done, _, _ = run_case(signal.SIGINT, kill_after=2)
    ok &= check("restore() and give_back() still run",
                "restore" in calls and "give_back" in calls)

    print("SIGHUP mid-sweep")
    calls, done, _, _ = run_case(signal.SIGHUP, kill_after=1)
    ok &= check("restore() and give_back() still run",
                "restore" in calls and "give_back" in calls)

    print("uninterrupted run")
    calls, done, manifest, on_disk = run_case(signal.SIGTERM, kill_after=99)
    ok &= check("all 6 runs complete", len(done) == 6, f"ran {len(done)}")
    ok &= check("cleanup runs exactly once",
                calls.count("restore") == 1 and calls.count("give_back") == 1)
    ok &= check("manifest lists every run",
                len(manifest["runs"]) == 6 and len(on_disk) == 6)

    print("\n" + ("all checks passed" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
