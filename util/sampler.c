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
#define MSR_PP0_ENERGY_STATUS 0x639	/* core domain (P+E cores) */
#define MSR_PP1_ENERGY_STATUS 0x641	/* client graphics domain, when present */

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

/*
 * Reads an energy-status MSR, returning its low 32 bits (the counter; the upper
 * half is reserved). Returns 0 and clears *ok on failure -- reading an
 * unimplemented domain MSR faults and the pread returns an error, which is how
 * a domain's absence is detected rather than assumed.
 */
static inline uint32_t rd_energy_msr(int fd, off_t msr, int *ok)
{
	uint64_t v = 0;
	if (pread(fd, &v, sizeof(v), msr) != sizeof(v)) {
		*ok = 0;
		return 0;
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

void rapl_sampler_enable_domains(struct rapl_sampler_t *s)
{
	int ok_pp0 = 1, ok_pp1 = 1;
	uint32_t pp0 = rd_energy_msr(s->fd, MSR_PP0_ENERGY_STATUS, &ok_pp0);
	uint32_t pp1 = rd_energy_msr(s->fd, MSR_PP1_ENERGY_STATUS, &ok_pp1);

	s->read_domains = 1;
	s->has_pp0 = ok_pp0;
	s->has_pp1 = ok_pp1;
	s->prev_pp0 = pp0;
	s->prev_pp1 = pp1;

	if (!ok_pp0)
		fprintf(stderr, "note: PP0 (core) RAPL domain not readable; "
			"core/uncore split unavailable\n");
	if (!ok_pp1)
		fprintf(stderr, "note: PP1 (graphics) RAPL domain not present; "
			"uncore will be reported as package minus core\n");
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

	/*
	 * Sub-domains, read once now that the package edge has been caught. The
	 * cores' energy MSRs advance on their own schedule, but differencing two
	 * reads taken at consecutive package edges yields the domain's energy
	 * over exactly the [prev edge, this edge] interval regardless, since the
	 * counters are cumulative. The few hundred nanoseconds between the
	 * package read above and these is negligible against a ~1 ms period and
	 * is common-mode across conditions.
	 */
	e.pp0_ticks = 0;
	e.pp1_ticks = 0;
	if (s->read_domains) {
		if (s->has_pp0) {
			int ok = 1;
			uint32_t pp0 = rd_energy_msr(s->fd, MSR_PP0_ENERGY_STATUS, &ok);
			e.pp0_ticks = pp0 - s->prev_pp0;	/* wraps correctly */
			s->prev_pp0 = pp0;
		}
		if (s->has_pp1) {
			int ok = 1;
			uint32_t pp1 = rd_energy_msr(s->fd, MSR_PP1_ENERGY_STATUS, &ok);
			e.pp1_ticks = pp1 - s->prev_pp1;
			s->prev_pp1 = pp1;
		}
	}

	if (s->mode == SAMPLE_EDGE) {
		/*
		 * The first edge is a fragment of an update period, not a
		 * period: the sampler opens at an arbitrary phase within one,
		 * so its dtsc is uniform in (0, T]. Seeding from it is what
		 * made this estimator latch. Once the overshoot branch stopped
		 * feeding the EWMA -- which it does deliberately, see below --
		 * a seed under two thirds of T put every subsequent real edge
		 * over the overshoot threshold, and the branch that rejects
		 * them is also the branch that cannot correct them. The
		 * estimate froze at the seed for the whole run and the
		 * receiver reported ~100% of its edges late: measured at
		 * 0.21-0.23 ms against a true 0.99 ms in two of the first five
		 * runs of results/20260921-185944-phase2_tier1_validate, which
		 * is what caught it. Recorded energies were unaffected -- the
		 * poll loop runs until the counter actually moves -- but the
		 * guard window and the overshoot gate both read the estimate,
		 * so a latched run polls ~80% of each period instead of ~12%
		 * and its quality statistic means nothing.
		 *
		 * Discarding the fragment and learning unconditionally through
		 * warmup makes the absorbing state unreachable rather than
		 * unlikely: by the time the rejection test is live, the
		 * estimate has already converged on the true period.
		 */
		if (s->edges_seen == 0) {
			/* a partial interval: carries no period information */
		} else if (s->period_est == 0) {
			s->period_est = e.dtsc;
		} else if (s->edges_seen <= WARMUP_EDGES) {
			/*
			 * The guard is not engaged yet, so every edge here is
			 * caught promptly and its dtsc is a real period. A
			 * heavier weight than steady state, to converge before
			 * the rejection test starts gating what gets learned.
			 */
			s->period_est += ((int64_t)e.dtsc - (int64_t)s->period_est) / 4;
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
