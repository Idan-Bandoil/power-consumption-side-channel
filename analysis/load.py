"""Load a results directory into numpy arrays with derived power/frequency."""
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class Run:
    label: str
    victim: str
    selectors: list
    energy_unit_j: float
    tsc_hz: float
    max_freq_khz: int
    sample_mode: str
    rapl_period_ms: float
    bytes_per_s: float
    bytes_per_s_by_cond: list
    freq_khz_by_cond: list
    freq_core: int
    block: np.ndarray
    cond: np.ndarray
    ticks: np.ndarray
    dtsc: np.ndarray
    daperf: np.ndarray
    dmperf: np.ndarray
    # Sub-domain energy per interval, in the same RAPL units as `ticks`. Empty
    # for runs written before the core/uncore split existed; has_domains says
    # which. core_ticks is PP0 (cores + caches on this client part); pp1_ticks
    # is the graphics domain, which is idle here.
    core_ticks: np.ndarray = None
    pp1_ticks: np.ndarray = None
    has_domains: bool = False
    meta: dict = field(default_factory=dict)

    @property
    def dt_s(self):
        return self.dtsc / self.tsc_hz

    @property
    def power_w(self):
        """Energy per interval divided by the *measured* interval.

        The old pipeline assumed a flat 1 ms and multiplied joules by 1000.
        Edge-triggered sampling gives the real interval per sample, so no
        assumption is needed."""
        return (self.ticks * self.energy_unit_j) / self.dt_s

    @property
    def core_power_w(self):
        """Package sub-domain PP0: the cores and their caches."""
        return (self.core_ticks * self.energy_unit_j) / self.dt_s

    @property
    def pp1_power_w(self):
        """Package sub-domain PP1: client graphics (idle on this platform)."""
        return (self.pp1_ticks * self.energy_unit_j) / self.dt_s

    @property
    def uncore_power_w(self):
        """Package minus core: the rail that meters everything outside PP0.

        On this consumer part it is small and nearly constant, so an operand
        effect that appears in the package but not here is localised to the
        core+cache domain. Not a pure ring/LLC/IMC figure -- PP1 is folded in --
        but PP1 is idle, so pkg-core is uncore to within the graphics floor."""
        return self.power_w - self.core_power_w

    @property
    def freq_khz(self):
        out = np.zeros(len(self.dmperf), dtype=float)
        ok = self.dmperf > 0
        out[ok] = self.max_freq_khz * (self.daperf[ok] / self.dmperf[ok])
        return out

    @property
    def is_aa_control(self):
        """True when the run's expected difference is zero, so it must come out
        at chance.

        Usually that is visible in the selectors: the same value in both
        conditions is an A/A by construction. It cannot always be read off
        them, though. The two-buffer A/A holds two *different* 64-bit selectors
        whose low halves match, which gives two distinct buffers with identical
        contents -- a control for buffer address, and the only kind of A/A that
        can see that confound at all. A spec declares that case with
        `"control": "aa"`."""
        return (len(set(self.selectors)) == 1
                or self.meta.get("control") == "aa")

    @property
    def declared_control(self):
        """True when the null expectation came from the spec, not the selectors."""
        return (self.meta.get("control") == "aa"
                and len(set(self.selectors)) != 1)

    @property
    def zero_tick_fraction(self):
        """Share of samples where the RAPL counter had not advanced.

        Non-zero means the sampler is aliasing against the update interval,
        which is what inflated variance in the pre-2026 datasets."""
        return float(np.mean(self.ticks == 0))

    def condition_labels(self):
        return {c: self.selectors[c] for c in sorted(set(self.cond.tolist()))}

    def mask(self, cond):
        return self.cond == cond


def load_run(csv_path, entry):
    d = entry["driver"]
    raw = np.loadtxt(csv_path, delimiter=",", skiprows=1, dtype=np.int64, ndmin=2)
    if raw.size == 0:
        raise ValueError(f"{csv_path} contains no samples")
    # Runs written before the core/uncore split have six columns; newer ones
    # append core_ticks and pp1_ticks. Detect by width so both parse.
    has_domains = raw.shape[1] >= 8
    core_ticks = raw[:, 6].astype(np.float64) if has_domains else None
    pp1_ticks = raw[:, 7].astype(np.float64) if has_domains else None
    return Run(
        label=entry.get("label", entry["victim"]),
        victim=entry["victim"],
        selectors=list(entry["selectors"]),
        energy_unit_j=d["energy_unit_j"],
        tsc_hz=d["tsc_hz"],
        max_freq_khz=d["max_frequency_khz"],
        sample_mode=d["sample_mode"],
        rapl_period_ms=d.get("rapl_period_ms", float("nan")),
        bytes_per_s=d.get("victim_bytes_per_s", float("nan")),
        # Absent from every run written before the per-condition counters
        # existed; the gates that read them skip rather than fail there.
        bytes_per_s_by_cond=list(d.get("victim_bytes_per_s_by_cond", [])),
        freq_khz_by_cond=list(d.get("victim_freq_khz_by_cond", [])),
        freq_core=d.get("victim_freq_core", -1),
        block=raw[:, 0].astype(np.int64),
        cond=raw[:, 1].astype(np.int64),
        ticks=raw[:, 2].astype(np.float64),
        dtsc=raw[:, 3].astype(np.float64),
        daperf=raw[:, 4].astype(np.float64),
        dmperf=raw[:, 5].astype(np.float64),
        core_ticks=core_ticks,
        pp1_ticks=pp1_ticks,
        has_domains=has_domains,
        meta=entry,
    )


def load_results(results_dir):
    """Returns (manifest, [Run]) for a directory written by experiment_runner."""
    results_dir = Path(results_dir)
    manifest = json.loads((results_dir / "manifest.json").read_text())
    runs = [load_run(results_dir / e["csv"], e) for e in manifest["runs"]]
    return manifest, runs
