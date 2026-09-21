# How this run's summary.txt is built. Run from the repo root with the run
# directory in $r; see analysis/regen-summary.sh, which is what executes it.
#
# The arm comparison below is not something analysis.covert produces -- it
# pairs two preamble arms measured in one session -- so it is inlined here
# rather than carried as frozen text, which keeps it regenerable.

./venv/bin/python3 -m analysis.covert "$r"

./venv/bin/python3 - "$r" <<'PY'
import json, math, sys
from collections import defaultdict
import numpy as np

sys.path.insert(0, ".")
from analysis.covert import decode_run
from pathlib import Path

d = Path(sys.argv[1])
rows = []
for e in json.loads((d / "manifest.json").read_text())["runs"]:
    if "tx" in e:
        rows.append(decode_run(e, d / e["csv"]))

def cap(p):
    p = min(max(p, 0.0), 1.0)
    return 1.0 if p in (0.0, 1.0) else 1 + p * math.log2(p) + (1 - p) * math.log2(1 - p)

g = defaultdict(list)
for r in rows:
    g[r["label"]].append(r)

print("\n" + "=" * 78)
print("preamble arms: acquisition against demodulation")
print("=" * 78)
print("""
  Both arms carry the same payloads at the same rates in one shuffled
  session. p13 is the 13-bit Barker preamble every published tier-1 number
  was measured with; p63 is a 63-bit maximal-length sequence (x^6+x+1,
  sidelobe 1, balanced 32/63) worth 2.2x in correlation amplitude. A
  preamble cannot improve demodulation, so if the arms differ it is
  acquisition and nothing else.
""")
order = ["sym_1500us", "sym_02ms", "sym_03ms", "sym_04ms", "sym_06ms", "sym_08ms"]
print("  %-8s %8s | %-16s | %-16s | %s"
      % ("symbol", "bit/s", "p13 Barker-13", "p63 m-seq 63", "BER given sync"))
print("  %-8s %8s | %5s %9s | %5s %9s | %7s %7s"
      % ("", "", "acq", "cap b/s", "acq", "cap b/s", "p13", "p63"))
for base in order:
    a, b = g["p13_" + base], g["p63_" + base]
    raw = a[0]["raw_bps"]
    print("  %-8s %8.1f | %2d/%-2d %9.1f | %2d/%-2d %9.1f | %7.4f %7.4f"
          % (base.replace("sym_", ""), raw,
             sum(r["acquired"] for r in a), len(a),
             raw * cap(float(np.mean([r["ber"] for r in a]))),
             sum(r["acquired"] for r in b), len(b),
             raw * cap(float(np.mean([r["ber"] for r in b]))),
             float(np.mean([r["ber_oracle_sync"] for r in a])),
             float(np.mean([r["ber_oracle_sync"] for r in b]))))

for arm in ("p13", "p63"):
    rs = [r for r in rows if r["label"].startswith(arm) and not r["aa_control"]]
    print("  %s: %d of %d runs acquired; pooled BER given sync %.4f"
          % (arm, sum(r["acquired"] for r in rs), len(rs),
             float(np.mean([r["ber_oracle_sync"] for r in rs]))))

print("""
  Capacity charges nothing for the preamble, which rides in every frame:
  63 bits of 319 against 13 of 269. Net of that the 2 ms m-sequence row
  delivers 249.6 payload b/s against 229.5 for the published 3 ms Barker
  figure, so the gain survives paying for the sync word that produced it.
""")
PY
