/* _GNU_SOURCE comes from CFLAGS. */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <immintrin.h>

#include "sampler.h"

#define MSR_RAPL_POWER_UNIT   0x606
#define MSR_PKG_ENERGY_STATUS 0x611

/*
 * Fraction of the estimated RAPL update period spent idling before we begin
 * tight-polling for the next counter edge. Staying strictly below 1 means an
 * edge can never be slept through; the remaining poll window bounds how late
 * we observe it.
 */
#define GUARD_NUM 7
#define GUARD_DEN 8

/* Edges used to learn the update period before the guard is engaged. */
#define WARMUP_EDGES 16

static inline void busy_wait(uint64_t cycles)
{
	uint64_t start = _rdtsc();
	while ((_rdtsc() - start) < cycles)
		_mm_pause();
}

void busy_wait_until(uint64_t deadline)
{
	while ((int64_t)(deadline - _rdtsc()) > 0)
		_mm_pause();
}

double measure_tsc_hz(void)
{
	struct timespec t0, t1, req = { 0, 50000000L };
	uint64_t c0, c1;

	clock_gettime(CLOCK_MONOTONIC, &t0);
	c0 = _rdtsc();
	nanosleep(&req, NULL);
	c1 = _rdtsc();
	clock_gettime(CLOCK_MONOTONIC, &t1);

	double secs = (double)(t1.tv_sec - t0.tv_sec)
		    + (double)(t1.tv_nsec - t0.tv_nsec) / 1e9;
	return (double)(c1 - c0) / secs;
}

static int open_msr(int core)
{
	char path[64];
	snprintf(path, sizeof(path), "/dev/cpu/%d/msr", core);

	int fd = open(path, O_RDONLY);
	if (fd < 0) {
		fprintf(stderr, "open %s: %s\n", path, strerror(errno));
		fprintf(stderr, "  (needs root, and 'modprobe msr')\n");
		exit(EXIT_FAILURE);
	}
	return fd;
}

/*
 * MSR_PKG_ENERGY_STATUS is a 32-bit counter in bits 31:0; the upper half is
 * reserved. Truncating to uint32_t makes the later subtraction wrap correctly
 * when the counter rolls over.
 */
static inline uint32_t rd_energy(int fd)
{
	uint64_t v = 0;
	if (pread(fd, &v, sizeof(v), MSR_PKG_ENERGY_STATUS) != sizeof(v)) {
		fprintf(stderr, "pread PKG_ENERGY_STATUS: %s\n", strerror(errno));
		exit(EXIT_FAILURE);
	}
	return (uint32_t)v;
}

static double read_energy_unit(int fd)
{
	uint64_t unit = 0;
	if (pread(fd, &unit, sizeof(unit), MSR_RAPL_POWER_UNIT) != sizeof(unit)) {
		fprintf(stderr, "pread RAPL_POWER_UNIT: %s\n", strerror(errno));
		exit(EXIT_FAILURE);
	}
	return 1.0 / (double)(1u << ((unit >> 8) & 0x1F));
}

void rapl_sampler_init(struct rapl_sampler_t *s, int core, int mode,
		       uint64_t fixed_cycles)
{
	memset(s, 0, sizeof(*s));
	s->core = core;
	s->mode = mode;
	s->fixed_cycles = fixed_cycles;

	set_frequency_units(core);
	s->fd = open_msr(core);
	s->energy_unit_j = read_energy_unit(s->fd);
	s->tsc_hz = measure_tsc_hz();

	s->prev_e = rd_energy(s->fd);
	s->prev_tsc = _rdtsc();
	s->prev_f = frequency_msr_raw(core);
}

struct rapl_edge_t rapl_sampler_next(struct rapl_sampler_t *s)
{
	struct rapl_edge_t e;
	uint32_t cur_e;
	uint64_t tsc;

	if (s->mode == SAMPLE_FIXED) {
		busy_wait(s->fixed_cycles);
		cur_e = rd_energy(s->fd);
		tsc = _rdtsc();
	} else {
		/* Idle through most of the interval, then poll tightly so the
		 * edge is caught promptly without burning the whole period in
		 * preads. */
		if (s->period_est && s->edges_seen > WARMUP_EDGES)
			busy_wait(s->period_est * GUARD_NUM / GUARD_DEN);

		uint64_t spins = 0;
		do {
			cur_e = rd_energy(s->fd);
			tsc = _rdtsc();
			if (++spins > 100000000ULL) {
				fprintf(stderr, "RAPL counter stalled\n");
				exit(EXIT_FAILURE);
			}
		} while (cur_e == s->prev_e);
	}

	struct freq_sample_t cur_f = frequency_msr_raw(s->core);

	e.ticks = cur_e - s->prev_e;	/* wraps correctly */
	e.tsc = tsc;
	e.dtsc = tsc - s->prev_tsc;
	e.daperf = cur_f.aperf - s->prev_f.aperf;
	e.dmperf = cur_f.mperf - s->prev_f.mperf;

	if (s->mode == SAMPLE_EDGE) {
		if (s->period_est == 0) {
			s->period_est = e.dtsc;
		} else if (e.dtsc > s->period_est + s->period_est / 2) {
			/*
			 * An edge seen this late spans about two update
			 * intervals. Feeding it to the estimator would pull
			 * the estimate up by roughly the overshoot rate, so
			 * the reported period would partly measure the
			 * sampler rather than the part -- and, since the
			 * estimate sets the guard window, a longer estimate
			 * moves the next poll closer to the following edge,
			 * which is a feedback path with two fixed points.
			 * Checked over 322 committed runs and it does not
			 * operate (corr(overshoot, period) = -0.009), so this
			 * corrects no published number. It costs one branch
			 * and removes the coupling, so the question cannot
			 * come back.
			 */
			s->overshoots++;
		} else {
			/* EWMA, 1/16 weight */
			s->period_est += ((int64_t)e.dtsc - (int64_t)s->period_est) / 16;
		}
		s->edges_seen++;
	}

	s->prev_e = cur_e;
	s->prev_tsc = tsc;
	s->prev_f = cur_f;
	return e;
}

void rapl_sampler_close(struct rapl_sampler_t *s)
{
	if (s->fd >= 0)
		close(s->fd);
	s->fd = -1;
}
