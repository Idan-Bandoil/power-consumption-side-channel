# Thesis drafts

Chapters map 1:1 onto the phases of the working plan
(`~/.claude/plans/resilient-squishing-spindle.md`), and each is drafted as its phase
completes rather than at the end.

| File | Phase | State |
|---|---|---|
| `phase0-measurement.md` | 0 — measurement infrastructure and validity | first full draft |
| `phase1-leakage.md` | 1 — leakage characterisation: operand movement | first full draft |
| *(pending)* | 2 — covert channel | not started |
| *(pending)* | 3 — ML inference leakage | not started |
| *(pending)* | 4 — mitigations | not started |

Drafts are Markdown so the content can be revised without fighting LaTeX; conversion to
the submission template is mechanical and deferred. Every number in a draft must cite the
run directory under `results/` it came from, and every run directory keeps its
`manifest.json`, its selector files, its figures and a generated `summary.txt` in git even
though the raw CSVs are not tracked.
