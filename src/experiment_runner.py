#!/usr/bin/env python3
"""Run a declarative experiment and archive its data with a full manifest.

Deliberately stdlib-only: this half runs as root, so it must not need the
venv. Analysis (which needs numpy/matplotlib) runs unprivileged afterwards
via the `analysis` package.

    sudo python3 src/experiment_runner.py experiments/phase0_validate.json
"""
import argparse
import glob
import json
import logging
import os
import random
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
BIN = SRC / "bin"
RESULTS = REPO / "results"
EXPERIMENTS = REPO / "experiments"

NO_TURBO = "/sys/devices/system/cpu/intel_pstate/no_turbo"
RAPL_ROOT = "/sys/class/powercap/intel-rapl:0"

logging.basicConfig(level=logging.INFO,
                    format="[%(asctime)s] %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("runner")


# ---------------------------------------------------------------------------
# System state
# ---------------------------------------------------------------------------

def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _write(path, value):
    try:
        with open(path, "w") as f:
            f.write(value)
        return True
    except OSError as e:
        logger.warning("could not write %s: %s", path, e)
        return False


def package_temp_c():
    """Package temperature in Celsius, or None if coretemp is unavailable."""
    for label in glob.glob("/sys/class/hwmon/hwmon*/temp*_label"):
        if _read(label, "").startswith("Package"):
            raw = _read(label.replace("_label", "_input"))
            if raw:
                return int(raw) / 1000.0
    raw = _read("/sys/class/thermal/thermal_zone0/temp")
    return int(raw) / 1000.0 if raw else None


def system_state():
    """Everything about the machine that could plausibly move the readings."""
    cpus = sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq"))
    return {
        "no_turbo": _read(NO_TURBO),
        "governor": _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        "scaling_driver": _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver"),
        "package_temp_c": package_temp_c(),
        "pl1_uw": _read(f"{RAPL_ROOT}/constraint_0_power_limit_uw"),
        "pl2_uw": _read(f"{RAPL_ROOT}/constraint_1_power_limit_uw"),
        "cur_freq_khz": {
            os.path.basename(os.path.dirname(c)): _read(f"{c}/scaling_cur_freq")
            for c in cpus[:20]
        },
        "loadavg": _read("/proc/loadavg"),
        "cmdline": _read("/proc/cmdline"),
        "uptime_s": float((_read("/proc/uptime") or "0 0").split()[0]),
    }


def give_back(paths):
    """Return root-created files to the invoking user (SUDO_UID)."""
    uid = int(os.environ.get("SUDO_UID", 0))
    gid = int(os.environ.get("SUDO_GID", 0))
    if not uid:
        return
    for root_path in paths:
        p = Path(root_path)
        if not p.exists():
            continue
        for q in ([p, *p.rglob("*")] if p.is_dir() else [p]):
            try:
                os.chown(q, uid, gid)
            except OSError:
                pass


def git_commit():
    try:
        out = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True)
        dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                               capture_output=True, text=True, check=True)
        return {"commit": out.stdout.strip(), "dirty": bool(dirty.stdout.strip())}
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"commit": None, "dirty": None}


# ---------------------------------------------------------------------------
# Frequency configuration
# ---------------------------------------------------------------------------

def apply_config(name):
    """Config-A pins frequency (isolates power leakage from DVFS).
    Config-B leaves turbo on (required by the frequency/timing receivers)."""
    if name == "A":
        if _write(NO_TURBO, "1"):
            logger.info("Config-A: turbo disabled, frequency pinned to base")
    elif name == "B":
        if _write(NO_TURBO, "0"):
            logger.info("Config-B: turbo enabled, DVFS free to respond")
    else:
        raise SystemExit(f"unknown config '{name}' (expected A or B)")


def restore():
    _write(NO_TURBO, "0")
    logger.info("turbo re-enabled")


MAX_START_LOAD = 2.0


def load1():
    return float((_read("/proc/loadavg") or "0").split()[0])


def preflight():
    """Refuse to measure on a busy machine.

    Victims are cloned with CLONE_VM and spin on ctl->run; if a driver ever
    dies without clearing it the children survive at 100% CPU on their pinned
    cores and quietly poison every later run. Victims now arm PR_SET_PDEATHSIG
    so that cannot happen, but stale processes from older builds, or anything
    else the machine is doing, would corrupt the measurement just as well.
    """
    stray = []
    for name in ("driver", "smoke"):
        out = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True)
        if out.returncode == 0:
            stray += [f"{name}:{p}" for p in out.stdout.split()]
    if stray:
        raise SystemExit(f"refusing to start: stray measurement processes {stray}\n"
                         f"  kill them with: pkill -9 -x driver; pkill -9 -x smoke")

    load = load1()
    if load > MAX_START_LOAD:
        raise SystemExit(f"refusing to start: 1-minute load average is {load:.2f} "
                         f"(limit {MAX_START_LOAD}).\n"
                         f"  Wait for the machine to go idle, or pass a higher "
                         f"limit if this is expected.")
    logger.info("preflight ok (load %.2f, package %.1fC)", load, package_temp_c() or -1)


def cooldown(seconds, target_c=None):
    """Fixed settle, optionally extended until the package is cool enough.

    Thermal state is the confound that ruined the earlier datasets; blocks are
    interleaved to cancel it within a run, and this keeps runs comparable."""
    if seconds:
        logger.info("cooling down %.0fs (package %.1fC)", seconds, package_temp_c() or -1)
        time.sleep(seconds)
    deadline = time.time() + 300
    while time.time() < deadline:
        t = package_temp_c()
        cool = target_c is None or t is None or t <= target_c
        quiet = load1() <= MAX_START_LOAD
        if cool and quiet:
            return
        time.sleep(5)
    logger.warning("cooldown did not settle within 5 min (%.1fC, load %.2f)",
                   package_temp_c() or -1, load1())


# ---------------------------------------------------------------------------
# Driver invocation
# ---------------------------------------------------------------------------

def build():
    subprocess.run(["modprobe", "msr"], check=False)
    subprocess.run(["make"], cwd=SRC, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    logger.info("build ok")


DRIVER_DEFAULTS = {
    "threads": 4,
    "samples": 100,
    "blocks": 100,
    "settle": 3,
    "attacker_core": 0,
    "victim_core_start": 2,
    "victim_core_stride": 2,
    "max_victim_core": 11,
    "mode": "edge",
    "order": "shuffled",
    "fixed_cycles": 2500000,
    "seed": 12345,
}

# JSON key -> driver long option
DRIVER_FLAGS = {
    "threads": "--threads",
    "samples": "--samples",
    "blocks": "--blocks",
    "settle": "--settle",
    "attacker_core": "--attacker-core",
    "victim_core_start": "--victim-core-start",
    "victim_core_stride": "--victim-core-stride",
    "max_victim_core": "--max-victim-core",
    "mode": "--mode",
    "order": "--order",
    "fixed_cycles": "--fixed-cycles",
    "seed": "--seed",
}


def run_driver(run_spec, driver_opts, out_dir, tag, repeat=0):
    selectors = run_spec["selectors"]
    input_path = out_dir / f"{tag}.input.txt"
    csv_path = out_dir / f"{tag}.csv"
    input_path.write_text("".join(f"{s}\n" for s in selectors))

    # Experiment-level driver options, overridable per run.
    opts = dict(DRIVER_DEFAULTS)
    opts.update(driver_opts)
    opts.update({k: v for k, v in run_spec.items() if k in DRIVER_FLAGS})

    cmd = [
        str(BIN / "driver"),
        "--victim", run_spec["victim"],
        "--input", str(input_path),
        "--out", str(csv_path),
    ]
    for key, flag in DRIVER_FLAGS.items():
        cmd += [flag, str(opts[key])]

    logger.info("running %s: victim=%s selectors=%s",
                tag, run_spec["victim"], selectors)
    before = system_state()
    started = time.time()
    proc = subprocess.run(cmd, cwd=SRC, capture_output=True, text=True)
    elapsed = time.time() - started

    if proc.returncode != 0:
        logger.error("driver failed (%d):\n%s", proc.returncode, proc.stderr[-2000:])
        raise SystemExit(proc.returncode)

    try:
        summary = json.loads(proc.stdout)
    except json.JSONDecodeError:
        logger.error("driver produced no JSON summary:\n%s", proc.stdout[-2000:])
        raise SystemExit(1)

    after = system_state()
    logger.info("  %s samples in %.1fs, RAPL period %.3f ms, %d overshoots",
                summary["samples_written"], elapsed,
                summary["rapl_period_ms"], summary["rapl_overshoots"])

    return {
        "tag": tag,
        "repeat": repeat,
        "label": run_spec.get("label", run_spec["victim"]),
        "victim": run_spec["victim"],
        "selectors": selectors,
        "csv": csv_path.name,
        "elapsed_s": elapsed,
        "driver": summary,
        "driver_argv": cmd,
        "state_before": before,
        "state_after": after,
    }


# ---------------------------------------------------------------------------

def _raise_interrupt(signum, frame):
    """Turn a termination signal into the exception the cleanup path expects."""
    raise KeyboardInterrupt(f"signal {signum}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("experiment", nargs="?",
                    help="path to an experiment JSON spec")
    ap.add_argument("--restore-only", action="store_true",
                    help="re-enable turbo and exit; use if a run was killed "
                         "before its cleanup could run")
    ap.add_argument("--cooldown", type=float, default=None,
                    help="override per-run cooldown seconds")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit")
    args = ap.parse_args()

    if args.restore_only:
        if os.geteuid() != 0:
            raise SystemExit("must run as root to change turbo state")
        # Config-A leaves no_turbo=1. A run killed before its finally block
        # leaves the machine pinned at base clock indefinitely.
        live = subprocess.run(["pgrep", "-x", "driver"], capture_output=True)
        if live.returncode == 0:
            raise SystemExit("a driver is still running; refusing to change "
                             "turbo state mid-experiment")
        restore()
        give_back([RESULTS, SRC / "obj", SRC / "bin", *REPO.glob("util/*.o")])
        return

    if not args.experiment:
        raise SystemExit("need an experiment spec (or --restore-only)")

    path = Path(args.experiment)
    if not path.exists():
        path = EXPERIMENTS / args.experiment
    spec = json.loads(path.read_text())

    if args.dry_run:
        print(json.dumps(spec, indent=2))
        return

    if os.geteuid() != 0:
        raise SystemExit("must run as root (RAPL MSRs are root-only)")

    # A plain `kill` would otherwise skip both restore() and give_back(),
    # leaving the machine pinned at base clock and the results root-owned.
    signal.signal(signal.SIGTERM, _raise_interrupt)
    signal.signal(signal.SIGHUP, _raise_interrupt)

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + spec["name"]
    out_dir = RESULTS / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    cool = args.cooldown if args.cooldown is not None else spec.get("cooldown_s", 30)
    manifest = {
        "run_id": run_id,
        "experiment": spec,
        "spec_path": str(path),
        "git": git_commit(),
        "started": datetime.now().isoformat(timespec="seconds"),
        # os.uname() is a structseq, not a namedtuple: no _asdict().
        "uname": {k: getattr(os.uname(), k) for k in
                  ("sysname", "nodename", "release", "version", "machine")},
        "cpu_model": next((l.split(":", 1)[1].strip()
                           for l in _read("/proc/cpuinfo", "").splitlines()
                           if l.startswith("model name")), None),
        "runs": [],
    }

    try:
        preflight()
        build()
        apply_config(spec.get("config", "A"))
        # Let the frequency change settle before the first measurement.
        time.sleep(2)

        repeats = int(spec.get("repeats", 1))
        base_seed = int(spec.get("driver", {}).get("seed", 12345))
        order_rng = random.Random(spec.get("run_order_seed", 20260822))

        for rep in range(repeats):
            runs = list(spec["runs"])
            # Randomise victim order per repeat. Interleaving cancels drift
            # *within* a run, but comparing effect sizes *across* runs would
            # otherwise be confounded with position in the session -- later
            # runs sit on a warmer die.
            if spec.get("shuffle_runs", repeats > 1):
                order_rng.shuffle(runs)
            logger.info("--- repeat %d/%d: %s ---", rep + 1, repeats,
                        " ".join(r.get("label", r["victim"]) for r in runs))

            for i, run_spec in enumerate(runs):
                base = run_spec.get("label") or f"{run_spec['victim']}_{i}"
                tag = f"{base}_r{rep}" if repeats > 1 else base
                # A distinct block-order seed per repeat.
                rs = dict(run_spec)
                rs["seed"] = int(rs.get("seed", base_seed)) + 1000 * rep

                cooldown(cool, spec.get("cooldown_target_c"))
                manifest["runs"].append(
                    run_driver(rs, spec.get("driver", {}), out_dir, tag, repeat=rep))
                # Persist after every run so a crash still leaves usable metadata.
                (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    except KeyboardInterrupt:
        logger.warning("interrupted")
    finally:
        manifest["finished"] = datetime.now().isoformat(timespec="seconds")
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        restore()
        # Everything here was created as root. Hand it back, or the next
        # unprivileged `make` and the analysis step both fail on permissions.
        # RESULTS itself, not just out_dir: the parent is created by the first
        # root run and would otherwise stay root-owned and unwritable. In the
        # finally block, not after it, so a kill mid-run still hands back what
        # was written -- a half-finished sweep is still worth analysing.
        give_back([RESULTS, SRC / "obj", SRC / "bin", *REPO.glob("util/*.o")])

    logger.info("results in %s", out_dir)
    print(f"\nNext: ./venv/bin/python3 -m analysis.report {out_dir}")


if __name__ == "__main__":
    main()
