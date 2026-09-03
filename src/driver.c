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
#include "../util/sampler.h"
#include "../util/util.h"
#include "../util/victim-pool.h"
#include "../util/victim-utils.h"

#define PRIORITY_HIGH         -20

struct sample_t {
	uint32_t block;
	uint32_t cond;
	uint32_t ticks;		/* raw RAPL energy units since previous edge */
	uint32_t _pad;
	uint64_t dtsc;
	uint64_t daperf;
	uint64_t dmperf;
};

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

	struct rapl_sampler_t smp;
	rapl_sampler_init(&smp, cfg.attacker_core, cfg.mode, cfg.fixed_cycles);

	struct ctl_t *ctl = calloc(1, sizeof(*ctl));
	if (!ctl) {
		fprintf(stderr, "out of memory\n");
		return EXIT_FAILURE;
	}
	ctl->selector = selectors[0];
	ctl->run = 1;

	struct victim_pool_t pool;
	victims_spawn(&pool, ctl, cfg.victim_func, cfg.nthreads,
		      cfg.victim_core_start, cfg.victim_core_stride,
		      cfg.max_victim_core);

	/*
	 * Block order: every condition appears blocks_per_condition times,
	 * shuffled with a logged seed. Interleaving is the whole point --
	 * running each condition as one long block confounds it with thermal
	 * drift, which is what made the older datasets unusable.
	 */
	int total_blocks = num_conditions * cfg.blocks_per_condition;
	int warmup = cfg.warmup_blocks < 0 ? 0 : cfg.warmup_blocks;
	uint32_t *order = calloc(warmup + total_blocks, sizeof(*order));
	if (!order) {
		fprintf(stderr, "out of memory\n");
		return EXIT_FAILURE;
	}
	/*
	 * Warmup blocks run before anything is recorded and cycle through
	 * every condition, so each one's operand buffers are faulted in and
	 * filled before the first measured sample. --settle discards samples
	 * inside a block and is no defence against a run-level transient: a
	 * victim that populates hundreds of megabytes under MAP_POPULATE has
	 * been seen to draw an extra 2.75 W across the first few blocks.
	 */
	for (int i = 0; i < warmup; i++)
		order[i] = (uint32_t)(i % num_conditions);
	uint32_t *measured = order + warmup;
	uint64_t rng = cfg.seed;
	if (cfg.sequential) {
		/* Deliberately confounded: each condition as one contiguous
		 * phase, so die temperature rises across the run in lockstep
		 * with the condition label. Only for demonstrating the
		 * artifact -- the analysis will fail the interleaving gate. */
		for (int i = 0; i < total_blocks; i++)
			measured[i] = (uint32_t)(i / cfg.blocks_per_condition);
	} else {
		for (int i = 0; i < total_blocks; i++)
			measured[i] = (uint32_t)(i % num_conditions);
		shuffle_u32(measured, total_blocks, &rng);
	}

	uint64_t total_samples = (uint64_t)total_blocks * cfg.samples_per_block;
	struct sample_t *log = calloc(total_samples, sizeof(*log));
	if (!log) {
		fprintf(stderr, "out of memory for %" PRIu64 " samples\n", total_samples);
		return EXIT_FAILURE;
	}

	/* Let the victims reach steady state before the first measurement. */
	usleep(200000);

	/* Victim throughput, so a watt difference can be expressed per byte of
	 * operand traffic rather than only per victim name. */
	uint64_t bursts0 = victims_bursts(&pool), bursts1 = 0;
	uint64_t work_tsc0 = _rdtsc();
	uint64_t idx = 0;

	fprintf(stderr, "victim=%s threads=%d conditions=%d blocks=%d samples/block=%" PRIu64
		" warmup=%d\n",
		cfg.victim_name, cfg.nthreads, num_conditions, total_blocks,
		cfg.samples_per_block, warmup);

	for (int b = 0; b < warmup + total_blocks; b++) {
		uint32_t cond = order[b];
		int measuring = b >= warmup;

		/* Throughput is counted over the measured blocks only, so a
		 * warmup that runs at a different rate cannot skew pJ/byte. */
		if (b == warmup) {
			bursts0 = victims_bursts(&pool);
			work_tsc0 = _rdtsc();
		}

		ctl->selector = selectors[cond];
		__sync_synchronize();
		ctl->epoch++;

		int keep = 0;
		uint64_t wanted = cfg.settle_samples + cfg.samples_per_block;

		for (uint64_t k = 0; k < wanted; k++) {
			struct rapl_edge_t e = rapl_sampler_next(&smp);

			if (measuring && k >= (uint64_t)cfg.settle_samples) {
				log[idx].block = (uint32_t)(b - warmup);
				log[idx].cond = cond;
				log[idx].ticks = e.ticks;
				log[idx].dtsc = e.dtsc;
				log[idx].daperf = e.daperf;
				log[idx].dmperf = e.dmperf;
				idx++;
				keep++;
			}
		}

		if (!measuring)
			fprintf(stderr, "  warmup block %d/%d (cond %u, discarded)\n",
				b + 1, warmup, cond);
		else if (((b - warmup) % 20) == 0 || b == warmup + total_blocks - 1)
			fprintf(stderr, "  block %d/%d (cond %u, %d kept)\n",
				b - warmup + 1, total_blocks, cond, keep);
	}

	uint64_t work_tsc1 = _rdtsc();
	bursts1 = victims_bursts(&pool);
	double work_s = (double)(work_tsc1 - work_tsc0) / smp.tsc_hz;
	double bursts_per_s = (double)(bursts1 - bursts0) / work_s;
	double bytes_per_s = bursts_per_s * (double)pool.vargs[0].bytes_per_burst;

	victims_stop(&pool);
	rapl_sampler_close(&smp);
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
	printf("  \"warmup_blocks\": %d,\n", warmup);
	printf("  \"attacker_core\": %d,\n", cfg.attacker_core);
	printf("  \"victim_core_start\": %d,\n", cfg.victim_core_start);
	printf("  \"victim_core_stride\": %d,\n", cfg.victim_core_stride);
	printf("  \"sample_mode\": \"%s\",\n", cfg.mode == SAMPLE_EDGE ? "edge" : "fixed");
	printf("  \"order\": \"%s\",\n", cfg.sequential ? "sequential" : "shuffled");
	printf("  \"seed\": %" PRIu64 ",\n", cfg.seed);
	printf("  \"energy_unit_j\": %.17g,\n", smp.energy_unit_j);
	printf("  \"max_frequency_khz\": %u,\n", maximum_frequency);
	printf("  \"tsc_hz\": %.17g,\n", smp.tsc_hz);
	printf("  \"rapl_period_tsc\": %" PRIu64 ",\n", smp.period_est);
	printf("  \"rapl_period_ms\": %.6f,\n", 1000.0 * (double)smp.period_est / smp.tsc_hz);
	printf("  \"rapl_overshoots\": %" PRIu64 ",\n", smp.overshoots);
	printf("  \"samples_written\": %" PRIu64 ",\n", idx);
	printf("  \"victim_bursts\": %" PRIu64 ",\n", bursts1 - bursts0);
	printf("  \"victim_bytes_per_burst\": %" PRIu64 ",\n", pool.vargs[0].bytes_per_burst);
	printf("  \"victim_bytes_per_s\": %.6g,\n", bytes_per_s);
	printf("  \"out\": \"%s\"\n", cfg.out_path);
	printf("}\n");

	free(log);
	free(order);
	free(pool.vargs);
	free(ctl);
	return EXIT_SUCCESS;
}
