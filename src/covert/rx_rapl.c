/*
 * Covert-channel receiver, tier 1: RAPL via /dev/cpu/N/msr (Phase 2).
 *
 * Records a raw power trace and nothing else. It is deliberately ignorant of
 * what is being transmitted -- no symbol period, no preamble, no message --
 * so that everything the decoder recovers is recovered from the trace. The
 * only thing shared with the transmitter is the invariant TSC, which any
 * unprivileged process can read and which every sample here is stamped with.
 *
 * Sampling is the same edge-triggered path the Phase 0/1 driver was validated
 * on (util/sampler.c), so the receiver's noise floor is a known quantity
 * rather than a new one. Each row covers the interval (tsc - dtsc, tsc].
 *
 * This tier needs root, and root can already read the victim's memory, so it
 * is the instrument reading rather than the attack. It bounds what the
 * unprivileged tiers are working towards.
 */
#include <errno.h>
#include <getopt.h>
#include <inttypes.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <unistd.h>
#include <x86intrin.h>

#include "../../util/sampler.h"
#include "../../util/util.h"

#define PRIORITY_HIGH -20

/* Headroom over the nominal ~1 kHz RAPL update rate when sizing the log. */
#define EXPECTED_HZ 1200.0

struct rx_config {
	int core;
	double duration_s;
	const char *out_path;
	int mode;
	uint64_t fixed_cycles;
};

static void usage(const char *prog)
{
	fprintf(stderr,
"Usage: %s --duration S --out PATH [options]\n"
"\n"
"  --duration S        seconds to record (default 10)\n"
"  --out PATH          CSV output path (default rx.csv)\n"
"  --core N            core to sample from (default 0, the isolated one)\n"
"  --mode edge|fixed   sampler mode (default edge)\n"
"  --fixed-cycles N    window length in fixed mode (default 2500000)\n"
"\n"
"Schema: tsc,ticks,dtsc,daperf,dmperf -- one row per RAPL counter edge,\n"
"covering the interval (tsc - dtsc, tsc]. JSON summary on stdout.\n", prog);
}

static void parse_rx_args(int argc, char *argv[], struct rx_config *c)
{
	static struct option opts[] = {
		{ "duration",     required_argument, 0, 'D' },
		{ "out",          required_argument, 0, 'o' },
		{ "core",         required_argument, 0, 'a' },
		{ "mode",         required_argument, 0, 'm' },
		{ "fixed-cycles", required_argument, 0, 'f' },
		{ "help",         no_argument,       0, 'h' },
		{ 0, 0, 0, 0 }
	};

	c->core = 0;
	c->duration_s = 10.0;
	c->out_path = "rx.csv";
	c->mode = SAMPLE_EDGE;
	c->fixed_cycles = 2500000;

	int o;
	while ((o = getopt_long(argc, argv, "", opts, NULL)) != -1) {
		switch (o) {
		case 'D': c->duration_s = atof(optarg); break;
		case 'o': c->out_path = optarg; break;
		case 'a': c->core = atoi(optarg); break;
		case 'f': c->fixed_cycles = strtoull(optarg, NULL, 10); break;
		case 'h': usage(argv[0]); exit(EXIT_SUCCESS);
		case 'm':
			if (strcmp(optarg, "edge") == 0) c->mode = SAMPLE_EDGE;
			else if (strcmp(optarg, "fixed") == 0) c->mode = SAMPLE_FIXED;
			else { fprintf(stderr, "Unknown --mode '%s'\n", optarg); exit(EXIT_FAILURE); }
			break;
		default: usage(argv[0]); exit(EXIT_FAILURE);
		}
	}

	if (c->duration_s <= 0) {
		fprintf(stderr, "--duration must be positive\n");
		exit(EXIT_FAILURE);
	}
}

int main(int argc, char *argv[])
{
	struct rx_config cfg;
	parse_rx_args(argc, argv, &cfg);

	/* Pin and prioritise before touching any MSR, so every reading comes
	 * from the same core. */
	pin_cpu(cfg.core);
	sched_yield();
	if (setpriority(PRIO_PROCESS, 0, PRIORITY_HIGH) != 0)
		fprintf(stderr, "warning: could not raise scheduling priority: %s\n",
			strerror(errno));

	struct rapl_sampler_t smp;
	rapl_sampler_init(&smp, cfg.core, cfg.mode, cfg.fixed_cycles);

	uint64_t capacity = (uint64_t)(cfg.duration_s * EXPECTED_HZ) + 1024;
	struct rapl_edge_t *log = calloc(capacity, sizeof(*log));
	if (!log) {
		fprintf(stderr, "out of memory for %" PRIu64 " samples\n", capacity);
		return EXIT_FAILURE;
	}

	uint64_t t_start = _rdtsc();
	uint64_t t_stop = t_start + (uint64_t)(cfg.duration_s * smp.tsc_hz);
	uint64_t n = 0;

	fprintf(stderr, "rx: core=%d duration=%.1fs mode=%s\n",
		cfg.core, cfg.duration_s, cfg.mode == SAMPLE_EDGE ? "edge" : "fixed");

	/*
	 * A full log is a truncated recording, not a wrapped one: the trace
	 * has to stay contiguous in time for the decoder to align to it.
	 */
	while (n < capacity) {
		log[n] = rapl_sampler_next(&smp);
		n++;
		if ((int64_t)(log[n - 1].tsc - t_stop) >= 0)
			break;
	}
	uint64_t t_end = _rdtsc();

	FILE *f = fopen(cfg.out_path, "w");
	if (f == NULL) {
		fprintf(stderr, "fopen %s: %s\n", cfg.out_path, strerror(errno));
		return EXIT_FAILURE;
	}
	fprintf(f, "tsc,ticks,dtsc,daperf,dmperf\n");
	for (uint64_t i = 0; i < n; i++)
		fprintf(f, "%" PRIu64 ",%" PRIu32 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 "\n",
			log[i].tsc, log[i].ticks, log[i].dtsc,
			log[i].daperf, log[i].dmperf);
	fclose(f);

	rapl_sampler_close(&smp);

	uint64_t zero_ticks = 0;
	for (uint64_t i = 0; i < n; i++)
		if (log[i].ticks == 0)
			zero_ticks++;

	printf("{\n");
	printf("  \"core\": %d,\n", cfg.core);
	printf("  \"duration_s\": %.6f,\n", cfg.duration_s);
	printf("  \"sample_mode\": \"%s\",\n", cfg.mode == SAMPLE_EDGE ? "edge" : "fixed");
	printf("  \"energy_unit_j\": %.17g,\n", smp.energy_unit_j);
	printf("  \"tsc_hz\": %.17g,\n", smp.tsc_hz);
	printf("  \"tsc_start\": %" PRIu64 ",\n", t_start);
	printf("  \"tsc_end\": %" PRIu64 ",\n", t_end);
	printf("  \"rapl_period_tsc\": %" PRIu64 ",\n", smp.period_est);
	printf("  \"rapl_period_ms\": %.6f,\n", 1000.0 * (double)smp.period_est / smp.tsc_hz);
	printf("  \"rapl_overshoots\": %" PRIu64 ",\n", smp.overshoots);
	printf("  \"samples_written\": %" PRIu64 ",\n", n);
	printf("  \"zero_tick_samples\": %" PRIu64 ",\n", zero_ticks);
	printf("  \"log_full\": %s,\n", n >= capacity ? "true" : "false");
	printf("  \"out\": \"%s\"\n", cfg.out_path);
	printf("}\n");

	fprintf(stderr, "rx: %" PRIu64 " samples, %.3f ms period, %" PRIu64 " overshoots\n",
		n, 1000.0 * (double)smp.period_est / smp.tsc_hz, smp.overshoots);

	free(log);
	return EXIT_SUCCESS;
}
