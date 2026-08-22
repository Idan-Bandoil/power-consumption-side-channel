/* _GNU_SOURCE comes from CFLAGS; clone(2) and tgkill need it. */
#include <errno.h>
#include <fcntl.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <immintrin.h>

#include "../util/freq-utils.h"
#include "../util/util.h"
#include "../util/victim-utils.h"

#define STACK_SIZE            65536
#define PRIORITY_HIGH         -20

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

struct sample_t {
	uint32_t block;
	uint32_t cond;
	uint32_t ticks;		/* raw RAPL energy units since previous edge */
	uint32_t _pad;
	uint64_t dtsc;
	uint64_t daperf;
	uint64_t dmperf;
};

static inline void busy_wait(uint64_t cycles)
{
	uint64_t start = _rdtsc();
	while ((_rdtsc() - start) < cycles)
		_mm_pause();
}

/*
 * The TSC is invariant, so one calibration against CLOCK_MONOTONIC converts
 * every recorded dtsc into seconds. Without this the analysis cannot turn
 * energy per edge into watts.
 */
static double measure_tsc_hz(void)
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

static void spawn_victims(const struct run_config_t *cfg, struct ctl_t *ctl,
			  char *stacks, struct victim_args_t *vargs, int *tids)
{
	for (int i = 0; i < cfg->nthreads; i++) {
		vargs[i].ctl = ctl;
		vargs[i].core_id = cfg->victim_core_start + i * cfg->victim_core_stride;

		if (vargs[i].core_id > cfg->max_victim_core) {
			fprintf(stderr,
				"Victim %d would land on core %d, past --max-victim-core %d.\n"
				"Reduce --threads or raise the limit.\n",
				i, vargs[i].core_id, cfg->max_victim_core);
			exit(EXIT_FAILURE);
		}

		/* Each victim gets its own args struct: the shared one the old
		 * driver reused was mutated between clones and only worked
		 * because of the sleep that followed. */
		tids[i] = clone(cfg->victim_func, stacks + (size_t)(i + 1) * STACK_SIZE,
				CLONE_VM | SIGCHLD, &vargs[i]);
		if (tids[i] < 0) {
			fprintf(stderr, "clone victim %d: %s\n", i, strerror(errno));
			exit(EXIT_FAILURE);
		}
	}
}

static void stop_victims(struct ctl_t *ctl, int nthreads, int *tids)
{
	ctl->run = 0;
	__sync_synchronize();

	/* Victims poll ctl->run once per burst (sub-microsecond), so this is
	 * normally immediate; the kill is only a backstop. */
	for (int waited = 0; waited < 200; waited++) {
		int alive = 0;
		for (int i = 0; i < nthreads; i++) {
			if (tids[i] > 0 && waitpid(tids[i], NULL, WNOHANG) == 0)
				alive++;
			else
				tids[i] = -1;
		}
		if (!alive)
			return;
		usleep(10000);
	}

	for (int i = 0; i < nthreads; i++) {
		if (tids[i] > 0) {
			syscall(SYS_tgkill, tids[i], tids[i], SIGTERM);
			waitpid(tids[i], NULL, 0);
		}
	}
}

static void write_csv(const char *path, const struct sample_t *log, uint64_t n)
{
	FILE *f = fopen(path, "w");
	if (f == NULL) {
		fprintf(stderr, "fopen %s: %s\n", path, strerror(errno));
		exit(EXIT_FAILURE);
	}

	fprintf(f, "block,cond,ticks,dtsc,daperf,dmperf\n");
	for (uint64_t i = 0; i < n; i++) {
		fprintf(f, "%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 "\n",
			log[i].block, log[i].cond, log[i].ticks,
			log[i].dtsc, log[i].daperf, log[i].dmperf);
	}
	fclose(f);
}

int main(int argc, char *argv[])
{
	struct run_config_t cfg;
	uint64_t selectors[MAX_SELECTORS];
	char out_path[512];

	parse_args(argc, argv, &cfg);
	int num_conditions = read_selectors(cfg.input_path, selectors, MAX_SELECTORS);

	if (cfg.out_path == NULL) {
		snprintf(out_path, sizeof(out_path), "out/%s.csv", cfg.victim_name);
		cfg.out_path = out_path;
	}

	/* The monitor thread is this thread. Pin and prioritise it before
	 * touching any MSR, so every reading comes from the same core. */
	pin_cpu(cfg.attacker_core);
	sched_yield();
	if (setpriority(PRIO_PROCESS, 0, PRIORITY_HIGH) != 0)
		fprintf(stderr, "warning: could not raise scheduling priority: %s\n",
			strerror(errno));

	set_frequency_units(cfg.attacker_core);
	int fd = open_msr(cfg.attacker_core);
	double energy_unit = read_energy_unit(fd);
	double tsc_hz = measure_tsc_hz();

	char *stacks = mmap(NULL, (size_t)(cfg.nthreads + 1) * STACK_SIZE,
			    PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (stacks == MAP_FAILED) {
		fprintf(stderr, "mmap stacks: %s\n", strerror(errno));
		return EXIT_FAILURE;
	}

	struct ctl_t *ctl = calloc(1, sizeof(*ctl));
	struct victim_args_t *vargs = calloc(cfg.nthreads, sizeof(*vargs));
	int *tids = calloc(cfg.nthreads, sizeof(*tids));
	if (!ctl || !vargs || !tids) {
		fprintf(stderr, "out of memory\n");
		return EXIT_FAILURE;
	}
	ctl->selector = selectors[0];
	ctl->run = 1;

	spawn_victims(&cfg, ctl, stacks, vargs, tids);

	/*
	 * Block order: every condition appears blocks_per_condition times,
	 * shuffled with a logged seed. Interleaving is the whole point --
	 * running each condition as one long block confounds it with thermal
	 * drift, which is what made the older datasets unusable.
	 */
	int total_blocks = num_conditions * cfg.blocks_per_condition;
	uint32_t *order = calloc(total_blocks, sizeof(*order));
	if (!order) {
		fprintf(stderr, "out of memory\n");
		return EXIT_FAILURE;
	}
	uint64_t rng = cfg.seed;
	if (cfg.sequential) {
		/* Deliberately confounded: each condition as one contiguous
		 * phase, so die temperature rises across the run in lockstep
		 * with the condition label. Only for demonstrating the
		 * artifact -- the analysis will fail the interleaving gate. */
		for (int i = 0; i < total_blocks; i++)
			order[i] = (uint32_t)(i / cfg.blocks_per_condition);
	} else {
		for (int i = 0; i < total_blocks; i++)
			order[i] = (uint32_t)(i % num_conditions);
		shuffle_u32(order, total_blocks, &rng);
	}

	uint64_t total_samples = (uint64_t)total_blocks * cfg.samples_per_block;
	struct sample_t *log = calloc(total_samples, sizeof(*log));
	if (!log) {
		fprintf(stderr, "out of memory for %" PRIu64 " samples\n", total_samples);
		return EXIT_FAILURE;
	}

	/* Let the victims reach steady state before the first measurement. */
	usleep(200000);

	uint32_t prev_e = rd_energy(fd);
	uint64_t prev_tsc = _rdtsc();
	struct freq_sample_t prev_f = frequency_msr_raw(cfg.attacker_core);

	uint64_t period_est = 0;
	uint64_t edges_seen = 0;
	uint64_t overshoots = 0;
	uint64_t idx = 0;

	fprintf(stderr, "victim=%s threads=%d conditions=%d blocks=%d samples/block=%" PRIu64 "\n",
		cfg.victim_name, cfg.nthreads, num_conditions, total_blocks, cfg.samples_per_block);

	for (int b = 0; b < total_blocks; b++) {
		uint32_t cond = order[b];

		ctl->selector = selectors[cond];
		__sync_synchronize();
		ctl->epoch++;

		int keep = 0;
		uint64_t wanted = cfg.settle_samples + cfg.samples_per_block;

		for (uint64_t k = 0; k < wanted; k++) {
			uint32_t cur_e;
			uint64_t tsc;

			if (cfg.mode == SAMPLE_FIXED) {
				busy_wait(cfg.fixed_cycles);
				cur_e = rd_energy(fd);
				tsc = _rdtsc();
			} else {
				/* Idle through most of the interval, then poll
				 * tightly so the edge is caught promptly
				 * without burning the whole period in preads. */
				if (period_est && edges_seen > WARMUP_EDGES)
					busy_wait(period_est * GUARD_NUM / GUARD_DEN);

				uint64_t spins = 0;
				do {
					cur_e = rd_energy(fd);
					tsc = _rdtsc();
					if (++spins > 100000000ULL) {
						fprintf(stderr, "RAPL counter stalled\n");
						exit(EXIT_FAILURE);
					}
				} while (cur_e == prev_e);
			}

			struct freq_sample_t cur_f = frequency_msr_raw(cfg.attacker_core);

			uint32_t ticks = cur_e - prev_e;	/* wraps correctly */
			uint64_t dtsc = tsc - prev_tsc;

			if (cfg.mode == SAMPLE_EDGE) {
				if (period_est == 0)
					period_est = dtsc;
				else if (dtsc > period_est + period_est / 2)
					overshoots++;
				/* EWMA, 1/16 weight */
				period_est += ((int64_t)dtsc - (int64_t)period_est) / 16;
				edges_seen++;
			}

			if (k >= (uint64_t)cfg.settle_samples) {
				log[idx].block = (uint32_t)b;
				log[idx].cond = cond;
				log[idx].ticks = ticks;
				log[idx].dtsc = dtsc;
				log[idx].daperf = cur_f.aperf - prev_f.aperf;
				log[idx].dmperf = cur_f.mperf - prev_f.mperf;
				idx++;
				keep++;
			}

			prev_e = cur_e;
			prev_tsc = tsc;
			prev_f = cur_f;
		}

		if ((b % 20) == 0 || b == total_blocks - 1)
			fprintf(stderr, "  block %d/%d (cond %u, %d kept)\n",
				b + 1, total_blocks, cond, keep);
	}

	stop_victims(ctl, cfg.nthreads, tids);
	close(fd);
	write_csv(cfg.out_path, log, idx);

	/* Machine-readable summary on stdout; the Python runner folds this
	 * into the run manifest. Human progress goes to stderr. */
	printf("{\n");
	printf("  \"victim\": \"%s\",\n", cfg.victim_name);
	printf("  \"threads\": %d,\n", cfg.nthreads);
	printf("  \"conditions\": %d,\n", num_conditions);
	printf("  \"selectors\": [");
	for (int i = 0; i < num_conditions; i++)
		printf("%s%" PRIu64, i ? ", " : "", selectors[i]);
	printf("],\n");
	printf("  \"blocks_per_condition\": %d,\n", cfg.blocks_per_condition);
	printf("  \"samples_per_block\": %" PRIu64 ",\n", cfg.samples_per_block);
	printf("  \"settle_samples\": %d,\n", cfg.settle_samples);
	printf("  \"attacker_core\": %d,\n", cfg.attacker_core);
	printf("  \"victim_core_start\": %d,\n", cfg.victim_core_start);
	printf("  \"victim_core_stride\": %d,\n", cfg.victim_core_stride);
	printf("  \"sample_mode\": \"%s\",\n", cfg.mode == SAMPLE_EDGE ? "edge" : "fixed");
	printf("  \"order\": \"%s\",\n", cfg.sequential ? "sequential" : "shuffled");
	printf("  \"seed\": %" PRIu64 ",\n", cfg.seed);
	printf("  \"energy_unit_j\": %.17g,\n", energy_unit);
	printf("  \"max_frequency_khz\": %u,\n", maximum_frequency);
	printf("  \"tsc_hz\": %.17g,\n", tsc_hz);
	printf("  \"rapl_period_tsc\": %" PRIu64 ",\n", period_est);
	printf("  \"rapl_period_ms\": %.6f,\n", 1000.0 * (double)period_est / tsc_hz);
	printf("  \"rapl_overshoots\": %" PRIu64 ",\n", overshoots);
	printf("  \"samples_written\": %" PRIu64 ",\n", idx);
	printf("  \"out\": \"%s\"\n", cfg.out_path);
	printf("}\n");

	free(log);
	free(order);
	free(tids);
	free(vargs);
	free(ctl);
	munmap(stacks, (size_t)(cfg.nthreads + 1) * STACK_SIZE);
	return EXIT_SUCCESS;
}
