#ifndef _VICTIM_POOL_H
#define _VICTIM_POOL_H

#include "util.h"

/*
 * Spawning and reaping a set of victim threads, shared by the measurement
 * driver and the covert-channel transmitter.
 *
 * Victims are cloned with CLONE_VM, so they share the spawner's address space
 * and see writes to ctl->selector immediately. That is what makes both
 * condition interleaving and covert modulation cost a pointer swap rather
 * than a thread teardown. The details worth not reimplementing are here: each
 * victim gets its own args struct (a shared one, mutated between clones, was
 * a real bug), and victim_pin() arms PR_SET_PDEATHSIG so an orphaned victim
 * cannot survive to spin at 100% on a pinned core and poison later runs.
 */
struct victim_pool_t {
	struct ctl_t *ctl;
	struct victim_args_t *vargs;
	int *tids;
	char *stacks;
	size_t stack_bytes;
	int n;
};

/*
 * Places victim i on core_start + i * core_stride and exits if that would
 * exceed max_core. `ctl` is borrowed, not owned; the caller sets ctl->run = 1
 * before spawning.
 */
void victims_spawn(struct victim_pool_t *p, struct ctl_t *ctl,
		   int (*fn)(void *), int n,
		   int core_start, int core_stride, int max_core);

/* Clears ctl->run, waits for the victims to notice, and kills any stragglers. */
void victims_stop(struct victim_pool_t *p);

/* Total bursts completed across the pool -- the basis of the throughput figure. */
uint64_t victims_bursts(const struct victim_pool_t *p);

/*
 * Fewest and most distinct ctl->epoch values any one victim observed. The
 * spawner bumps epoch on every selector write, so `lo` against the number of
 * writes is the check that the modulation reached the victims and was not
 * merely scheduled -- the worst observer is the one that matters, since one
 * victim missing a chip is enough to blunt the edge.
 */
void victims_epochs(const struct victim_pool_t *p, uint64_t *lo, uint64_t *hi);

#endif
