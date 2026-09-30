# Thesis drafts

Chapters map 1:1 onto the phases of the working plan
(`~/.claude/plans/resilient-squishing-spindle.md`), and each is drafted as its phase
completes rather than at the end.

| File | Phase | State |
|---|---|---|
| `phase0-measurement.md` | 0 — measurement infrastructure and validity | first full draft |
| `phase1-leakage.md` | 1 — leakage characterisation: operand movement | first full draft |
| `phase2-covert.md` | 2 — covert channel | **partial** — tier 1 only; §9 lists the gaps |
| `phase3-inference.md` | 3 — ML inference leakage | **methodology skeleton** — design/method/predictions written; results PENDING (victims + experiments + analysis built, not yet run) |
| *(pending)* | 4 — mitigations | not started |

A draft marked *partial* is written for the work done so far and carries a section saying
what it does not yet cover, so the gaps are visible in the chapter rather than only in the
plan. It is not a stub: what is there is finished prose over measured results.

A draft marked *methodology skeleton* goes one step earlier: its design, method and
predictions are finished prose, but its results sections are labelled placeholders because
the experiments have not been run yet. Every such table names the run directory it will
cite, and every predicted number is labelled as a prediction — nothing is stated as a
finding. `phase3-inference.md` is in this state, and its §8 lists exactly what remains.

Drafts are Markdown so the content can be revised without fighting LaTeX; conversion to
the submission template is mechanical and deferred. Every number in a draft must cite the
run directory under `results/` it came from, and every run directory keeps its
`manifest.json`, its selector files, its figures and a generated `summary.txt` in git even
though the raw CSVs are not tracked.
