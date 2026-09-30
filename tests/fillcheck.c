/*
 * Verify the sparsity/iid fills do what the selector says (critique E2).
 *
 * The whole x-axis of the E2 sweeps is the requested density. A PRNG or masking
 * bug would fill the buffer to the *wrong* density smoothly and monotonically,
 * and every validity gate -- which check the measurement, not the operand --
 * would pass while the curve meant nothing. So before a 45-minute session trusts
 * the fill, this asserts the realised zero fraction, mean Hamming weight, and
 * mean Hamming distance match what each mode promises. No root, no MSR.
 *
 *   cd src && make fillcheck && ./bin/fillcheck
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "victim-utils.h"

#define BYTES (4u * 1024 * 1024)	/* 1M words, as ws_l3_x8 */

static int fails;

/*
 * Mean bits toggled between consecutive `chunk`-byte transfers in memory order.
 * This -- not the word-to-word figure ws_fill_probe returns -- is the switching
 * the datapath sees: loads move 32 bytes at a time and line fills 64, and Phase
 * 1's ab/ab64 pair showed that toggling between consecutive loads and lines is
 * what costs power. Word-to-word alternation *inside* one load is a static
 * spatial pattern on a 256-bit-wide path, not a temporal toggle.
 */
static double chunk_hd(const unsigned char *buf, size_t bytes, size_t chunk)
{
	const uint64_t *q = (const uint64_t *)buf;
	size_t words = chunk / sizeof(*q), n = bytes / chunk;
	uint64_t total = 0;
	for (size_t c = 1; c < n; c++)
		for (size_t w = 0; w < words; w++)
			total += (uint64_t)__builtin_popcountll(q[c * words + w] ^ q[(c - 1) * words + w]);
	return n > 1 ? (double)total / (double)(n - 1) : 0.0;
}

static void check(const char *what, double got, double want, double tol)
{
	int ok = fabs(got - want) <= tol;
	printf("    %-28s got %8.4f  want %8.4f  %s\n",
	       what, got, want, ok ? "OK" : "FAIL");
	if (!ok)
		fails++;
}

int main(void)
{
	unsigned char *buf = malloc(BYTES);
	if (!buf) {
		fprintf(stderr, "oom\n");
		return 1;
	}

	const uint32_t dens[] = { 128, 256, 512, 768, 1024 };

	/*
	 * Sparse scattered (block 1) and blocked (block 16). Both fill nonzero
	 * words with 0xFFFFFFFF (weight 32), so at density d = per1024/1024 the
	 * zero fraction must be 1-d and the mean weight 32d, identical between the
	 * two. What must differ is the switching: scattered toggles far more often
	 * than blocked at the same density.
	 */
	for (int mode = 0; mode < 2; mode++) {
		int block = mode ? 16 : 1;
		printf("  sparse %s (block=%d):\n", mode ? "blocked" : "scattered", block);
		for (size_t k = 0; k < sizeof(dens) / sizeof(dens[0]); k++) {
			uint64_t sel = dens[k];		/* pattern 0 -> 0xFFFFFFFF */
			double d = (double)dens[k] / 1024.0;
			double zf, hw, hd;
			ws_fill_probe(buf, BYTES, sel, 1, block, 0, 0, &zf, &hw, &hd);
			char lbl[64];
			snprintf(lbl, sizeof(lbl), "d=%.3f zero_frac", d);
			check(lbl, zf, 1.0 - d, 0.01);
			snprintf(lbl, sizeof(lbl), "d=%.3f mean_hw", d);
			check(lbl, hw, 32.0 * d, 0.30);
		}
	}

	/*
	 * Where the switching actually is. An earlier version of this check
	 * compared word-to-word Hamming distance and concluded the blocked fill was
	 * the quiet one. That is the wrong granularity, and it got the E2 design's
	 * roles backwards: the scattered fill's per-word spread has a period of
	 * 1, 2, 4 or 8 words at every density swept, so every 32-byte load (8
	 * words) is *identical* and nothing toggles between consecutive loads or
	 * lines -- its 0/P alternation is spatial, inside one 256-bit transfer.
	 * The blocked fill alternates whole 64-byte lines between all-zero and
	 * all-ones, so it is the one that toggles. Asserted here so the
	 * interpretation rests on the fill, not on arithmetic about it.
	 */
	printf("  temporal switching (bits toggled between consecutive transfers):\n");
	printf("    %-7s %-10s %10s %10s %10s\n", "d", "fill", "word", "32B load", "64B line");
	for (size_t k = 0; k < sizeof(dens) / sizeof(dens[0]); k++) {
		for (int mode = 0; mode < 2; mode++) {
			int block = mode ? 16 : 1;
			double zf, hw, hd_word;
			ws_fill_probe(buf, BYTES, dens[k], 1, block, 0, 0, &zf, &hw, &hd_word);
			double hd_load = chunk_hd(buf, BYTES, 32);
			double hd_line = chunk_hd(buf, BYTES, 64);
			double d = (double)dens[k] / 1024.0;
			printf("    %-7.3f %-10s %10.2f %10.2f %10.2f\n", d,
			       mode ? "blocked" : "scattered", hd_word, hd_load, hd_line);
			if (!mode && (hd_load > 0.01 || hd_line > 0.01)) {
				printf("    FAIL: scattered should not toggle between transfers\n");
				fails++;
			}
			if (mode && d > 0.0 && d < 1.0 && hd_line < 1.0) {
				printf("    FAIL: blocked should toggle between lines\n");
				fails++;
			}
		}
	}

	/*
	 * I.i.d.: each bit set with probability p = per1024/1024. Mean weight is
	 * 32p as for the sparse fill, but the zero-word fraction is (1-p)^32 -- not
	 * 1-p -- because a word is zero only if all 32 bits missed. Getting this
	 * expectation right is the point: iid is a different distribution at the
	 * same mean density.
	 */
	printf("  iid:\n");
	const uint32_t idens[] = { 256, 512, 768 };
	for (size_t k = 0; k < sizeof(idens) / sizeof(idens[0]); k++) {
		double p = (double)idens[k] / 1024.0;
		double zf, hw, hd;
		ws_fill_probe(buf, BYTES, idens[k], 0, 1, 1, 0, &zf, &hw, &hd);
		char lbl[64];
		snprintf(lbl, sizeof(lbl), "p=%.3f mean_hw", p);
		check(lbl, hw, 32.0 * p, 0.10);
		snprintf(lbl, sizeof(lbl), "p=%.3f zero_frac", p);
		check(lbl, zf, pow(1.0 - p, 32.0), 0.01);
		snprintf(lbl, sizeof(lbl), "p=%.3f mean_hd", p);
		check(lbl, hd, 32.0 * 2.0 * p * (1.0 - p), 0.30);
	}

	free(buf);
	printf(fails ? "\nfillcheck: %d FAILURES\n" : "\nfillcheck: all OK\n", fails);
	return fails ? 1 : 0;
}
