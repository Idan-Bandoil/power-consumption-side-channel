#ifndef _SAMPLER_H
#define _SAMPLER_H

#include <stdint.h>

#include "freq-utils.h"
#include "util.h"

/*
 * Edge-triggered RAPL sampling, factored out of the driver so that the
 * covert-channel receiver measures with the same instrument the Phase 0/1
 * results were validated on rather than a second copy of it.
 *
 * The sampler idles for most of the estimated RAPL update period, then polls
 * tightly until MSR_PKG_ENERGY_STATUS changes. That records the exact energy
 * increment and the exact interval it covers, instead of a fixed window that
 * aliases against the update interval (which is what put 9.2% zero-energy
 * samples in the pre-rework datasets).
 */

struct rapl_edge_t {
	uint32_t ticks;		/* raw RAPL energy units since the previous edge */
	uint64_t tsc;		/* absolute TSC at which this edge was observed */
	uint64_t dtsc;		/* TSC elapsed since the previous edge */
	uint64_t daperf;
	uint64_t dmperf;
};

struct rapl_sampler_t {
	int fd;
	int core;
	int mode;		/* SAMPLE_EDGE or SAMPLE_FIXED */
	uint64_t fixed_cycles;	/* window length in SAMPLE_FIXED mode */

	double energy_unit_j;
	double tsc_hz;

	uint32_t prev_e;
	uint64_t prev_tsc;
	struct freq_sample_t prev_f;

	uint64_t period_est;	/* EWMA of the RAPL update period, in TSC cycles */
	uint64_t edges_seen;
	uint64_t overshoots;	/* edges observed more than 1.5 periods late */
};

/*
 * The TSC is invariant, so one calibration against CLOCK_MONOTONIC converts
 * every recorded dtsc into seconds. Without it energy per edge cannot be
 * turned into watts, and the transmitter cannot size a symbol in real time.
 * Costs 50 ms; call it once.
 */
double measure_tsc_hz(void);

/* Spin until the TSC reaches `deadline`. */
void busy_wait_until(uint64_t deadline);

/*
 * Opens the MSR for `core` (exits on failure -- needs root and the msr
 * module), reads the energy unit, calibrates the TSC, and latches the first
 * energy and frequency reading. Also calls set_frequency_units(), which the
 * APERF/MPERF scaling in every later sample depends on.
 *
 * Does not pin: the caller decides which thread does the sampling.
 */
void rapl_sampler_init(struct rapl_sampler_t *s, int core, int mode,
		       uint64_t fixed_cycles);

/* Blocks until the next RAPL counter edge (or fixed window) and returns it. */
struct rapl_edge_t rapl_sampler_next(struct rapl_sampler_t *s);

void rapl_sampler_close(struct rapl_sampler_t *s);

#endif
