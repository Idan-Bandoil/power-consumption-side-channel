# How this run's summary.txt is built. Run from the repo root with the run
# directory in $r; see analysis/regen-summary.sh, which is what executes it.
#
# Four sections, because a uniform report+aggregate pass would lose the last
# two and they are the point of the session:
#
#   1. report    -- the gates. Three of them fail here and all three are
#                   informative rather than fatal; the NOTES section says why.
#   2. aggregate -- between-run spread over three repeats, the reporting unit.
#   3. hwfit x4  -- one fit per depth. This is the experiment: the intercept
#                   a in dP = a + b*HW is the zero-operand step, and whether
#                   it is constant in watts, constant in pJ/byte, or scales
#                   with depth is exactly what critique item A2 turns on.
#                   --labels keeps each depth's four points in their own fit
#                   and keeps the anchor and the controls out of all of them.
#   4. NOTES     -- what the numbers mean and which one is contaminated.

./venv/bin/python3 -m analysis.report "$r"

echo
echo "### between-run aggregate over three repeats ###"
echo
./venv/bin/python3 -m analysis.aggregate "$r"

for d in l1 l2 l3 dram; do
	echo
	echo "### hwfit, depth $d ###"
	echo
	./venv/bin/python3 -m analysis.hwfit "$r" --labels "${d}_*" --no-figure
done

cat <<'NOTES'

### NOTES ###

WHAT THIS SESSION ANSWERS (critique A2, E1, C4)

A2. The zero-operand step is neither constant in watts nor constant in
pJ/byte. It scales with depth, which was the third of the three readings and
the one no measurement had yet distinguished. Intercepts of dP = a + b*HW,
per depth, from the per-repeat fits above:

    depth   a (mW)  per-repeat                 b (mW/bit)   b*32 (mW)
    L1        -60   -42.8  -68.5  -68.2          7.23 +- 2.97     231
    L2       +231  +238.9 +277.2 +176.4         25.99 +- 4.10     832
    L3       +194  +182.4 +176.4 +221.7         48.96 +- 3.24    1567
    DRAM      +52   +37.7  +19.9  +98.5         22.52 +- 1.20     721

So the +349 mW step measured on ws_l3_x8 alone is not a platform constant. At
L1 it is absent and slightly negative; at L3 it is +194 mW, about 55% of the
figure section 8 quotes; at DRAM it is +52 mW and within its own spread of
zero. The step is a property of the transport path, not of the baseline.

CONSEQUENCE FOR SECTION 4. The critique's worst case does not happen. Removing
each depth's own intercept leaves the bit-attributable cost b*32 normalised to
that depth's throughput:

    depth   GB/s   dpJ/B as measured   dpJ/B less its own intercept
    L1     723.3          0.225                   0.319
    L2     339.1          3.150                   2.454
    L3     142.8         12.427                  10.971
    DRAM    41.0         18.514                  17.598

Both columns are normalised by the same throughput, that of each depth's own
HW-32 row, so the correction is the only difference between them.

The ladder is 55x L1 to DRAM, monotone, with L3 and DRAM still well separated
and L1 still positive. The feared collapse to ~5.5x with a negative L1 row
came from applying a constant 0.349 W correction at every depth, and that
correction is now measured and is not constant. Section 4 stands, with the
ratio revised from 68x to 55x.

E1. The grid is the interaction term: b rises from 7.2 mW/bit at L1 to 49.0 at
L3 and falls to 22.5 at DRAM, so the weight coefficient is not separable from
depth and a combined model needs the product term. Absolute watts peak at L3,
as the published depth ladder already found, and both the slope and the
intercept peak there too.

CROSS-SESSION ANCHOR. anchor_hw16 reads +1.160 W (SD 0.095) against +1.133,
+1.215 and +1.228 W in the three earlier sessions -- inside the spread, so the
session is comparable to the corpus. l3_hw32 reads +1.775 W against +1.904
(weight sweep) and +1.841 (polarity), also inside it. The L3 slope of +48.96
mW/bit reproduces the published pooled +50.75 within its error bar.

THE THREE GATE FAILURES

1. aa_l3_2buf, the two-buffer A/A (C4). FAILS in all three repeats, at
   -0.198, -0.354 and -0.128 W, all the same sign, detector 0.78-0.98. The two
   buffers hold bit-identical data: ws_fill casts the selector to uint32, so
   0x00000000_5A5A5A5A and 0x00000001_5A5A5A5A both fill with 0x5A5A5A5A,
   while ws_get keys its cache on all 64 bits and so allocates two distinct
   mappings. The victim loop reads only the returned pointer -- the high half
   reaches nothing else -- so the only difference between the conditions is
   which mmap is being read. Throughput differs with it (-1.08%, -0.27%,
   -0.60%, second buffer always slower), so part of this is a placement
   effect acting through achieved bandwidth. The single-buffer aa_l3 in the
   same session is clean at -0.003 W and 0.02% throughput spread, so this is
   not the harness.

   This is NOT a blanket correction to every A/B result, and the corpus
   already bounds it: phase1_polarity ran the same victim forward and reverse
   and got (fwd+rev)/2 = -7 mW, where a fixed slot-1 penalty of -227 mW would
   have shown up in full. So the placement term is large per allocation and
   near zero in the mean -- a variance source, not a bias. It is the best
   candidate yet for the ~100 mW between-run SD this project has been
   attributing to "the instrument" without naming a mechanism. It needs its
   own session before the chapter claims more than that.

2. work_balance, 19 of 66 runs. Systematic at DRAM: +2.08% mean over 12 runs,
   11 of them over the 1% gate, the heavier operand always moving more bytes.
   Not noise, and it lands on the row with the highest pJ/byte. The bound the
   report prints is the worst case in which all package power scales with
   traffic; the true share is smaller, but the DRAM row should be quoted with
   this stated rather than silently. L1, L2 and L3 are inside the gate apart
   from the runs named below. frequency_balance passes everywhere at 0.000%,
   so Config-A held throughout.

3. sham_l3_hw16, the effect-scale noise floor (C4's second half). Reads
   -0.174 W with between-run SD 0.094 and all three repeats negative, on an
   operand pair matched in both Hamming weight (16) and Hamming distance. It
   is deliberately not declared a control, so it does not fail as an A/A --
   its work_balance does, at +1.19%, +1.44% and -0.95% with the sign flipping.
   Read together with aa_l3_2buf: a matched-operand contrast across two
   mappings sits at roughly -0.2 W with a ~0.09 W spread, which is the noise
   floor for large effects the chapter did not previously have. Every claim
   in the grid above at HW 8 and 32 clears it; the HW 1 and 2 rows at L1 and
   DRAM do not, and should not be quoted individually.

ONE CONTAMINATED RUN

l2_hw08_r2 stalled for 8595 s against 47.1 s for every other run in the
session -- a suspend, landing in its cond 0 blocks. Its throughput reads
0.8 GB/s against 364.3 (work_balance 199.1%) and its cond-0 drift span is
1.94 W against 0.92 W for cond 1. The power difference it reports, +0.481 W,
is in line with its siblings (+0.319, +0.439), but the run is contaminated and
its throughput figure is meaningless.

It is left in place so the gate fails visibly rather than being quietly
deleted, but it should not be quoted. Excluding it, from the per-repeat fits
printed above:

    l2_hw08   +0.379 W over repeats 0 and 1, against +0.413 with all three
    L2 slope  +23.68 mW/bit (repeats 0, 1), against +25.99 with all three
    L2 a      +258 mW       (repeats 0, 1), against +231 with all three

Neither figure moves the A2 conclusion. The L2 aggregate throughput hwfit
prints, 315 GB/s, is dragged down by this run; the other three L2 rows average
348 GB/s, and the pJ/byte table above sidesteps it by normalising on the
uncontaminated l2_hw32 row. Re-running this one cell costs about 90 seconds
and would restore the depth to n=3.

WHAT THIS SESSION DOES NOT CARRY

The power source was not recorded. The run started at 10:34 on commit a24993e
and the ac_online field landed at 10:50 in 1c39203, sixteen minutes into the
session, so the on_mains gate passes vacuously here and this is the last
session that cannot answer the question. PL1 held at 200 W and PL2 at 80 W
across all 132 snapshots, so the power_state gate is genuinely clean and
thermald did not move anything.
NOTES
