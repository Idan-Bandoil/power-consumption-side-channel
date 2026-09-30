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


def power_source():
    """Mains online, and what the battery is doing.

    Never recorded until 2026-09-04, which made an obvious question about the
    corpus -- was the charger plugged in? -- unanswerable from the manifests.
    It turned out not to be the variable that moved (see thermald_running),
    but that could only be established indirectly, and it should not have had
    to be.
    """
    ac, bat = None, None
    for s in sorted(glob.glob("/sys/class/power_supply/*")):
        kind = _read(f"{s}/type")
        if kind == "Mains" and ac is None:
            ac = _read(f"{s}/online")
        elif kind == "Battery" and bat is None:
            bat = _read(f"{s}/status")
    return ac, bat


def thermald_running():
    """Whether a daemon that rewrites PL1 underneath the experiment is up.

    thermald lowers constraint_0_power_limit_uw as the die heats, and it does
    so *during* a run: the corpus contains sessions where PL1 went 200 W -> 15
    -> 35 as the package crossed ~50 C, with the step landing mid-run. No
    measurement here was distorted by it -- package power exceeded the
    nominal limit in those runs without being clamped, and their effects were
    if anything the largest of their group -- but the platform was silently
    changing a power limit under a power measurement, which is not a state to
    measure in without knowing.
    """
    out = subprocess.run(["pgrep", "-x", "thermald"], capture_output=True)
    return out.returncode == 0


def system_state():
    """Everything about the machine that could plausibly move the readings."""
    cpus = sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq"))
    ac, bat = power_source()
    return {
        "no_turbo": _read(NO_TURBO),
        "governor": _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        "scaling_driver": _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver"),
        "package_temp_c": package_temp_c(),
        "ac_online": ac,
        "battery_status": bat,
        "platform_profile": _read("/sys/firmware/acpi/platform_profile"),
        "pl1_uw": _read(f"{RAPL_ROOT}/constraint_0_power_limit_uw"),
        "pl2_uw": _read(f"{RAPL_ROOT}/constraint_1_power_limit_uw"),
        "pl1_window_us": _read(f"{RAPL_ROOT}/constraint_0_time_window_us"),
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


def open_output_dir(out_dir):
    """Make the run's output directory writable by the invoking user.

    Setup, not cleanup: the unprivileged receivers are dropped to SUDO_UID and
    open their own CSV, so a root-owned directory fails them mid-run rather
    than at the end. give_back() in the finally block still covers everything
    root created afterwards; this only opens the door first.
    """
    give_back([RESULTS, out_dir])


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

# Anything that spawns victims or holds an MSR open. A survivor from an older
# run spins at 100% on a pinned core and quietly poisons every later
# measurement, so a run refuses to start while one is alive.
MEASURING_PROCS = ("driver", "smoke", "tx", "rx_rapl", "rx_freq", "rx_timing",
                   "battery_xcheck")


def load1():
    return float((_read("/proc/loadavg") or "0").split()[0])


def preflight(allow_battery=False):
    """Refuse to measure on a busy machine, or in the wrong power state.

    Victims are cloned with CLONE_VM and spin on ctl->run; if a driver ever
    dies without clearing it the children survive at 100% CPU on their pinned
    cores and quietly poison every later run. Victims now arm PR_SET_PDEATHSIG
    so that cannot happen, but stale processes from older builds, or anything
    else the machine is doing, would corrupt the measurement just as well.
    """
    stray = []
    for name in MEASURING_PROCS:
        out = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True)
        if out.returncode == 0:
            stray += [f"{name}:{p}" for p in out.stdout.split()]
    if stray:
        kill = "; ".join(f"pkill -9 -x {n}" for n in MEASURING_PROCS)
        raise SystemExit(f"refusing to start: stray measurement processes {stray}\n"
                         f"  kill them with: {kill}")

    load = load1()
    if load > MAX_START_LOAD:
        raise SystemExit(f"refusing to start: 1-minute load average is {load:.2f} "
                         f"(limit {MAX_START_LOAD}).\n"
                         f"  Wait for the machine to go idle, or pass a higher "
                         f"limit if this is expected.")

    # Sessions have to be comparable to each other, and a laptop measured on
    # battery is not the same instrument as one on mains: the platform lowers
    # its power limits, and the whole thesis is a power measurement. Every
    # committed session was recorded on mains, so that is the baseline.
    ac, bat = power_source()
    if ac == "0" and not allow_battery:
        raise SystemExit(
            f"refusing to start: running on battery (AC offline, battery "
            f"{bat}).\n"
            f"  Every committed session was measured on mains, and the "
            f"platform lowers its power limits on battery, so a session run "
            f"this way is not comparable to the rest of the corpus.\n"
            f"  Plug the charger in, or pass --allow-battery if this is "
            f"deliberate.")

    # thermald rewrites PL1 while the experiment runs. It is a warning rather
    # than a refusal because the corpus shows it did not distort anything --
    # see thermald_running() -- and because stopping it changes the platform
    # from the state every earlier session was measured in.
    if thermald_running():
        logger.warning("thermald is running and will move PL1 as the die "
                       "heats; PL1 is recorded per run, and analysis.report "
                       "fails the session if it moved. `systemctl stop "
                       "thermald` before a session to hold it fixed.")

    pl1 = _read(f"{RAPL_ROOT}/constraint_0_power_limit_uw")
    logger.info("preflight ok (load %.2f, package %.1fC, AC %s, battery %s, "
                "PL1 %.1f W)", load, package_temp_c() or -1, ac, bat,
                (float(pl1) / 1e6) if pl1 else float("nan"))


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
    "warmup_blocks": 0,
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
    "warmup_blocks": "--warmup-blocks",
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
        # A run whose expected difference is zero but whose selectors do not
        # say so. The two-buffer A/A holds two different 64-bit selectors whose
        # low halves match, giving two distinct buffers with identical
        # contents -- the only A/A that can see the buffer-address confound.
        # Carried through so analysis.report gates it as a control.
        "control": run_spec.get("control"),
        "csv": csv_path.name,
        "elapsed_s": elapsed,
        "driver": summary,
        "driver_argv": cmd,
        "state_before": before,
        "state_after": after,
    }


# ---------------------------------------------------------------------------
# Covert channel (Phase 2)
# ---------------------------------------------------------------------------

TX_DEFAULTS = {
    "victim": "ws_l3_x8",
    "threads": 4,
    "on": 4294967295,
    "off": 0,
    "symbol_us": 4000,
    "code": "manchester",
    "random_bits": 64,
    "seed": 12345,
    "frames": 8,
    "preamble": "1111100110101",
    "lead_ms": 300,
    "tail_ms": 300,
    "prewarm_ms": 600,
    "tx_core": 12,
    "victim_core_start": 2,
    "victim_core_stride": 2,
    "max_victim_core": 11,
}

TX_FLAGS = {
    "victim": "--victim",
    "threads": "--threads",
    "on": "--on",
    "off": "--off",
    "symbol_us": "--symbol-us",
    "code": "--code",
    "random_bits": "--random-bits",
    "seed": "--seed",
    "frames": "--frames",
    "preamble": "--preamble",
    "lead_ms": "--lead-ms",
    "tail_ms": "--tail-ms",
    "prewarm_ms": "--prewarm-ms",
    "tx_core": "--tx-core",
    "victim_core_start": "--victim-core-start",
    "victim_core_stride": "--victim-core-stride",
    "max_victim_core": "--max-victim-core",
}

# Tier 1 (RAPL, root) and tier 2 (scaling_cur_freq, unprivileged) take
# different options, so each receiver carries its own flag map. "tier" in an
# experiment's `rx` section picks between them.
RX_RECEIVERS = {
    "rapl": {
        "bin": "rx_rapl",
        "defaults": {"core": 0, "mode": "edge"},
        "flags": {"core": "--core", "mode": "--mode"},
        "root": True,
    },
    "freq": {
        "bin": "rx_freq",
        "defaults": {"core": 1, "watch": 2, "interval_us": 200},
        "flags": {"core": "--core", "watch": "--watch",
                  "interval_us": "--interval-us"},
        "root": False,
    },
    "timing": {
        "bin": "rx_timing",
        "defaults": {"core": 0, "sample_us": 500},
        "flags": {"core": "--core", "sample_us": "--sample-us"},
        "root": False,
    },
}

# Seconds the receiver keeps recording past the transmitter's last symbol.
RX_MARGIN_S = 2.0
# Seconds the receiver runs before the transmitter starts, so the trace opens
# on an idle baseline the decoder can measure a false-sync rate against.
RX_LEAD_S = 1.0


def tx_bits_per_frame(opts):
    payload = len(opts["message"]) * 8 if opts.get("message") else int(opts["random_bits"])
    return len(str(opts["preamble"])) + payload


def run_covert(run_spec, tx_opts, rx_opts, out_dir, tag, repeat=0):
    """One transmission, recorded by an independent receiver process.

    The two are separate processes on purpose: the transmitter shares no
    memory with the receiver, so everything the decoder recovers has come
    through the power. The transmitter also drops to the invoking user --
    running it as root would quietly undercut the claim that it needs no
    privilege.
    """
    csv_path = out_dir / f"{tag}.rx.csv"

    opts = dict(TX_DEFAULTS)
    opts.update(tx_opts)
    opts.update({k: v for k, v in run_spec.items() if k in TX_FLAGS or k == "message"})

    tier = run_spec.get("tier", rx_opts.get("tier", "rapl"))
    if tier not in RX_RECEIVERS:
        raise SystemExit(f"unknown receiver tier '{tier}' "
                         f"(expected one of {sorted(RX_RECEIVERS)})")
    recv = RX_RECEIVERS[tier]
    ropts = dict(recv["defaults"])
    ropts.update({k: v for k, v in rx_opts.items() if k in recv["flags"]})
    ropts.update({k: v for k, v in run_spec.items() if k in recv["flags"]})

    tx_cmd = [str(BIN / "tx")]
    for key, flag in TX_FLAGS.items():
        tx_cmd += [flag, str(opts[key])]
    if opts.get("message"):
        tx_cmd += ["--message", str(opts["message"])]

    # The receiver is told how long to record, and nothing else about the
    # transmission -- not the symbol period, not the preamble, not the payload.
    bits = tx_bits_per_frame(opts) * int(opts["frames"])
    tx_s = ((float(opts["prewarm_ms"]) + float(opts["lead_ms"]) + float(opts["tail_ms"])) / 1000.0
            + bits * float(opts["symbol_us"]) / 1e6)
    rx_s = RX_LEAD_S + tx_s + RX_MARGIN_S

    rx_cmd = [str(BIN / recv["bin"]), "--duration", f"{rx_s:.3f}",
              "--out", str(csv_path)]
    for key, flag in recv["flags"].items():
        rx_cmd += [flag, str(ropts[key])]

    uid = int(os.environ.get("SUDO_UID", 0))
    gid = int(os.environ.get("SUDO_GID", 0))
    # Only tier 1 needs root. Dropping the others to the invoking user is the
    # point of the ladder, not a detail: a tier-2 receiver run as root would
    # demonstrate nothing about what an unprivileged process can see.
    drop = {"user": uid, "group": gid} if uid and not recv["root"] else {}

    logger.info("running %s: tier-%s %s symbol=%sus %d bits (%.1fs tx, %.1fs rx)",
                tag, tier, opts["victim"], opts["symbol_us"], bits, tx_s, rx_s)
    before = system_state()
    started = time.time()

    rx_proc = subprocess.Popen(rx_cmd, cwd=SRC, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, **drop)
    try:
        time.sleep(RX_LEAD_S)
        tx_kwargs = {"user": uid, "group": gid} if uid else {}
        tx_proc = subprocess.run(tx_cmd, cwd=SRC, capture_output=True, text=True,
                                 **tx_kwargs)
    except BaseException:
        rx_proc.kill()
        rx_proc.wait()
        raise
    rx_out, rx_err = rx_proc.communicate()
    elapsed = time.time() - started

    if tx_proc.returncode != 0:
        logger.error("tx failed (%d):\n%s", tx_proc.returncode, tx_proc.stderr[-2000:])
        raise SystemExit(tx_proc.returncode)
    if rx_proc.returncode != 0:
        logger.error("rx failed (%d):\n%s", rx_proc.returncode, rx_err[-2000:])
        raise SystemExit(rx_proc.returncode)

    try:
        tx_summary = json.loads(tx_proc.stdout)
        rx_summary = json.loads(rx_out)
    except json.JSONDecodeError:
        logger.error("covert run produced no JSON summary:\ntx: %s\nrx: %s",
                     tx_proc.stdout[-1000:], rx_out[-1000:])
        raise SystemExit(1)

    after = system_state()
    # A late chip means the transmitter could not hold its own schedule, so a
    # bit-error rate from this run would describe the transmitter rather than
    # the channel. Loud, because it invalidates the run rather than degrading it.
    if tx_summary["late_chips"]:
        logger.warning("  %d late chips (max %.1f us) -- symbol period too short",
                       tx_summary["late_chips"], tx_summary["max_late_us"])
    if tier == "freq" and rx_summary["value_changes"] == 0:
        # A flat frequency trace means no DVFS response to read: either the
        # part never had to throttle at this load, or the run is Config-A.
        logger.warning("  frequency never changed -- nothing to decode. "
                       "Config-B and enough load to make something limit?")
    if tier == "rapl":
        detail = f"RAPL period {rx_summary['rapl_period_ms']:.3f} ms"
    elif tier == "freq":
        detail = (f"{rx_summary['value_changes']} value changes, "
                  f"{rx_summary['late_polls']} late polls")
    else:
        detail = (f"work {rx_summary['work_mean_tsc']:.0f} TSC, "
                  f"CV {rx_summary['work_cv'] * 100:.1f}%")
    logger.info("  %s rx samples in %.1fs, %s, %d late chips",
                rx_summary["samples_written"], elapsed, detail,
                tx_summary["late_chips"])

    return {
        "tag": tag,
        "repeat": repeat,
        "label": run_spec.get("label", tag),
        "tier": tier,
        "victim": opts["victim"],
        "csv": csv_path.name,
        "elapsed_s": elapsed,
        "tx": tx_summary,
        "rx": rx_summary,
        "tx_argv": tx_cmd,
        "rx_argv": rx_cmd,
        "state_before": before,
        "state_after": after,
    }


# ---------------------------------------------------------------------------
# Battery cross-validation (critique B1.2)
# ---------------------------------------------------------------------------

BATTERY_DEFAULTS = {
    "victim": "ws_l3_x8",
    "threads": 4,
    "monitor_core": 0,
    "victim_core_start": 2,
    "victim_core_stride": 2,
    "max_victim_core": 11,
    "on": 4294967295,
    "off": 0,
    "arm_ms": 4000,
    "settle_ms": 800,
    "rounds": 20,
    "poll_ms": 150,
    "seed": 12345,
}

BATTERY_FLAGS = {
    "victim": "--victim",
    "threads": "--threads",
    "monitor_core": "--monitor-core",
    "victim_core_start": "--victim-core-start",
    "victim_core_stride": "--victim-core-stride",
    "max_victim_core": "--max-victim-core",
    "on": "--on",
    "off": "--off",
    "arm_ms": "--arm-ms",
    "settle_ms": "--settle-ms",
    "rounds": "--rounds",
    "poll_ms": "--poll-ms",
    "seed": "--seed",
}


def run_battery(run_spec, bx_opts, out_dir, tag, repeat=0):
    """One battery-vs-RAPL cross-check run.

    Reads the package MSR (root) and the battery's own current/voltage over the
    same long arms, alternating the operand, so an operand effect can be seen on
    an instrument that shares nothing with RAPL. Only meaningful on battery,
    which the runner allows solely under --allow-battery.
    """
    csv_path = out_dir / f"{tag}.csv"

    opts = dict(BATTERY_DEFAULTS)
    opts.update(bx_opts)
    opts.update({k: v for k, v in run_spec.items() if k in BATTERY_FLAGS})

    cmd = [str(BIN / "battery_xcheck"), "--out", str(csv_path)]
    for key, flag in BATTERY_FLAGS.items():
        cmd += [flag, str(opts[key])]

    logger.info("running %s: battery xcheck victim=%s rounds=%s arm=%sms",
                tag, opts["victim"], opts["rounds"], opts["arm_ms"])
    before = system_state()
    started = time.time()
    proc = subprocess.run(cmd, cwd=SRC, capture_output=True, text=True)
    elapsed = time.time() - started

    if proc.returncode != 0:
        logger.error("battery_xcheck failed (%d):\n%s", proc.returncode,
                     proc.stderr[-2000:])
        raise SystemExit(proc.returncode)
    try:
        summary = json.loads(proc.stdout)
    except json.JSONDecodeError:
        logger.error("battery_xcheck produced no JSON summary:\n%s",
                     proc.stdout[-2000:])
        raise SystemExit(1)

    after = system_state()
    logger.info("  %s arms in %.1fs, battery %s -> %s", summary["arms_written"],
                elapsed, summary["battery_status_start"],
                summary["battery_status_end"])
    if summary["battery_status_start"] != "Discharging":
        logger.warning("  battery was %s, not Discharging -- no discharge to "
                       "compare. Unplug the charger and re-run.",
                       summary["battery_status_start"])

    return {
        "tag": tag,
        "repeat": repeat,
        "label": run_spec.get("label", run_spec.get("victim", tag)),
        "victim": opts["victim"],
        "selectors": [opts["off"], opts["on"]],
        "csv": csv_path.name,
        "elapsed_s": elapsed,
        "battery": summary,
        "battery_argv": cmd,
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
    ap.add_argument("--allow-battery", action="store_true",
                    help="measure on battery. Refused by default: the "
                         "platform lowers its power limits there, so the "
                         "session is not comparable to the rest of the corpus")
    args = ap.parse_args()

    if args.restore_only:
        if os.geteuid() != 0:
            raise SystemExit("must run as root to change turbo state")
        # Config-A leaves no_turbo=1. A run killed before its finally block
        # leaves the machine pinned at base clock indefinitely.
        for name in MEASURING_PROCS:
            if subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0:
                raise SystemExit(f"a {name} is still running; refusing to change "
                                 f"turbo state mid-experiment")
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
    open_output_dir(out_dir)

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
        preflight(allow_battery=args.allow_battery)
        build()
        apply_config(spec.get("config", "A"))
        # Let the frequency change settle before the first measurement.
        time.sleep(2)

        repeats = int(spec.get("repeats", 1))
        kind = spec.get("kind", "driver")
        covert = kind == "covert"
        battery = kind == "battery"
        section = (spec.get("tx", {}) if covert
                   else spec.get("battery", {}) if battery
                   else spec.get("driver", {}))
        base_seed = int(section.get("seed", 12345))
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
                        " ".join(r.get("label", r.get("victim", "run")) for r in runs))

            for i, run_spec in enumerate(runs):
                base = run_spec.get("label") or f"{run_spec.get('victim', 'run')}_{i}"
                tag = f"{base}_r{rep}" if repeats > 1 else base
                # A distinct block-order seed per repeat.
                rs = dict(run_spec)
                rs["seed"] = int(rs.get("seed", base_seed)) + 1000 * rep

                cooldown(cool, spec.get("cooldown_target_c"))
                if covert:
                    result = run_covert(rs, spec.get("tx", {}), spec.get("rx", {}),
                                        out_dir, tag, repeat=rep)
                elif battery:
                    result = run_battery(rs, spec.get("battery", {}), out_dir, tag,
                                         repeat=rep)
                else:
                    result = run_driver(rs, spec.get("driver", {}), out_dir, tag,
                                        repeat=rep)
                manifest["runs"].append(result)
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
    entry = {"covert": "analysis.covert",
             "battery": "analysis.battery"}.get(spec.get("kind"), "analysis.report")
    print(f"\nNext: ./venv/bin/python3 -m {entry} {out_dir}")


if __name__ == "__main__":
    main()
