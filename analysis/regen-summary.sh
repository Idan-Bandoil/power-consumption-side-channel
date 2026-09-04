#!/usr/bin/env bash
#
# Rebuild a run's summary.txt from its own recorded commands.
#
#   analysis/regen-summary.sh results/<run_id> [...]
#
# Raw CSVs are gitignored; summary.txt is what survives them, so it has to be
# regenerable and it has to be regenerated the same way it was made. It was
# not: the summaries are heterogeneous -- some carry analysis.hwfit output, the
# instruction table carries two differently-flagged aggregate sections, the
# tier-2 sweep carries a second decode against a different watched CPU -- and a
# uniform "report + aggregate" pass over all of them silently deletes that
# content. It did, once, and was caught only because the diff came out
# net-negative.
#
# So each run directory carries a summary.cmd saying how its summary is built,
# and this runs it. A directory without one is skipped with a message rather
# than guessed at: guessing is the failure mode this exists to prevent.
#
# Recording the command is necessary and not sufficient: a summary.cmd can
# itself be wrong, and the first one written for the tier-2 smoke was -- it
# omitted the second decode against the other watched CPU and cut 61 of that
# summary's 130 lines. So this also refuses to shrink a summary by more than
# SHRINK_PCT without --force. Output legitimately shrinks sometimes (a gate
# stops firing), which is why it is a threshold and an override rather than a
# prohibition.
set -u

SHRINK_PCT=15
force=0
if [ "${1:-}" = "--force" ]; then
	force=1
	shift
fi

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo" || exit 1

status=0
for r in "$@"; do
	r="${r%/}"
	if [ ! -f "$r/manifest.json" ]; then
		echo "skip $r: not a run directory" >&2
		status=1
		continue
	fi
	if [ ! -f "$r/summary.cmd" ]; then
		echo "skip $r: no summary.cmd -- write one recording the exact" >&2
		echo "     commands that built its summary.txt, then re-run" >&2
		status=1
		continue
	fi

	tmp="$(mktemp)" || exit 1
	# shellcheck disable=SC1090
	if ! r="$r" bash "$r/summary.cmd" > "$tmp" 2>&1; then
		echo "warn $r: summary.cmd exited non-zero (gates can do that);" >&2
		echo "     output kept, check it before committing" >&2
	fi
	if [ ! -s "$tmp" ]; then
		echo "skip $r: summary.cmd produced nothing, leaving summary.txt" >&2
		rm -f "$tmp"
		status=1
		continue
	fi

	old=$(wc -l < "$r/summary.txt" 2>/dev/null || echo 0)
	new=$(wc -l < "$tmp")

	if [ "$force" -eq 0 ] && [ "$old" -gt 0 ] \
	   && [ $((new * 100)) -lt $((old * (100 - SHRINK_PCT))) ]; then
		echo "REFUSING $r: $old -> $new lines, a $(( (old - new) * 100 / old ))% cut" >&2
		echo "     summary.cmd is probably missing a section the committed" >&2
		echo "     summary has. Compare against: git show HEAD:$r/summary.txt" >&2
		echo "     Kept the new output at $tmp. Re-run with --force if the" >&2
		echo "     shrink is intended." >&2
		status=1
		continue
	fi

	mv "$tmp" "$r/summary.txt"
	echo "$r: $old -> $new lines"
done
exit "$status"
