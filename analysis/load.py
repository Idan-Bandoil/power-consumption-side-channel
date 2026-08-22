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
    block: np.ndarray
    cond: np.ndarray
    ticks: np.ndarray
    dtsc: np.ndarray
    daperf: np.ndarray
    dmperf: np.ndarray
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
    def freq_khz(self):
        out = np.zeros(len(self.dmperf), dtype=float)
        ok = self.dmperf > 0
        out[ok] = self.max_freq_khz * (self.daperf[ok] / self.dmperf[ok])
        return out

    @property
    def is_aa_control(self):
        """True when every condition carries the same selector, i.e. the run
        is an A/A negative control and must come out at chance."""
        return len(set(self.selectors)) == 1

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
        block=raw[:, 0].astype(np.int64),
        cond=raw[:, 1].astype(np.int64),
        ticks=raw[:, 2].astype(np.float64),
        dtsc=raw[:, 3].astype(np.float64),
        daperf=raw[:, 4].astype(np.float64),
        dmperf=raw[:, 5].astype(np.float64),
        meta=entry,
    )


def load_results(results_dir):
    """Returns (manifest, [Run]) for a directory written by experiment_runner."""
    results_dir = Path(results_dir)
    manifest = json.loads((results_dir / "manifest.json").read_text())
    runs = [load_run(results_dir / e["csv"], e) for e in manifest["runs"]]
    return manifest, runs
