#ifndef VICTIM_UTILS_H
#define VICTIM_UTILS_H

#include <stdio.h>

/*
 * Victims are looked up by name from a single table in victim-utils.c, so
 * adding one is a single edit there. Every victim runs a tight inline-asm
 * loop and re-reads ctl->selector between bursts, which lets the driver
 * change the tested operand live instead of respawning threads.
 */
int (*get_victim(const char *name))(void *);

void list_victims(FILE *out);

/*
 * Fill `buf` (bytes long) exactly as a victim in the given mode would for `sel`,
 * and report the realised zero-word fraction, mean Hamming weight per word, and
 * mean per-word Hamming distance. For tests only: the sparsity sweeps' whole
 * x-axis is the requested density, so the fill is verified against ground truth
 * before a session trusts it. Modes match the ws_cache flags (sparse,
 * sparse_block, iid, ab_mask).
 */
#include <stddef.h>
#include <stdint.h>
void ws_fill_probe(unsigned char *buf, size_t bytes, uint64_t sel,
		   int sparse, int sparse_block, int iid, int ab_mask,
		   double *zero_frac, double *mean_hw, double *mean_hd);

#endif
