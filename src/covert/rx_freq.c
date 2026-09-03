/*
 * Covert-channel receiver, tier 2: scaling_cur_freq (Phase 2).
 *
 * This is the tier that carries the security claim. It needs **no privilege
 * at all**: /sys/devices/system/cpu/cpuN/cpufreq/scaling_cur_freq is
 * world-readable, and this process reads it and nothing else. No MSR, no
 * perf, no root, no shared memory with the transmitter.
 *
 * The mechanism is indirect and one step longer than tier 1's. The operand
 * changes the victim's power draw; if enough of the part is loaded for
 * something to limit, the extra power forces a clock reduction; the reduction
 * is visible in the frequency the kernel reports. So the sign inverts against
 * tier 1 -- the heavier operand reads as *lower* frequency -- and the channel
 * only exists while the part is actually limiting. On this machine four
 * victim threads are not enough to make it limit; ten are. See
 * experiments/phase2_tier2_feasibility.json.
 *
 * It must run under Config-B. Config-A pins the frequency precisely to remove
 * this response, so under Config-A this receiver reads a flat line by
 * construction.
 *
 * Unlike RAPL there is no counter edge to lock onto: the value is a level,
 * not an accumulator, so sampling is a fixed-rate poll and the decoder treats
 * the trace as sample-and-hold rather than integrating an energy increment.
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

#include "../../util/freq-utils.h"
#include "../../util/sampler.h"
#include "../../util/util.h"

#define PRIORITY_HIGH -20
#define MAX_WATCH 8

struct rx_config {
	int core;			/* where this process runs */
	int watch[MAX_WATCH];		/* CPUs whose frequency is sampled */
	int n_watch;
	double duration_s;
	double interval_us;
	const char *out_path;
};

static void usage(const char *prog)
{
	fprintf(stderr,
"Usage: %s --duration S --out PATH [options]\n"
"\n"
"  --duration S      seconds to record (default 10)\n"
"  --out PATH        CSV output path (default rx_freq.csv)\n"
"  --watch LIST      comma-separated CPUs to sample (default 2)\n"
"  --interval-us US  polling period (default 200)\n"
"  --core N          core to run on (default 1)\n"
"\n"
"Needs no privilege. Requires Config-B: under Config-A the frequency is\n"
"pinned and this receiver reads a flat line by construction.\n"
"\n"
"Schema: tsc,khz0[,khz1...] -- one row per poll. JSON summary on stdout.\n", prog);
}

static void parse_watch(const char *s, struct rx_config *c)
{
	char buf[256];
	snprintf(buf, sizeof(buf), "%s", s);
	c->n_watch = 0;
	for (char *tok = strtok(buf, ","); tok; tok = strtok(NULL, ",")) {
		if (c->n_watch >= MAX_WATCH) {
			fprintf(stderr, "--watch takes at most %d CPUs\n", MAX_WATCH);
			exit(EXIT_FAILURE);
		}
		c->watch[c->n_watch++] = atoi(tok);
	}
	if (c->n_watch == 0) {
		fprintf(stderr, "--watch needs at least one CPU\n");
		exit(EXIT_FAILURE);
	}
}

static void parse_rx_args(int argc, char *argv[], struct rx_config *c)
{
	static struct option opts[] = {
		{ "duration",    required_argument, 0, 'D' },
		{ "out",         required_argument, 0, 'o' },
		{ "watch",       required_argument, 0, 'w' },
		{ "interval-us", required_argument, 0, 'i' },
		{ "core",        required_argument, 0, 'a' },
		{ "help",        no_argument,       0, 'h' },
		{ 0, 0, 0, 0 }
	};

	c->core = 1;
	c->watch[0] = 2;
	c->n_watch = 1;
	c->duration_s = 10.0;
	c->interval_us = 200.0;
	c->out_path = "rx_freq.csv";

	int o;
	while ((o = getopt_long(argc, argv, "", opts, NULL)) != -1) {
		switch (o) {
		case 'D': c->duration_s = atof(optarg); break;
		case 'o': c->out_path = optarg; break;
		case 'w': parse_watch(optarg, c); break;
		case 'i': c->interval_us = atof(optarg); break;
		case 'a': c->core = atoi(optarg); break;
		case 'h': usage(argv[0]); exit(EXIT_SUCCESS);
		default: usage(argv[0]); exit(EXIT_FAILURE);
		}
	}

	if (c->duration_s <= 0 || c->interval_us <= 0) {
		fprintf(stderr, "--duration and --interval-us must be positive\n");
		exit(EXIT_FAILURE);
	}
}

struct freq_row {
	uint64_t tsc;
	uint32_t khz[MAX_WATCH];
};

int main(int argc, char *argv[])
{
	struct rx_config cfg;
	parse_rx_args(argc, argv, &cfg);

	pin_cpu(cfg.core);
	sched_yield();
	/* Best-effort only: an unprivileged process cannot lower its nice
	 * value, and the receiver is supposed to work without privilege. */
	setpriority(PRIO_PROCESS, 0, PRIORITY_HIGH);

	double tsc_hz = measure_tsc_hz();
	uint64_t interval_tsc = (uint64_t)(cfg.interval_us * 1e-6 * tsc_hz);
	if (interval_tsc == 0) {
		fprintf(stderr, "--interval-us %g is below TSC resolution\n", cfg.interval_us);
		return EXIT_FAILURE;
	}

	int fds[MAX_WATCH];
	for (int i = 0; i < cfg.n_watch; i++)
		fds[i] = cpufreq_open(cfg.watch[i]);

	uint64_t capacity = (uint64_t)(cfg.duration_s / (cfg.interval_us * 1e-6)) + 1024;
	struct freq_row *log = calloc(capacity, sizeof(*log));
	if (!log) {
		fprintf(stderr, "out of memory for %" PRIu64 " samples\n", capacity);
		return EXIT_FAILURE;
	}

	fprintf(stderr, "rx_freq: core=%d watch=%d cpu(s) interval=%.0fus duration=%.1fs\n",
		cfg.core, cfg.n_watch, cfg.interval_us, cfg.duration_s);

	uint64_t t_start = _rdtsc();
	uint64_t t_stop = t_start + (uint64_t)(cfg.duration_s * tsc_hz);
	uint64_t n = 0, late = 0;

	/* Absolute deadlines, as the transmitter uses: a slow poll must not
	 * push its successors and stretch the sampling grid. */
	while (n < capacity) {
		uint64_t deadline = t_start + n * interval_tsc;
		if ((int64_t)(deadline - _rdtsc()) > 0)
			busy_wait_until(deadline);
		else
			late++;

		log[n].tsc = _rdtsc();
		for (int i = 0; i < cfg.n_watch; i++)
			log[n].khz[i] = cpufreq_read(fds[i]);
		n++;
		if ((int64_t)(log[n - 1].tsc - t_stop) >= 0)
			break;
	}
	uint64_t t_end = _rdtsc();

	for (int i = 0; i < cfg.n_watch; i++)
		close(fds[i]);

	FILE *f = fopen(cfg.out_path, "w");
	if (f == NULL) {
		fprintf(stderr, "fopen %s: %s\n", cfg.out_path, strerror(errno));
		return EXIT_FAILURE;
	}
	fprintf(f, "tsc");
	for (int i = 0; i < cfg.n_watch; i++)
		fprintf(f, ",khz%d", cfg.watch[i]);
	fprintf(f, "\n");
	for (uint64_t j = 0; j < n; j++) {
		fprintf(f, "%" PRIu64, log[j].tsc);
		for (int i = 0; i < cfg.n_watch; i++)
			fprintf(f, ",%" PRIu32, log[j].khz[i]);
		fprintf(f, "\n");
	}
	fclose(f);

	/* How often the reported value actually changed. sysfs may serve a
	 * cached pstate, in which case the effective resolution is far coarser
	 * than the polling rate and the decoder is working from held values. */
	uint64_t changes = 0;
	for (uint64_t j = 1; j < n; j++)
		if (log[j].khz[0] != log[j - 1].khz[0])
			changes++;

	printf("{\n");
	printf("  \"receiver\": \"freq\",\n");
	printf("  \"core\": %d,\n", cfg.core);
	printf("  \"watch\": [");
	for (int i = 0; i < cfg.n_watch; i++)
		printf("%s%d", i ? ", " : "", cfg.watch[i]);
	printf("],\n");
	printf("  \"duration_s\": %.6f,\n", cfg.duration_s);
	printf("  \"interval_us\": %.6f,\n", cfg.interval_us);
	printf("  \"interval_tsc\": %" PRIu64 ",\n", interval_tsc);
	printf("  \"tsc_hz\": %.17g,\n", tsc_hz);
	printf("  \"tsc_start\": %" PRIu64 ",\n", t_start);
	printf("  \"tsc_end\": %" PRIu64 ",\n", t_end);
	printf("  \"samples_written\": %" PRIu64 ",\n", n);
	printf("  \"late_polls\": %" PRIu64 ",\n", late);
	printf("  \"value_changes\": %" PRIu64 ",\n", changes);
	printf("  \"log_full\": %s,\n", n >= capacity ? "true" : "false");
	printf("  \"out\": \"%s\"\n", cfg.out_path);
	printf("}\n");

	fprintf(stderr, "rx_freq: %" PRIu64 " samples, %" PRIu64 " late, "
		"%" PRIu64 " value changes\n", n, late, changes);

	free(log);
	return EXIT_SUCCESS;
}
