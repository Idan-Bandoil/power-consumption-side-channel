/*
 * Covert-channel receiver, tier 3: self-timing (Phase 2).
 *
 * The least-privileged receiver of the three, and the one hardest to take
 * away. Tier 2 at least reads a file, so a container that does not mount
 * sysfs, or a kernel that restricts cpufreq, removes it. This tier reads
 * nothing: it runs its own fixed workload and times it with rdtsc, both of
 * which any process can do anywhere. There is no interface to revoke.
 *
 * The mechanism is Hertzbleed's. The TSC is invariant -- it ticks at a fixed
 * rate regardless of the core clock -- while a fixed instruction stream that
 * never touches memory takes a fixed number of *core* cycles. So the TSC
 * elapsed over that stream is inversely proportional to the core frequency,
 * and a transmitter that makes the part throttle stretches the receiver's own
 * workload. The receiver never observes the victim; it observes itself
 * running slower.
 *
 * Like tier 2 this needs Config-B and needs the part to be throttling; see
 * the note in experiments/phase2_tier2_covert.json.
 *
 * The recorded level is the raw TSC each workload took, so the polarity is
 * the opposite of a tier-2 frequency trace: the heavier operand throttles the
 * part and the workload reads *longer*. The decoder resolves that from the
 * preamble and is not told.
 */
#include <errno.h>
#include <getopt.h>
#include <inttypes.h>
#include <math.h>
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

/*
 * 64 adds to one register per loop iteration.
 *
 * These are written as a dependency chain and do not execute as one. Measured
 * on this part the loop retires about 5 adds per core cycle -- the integer
 * ALU width -- not the 1 per cycle a serial chain would give: 12.8M adds in
 * 480 us of wall clock, cross-checked against CLOCK_MONOTONIC. Whatever the
 * core is doing with `add $imm, reg`, the consequence here is that no honest
 * cycle count can be attached to a loop, so this file does not attach one.
 *
 * It does not matter. What tier 3 needs is a workload whose duration is
 * proportional to 1/f_core, and any fixed instruction stream that never
 * touches memory has that property whether it issues at one per cycle or
 * five. What it must not be is memory-bound, since DRAM latency does not
 * scale with the core clock. So the level recorded below is the raw TSC the
 * workload took -- higher means slower means throttled -- rather than a
 * frequency derived through a made-up constant.
 */
#define ADD8 \
	"add $1, %[x]\n\t" "add $1, %[x]\n\t" "add $1, %[x]\n\t" "add $1, %[x]\n\t" \
	"add $1, %[x]\n\t" "add $1, %[x]\n\t" "add $1, %[x]\n\t" "add $1, %[x]\n\t"
#define ADD64 ADD8 ADD8 ADD8 ADD8 ADD8 ADD8 ADD8 ADD8

/*
 * Returns TSC elapsed over `loops` iterations of the dependent chain.
 *
 * The LFENCEs are load-bearing, not decoration. RDTSC is not serialising and
 * the closing read has no data dependency on the chain, so the out-of-order
 * engine is free to execute it while the adds are still in flight. Without
 * the fences this timer reports about six TSC ticks for sixty-four dependent
 * adds -- an implied 26 GHz -- and a 10% coefficient of variation that comes
 * from nothing but how far ahead the read drifted. LFENCE is dispatch
 * serialising on Intel, so it holds the read until the chain has retired.
 */
static inline uint64_t timed_work(uint64_t loops)
{
	uint64_t x = 1, n = loops;

	_mm_lfence();
	uint64_t t0 = _rdtsc();
	_mm_lfence();

	asm volatile(
		"1:\n\t"
		ADD64
		"sub $1, %[n]\n\t"
		"jnz 1b\n\t"
		: [x] "+r"(x), [n] "+r"(n)
		:
		: "cc");

	_mm_lfence();
	uint64_t t1 = _rdtsc();
	_mm_lfence();

	/* x is dead but must look live, or -O2 deletes the chain. */
	asm volatile("" :: "r"(x));
	return t1 - t0;
}

struct rx_config {
	int core;
	double duration_s;
	double sample_us;
	const char *out_path;
};

static void usage(const char *prog)
{
	fprintf(stderr,
"Usage: %s --duration S --out PATH [options]\n"
"\n"
"  --duration S    seconds to record (default 10)\n"
"  --out PATH      CSV output path (default rx_timing.csv)\n"
"  --sample-us US  target time per sample (default 200); the loop count is\n"
"                  calibrated at startup to hit it\n"
"  --core N        core to run on (default 0)\n"
"\n"
"Needs no privilege and reads no file. Requires Config-B, and requires the\n"
"part to be throttling -- see the tier-2 experiment's notes.\n"
"\n"
"Schema: tsc,work_tsc -- one row per timed workload, work_tsc being how long\n"
"it took. Higher means slower means throttled. JSON summary on stdout.\n", prog);
}

static void parse_rx_args(int argc, char *argv[], struct rx_config *c)
{
	static struct option opts[] = {
		{ "duration",  required_argument, 0, 'D' },
		{ "out",       required_argument, 0, 'o' },
		{ "sample-us", required_argument, 0, 's' },
		{ "core",      required_argument, 0, 'a' },
		{ "help",      no_argument,       0, 'h' },
		{ 0, 0, 0, 0 }
	};

	c->core = 0;
	c->duration_s = 10.0;
	c->sample_us = 200.0;
	c->out_path = "rx_timing.csv";

	int o;
	while ((o = getopt_long(argc, argv, "", opts, NULL)) != -1) {
		switch (o) {
		case 'D': c->duration_s = atof(optarg); break;
		case 'o': c->out_path = optarg; break;
		case 's': c->sample_us = atof(optarg); break;
		case 'a': c->core = atoi(optarg); break;
		case 'h': usage(argv[0]); exit(EXIT_SUCCESS);
		default: usage(argv[0]); exit(EXIT_FAILURE);
		}
	}

	if (c->duration_s <= 0 || c->sample_us <= 0) {
		fprintf(stderr, "--duration and --sample-us must be positive\n");
		exit(EXIT_FAILURE);
	}
}

struct timing_row {
	uint64_t tsc;
	uint64_t dtsc;
};

int main(int argc, char *argv[])
{
	struct rx_config cfg;
	parse_rx_args(argc, argv, &cfg);

	pin_cpu(cfg.core);
	sched_yield();
	/* Best-effort: an unprivileged process cannot lower its nice value, and
	 * this receiver is supposed to work without privilege. */
	setpriority(PRIO_PROCESS, 0, PRIORITY_HIGH);

	double tsc_hz = measure_tsc_hz();

	/*
	 * Calibrate the loop count for the requested sample period.
	 *
	 * The warm-up is the part that matters. A core that has just been woken
	 * sits at a few hundred MHz and takes milliseconds to ramp, so a
	 * calibration taken immediately reads the loop as several times more
	 * expensive than it steady-state is -- which then picks a loop count
	 * several times too small and oversamples for the whole run. Spin for a
	 * while first, then calibrate twice and keep the second.
	 */
	uint64_t probe = 20000;
	uint64_t warm_end = _rdtsc() + (uint64_t)(0.05 * tsc_hz);
	while ((int64_t)(warm_end - _rdtsc()) > 0)
		timed_work(probe);

	double tsc_per_loop = 0.0;
	for (int i = 0; i < 2; i++)
		tsc_per_loop = (double)timed_work(probe) / (double)probe;

	uint64_t loops = (uint64_t)(cfg.sample_us * 1e-6 * tsc_hz / tsc_per_loop);
	if (loops < 1)
		loops = 1;

	uint64_t capacity = (uint64_t)(cfg.duration_s / (cfg.sample_us * 1e-6)) + 1024;
	struct timing_row *log = calloc(capacity, sizeof(*log));
	if (!log) {
		fprintf(stderr, "out of memory for %" PRIu64 " samples\n", capacity);
		return EXIT_FAILURE;
	}

	fprintf(stderr, "rx_timing: core=%d sample=%.0fus (%" PRIu64 " loops at "
		"%.2f TSC each) duration=%.1fs\n",
		cfg.core, cfg.sample_us, loops, tsc_per_loop, cfg.duration_s);

	uint64_t t_start = _rdtsc();
	uint64_t t_stop = t_start + (uint64_t)(cfg.duration_s * tsc_hz);
	uint64_t n = 0;

	/*
	 * Back to back, with no idle between samples. Unlike the other two
	 * receivers there is no external clock to wait for -- the measurement
	 * *is* the workload, so pausing would only create gaps in the trace.
	 */
	while (n < capacity) {
		uint64_t d = timed_work(loops);
		log[n].tsc = _rdtsc();
		log[n].dtsc = d;
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
	/* The level is the workload's own duration: higher is slower is
	 * throttled, the opposite sense to a frequency trace. The decoder
	 * resolves that from the preamble and needs no telling. */
	fprintf(f, "tsc,work_tsc\n");
	for (uint64_t i = 0; i < n; i++)
		fprintf(f, "%" PRIu64 ",%" PRIu64 "\n", log[i].tsc, log[i].dtsc);
	fclose(f);

	/* Spread of the workload's own duration, as a noise floor: if this is
	 * large the receiver cannot see a small frequency shift however long it
	 * integrates. */
	double mean = 0.0;
	for (uint64_t i = 0; i < n; i++)
		mean += (double)log[i].dtsc;
	mean /= (double)(n ? n : 1);
	double var = 0.0;
	for (uint64_t i = 0; i < n; i++) {
		double d = (double)log[i].dtsc - mean;
		var += d * d;
	}
	var /= (double)(n > 1 ? n - 1 : 1);

	printf("{\n");
	printf("  \"receiver\": \"timing\",\n");
	printf("  \"core\": %d,\n", cfg.core);
	printf("  \"duration_s\": %.6f,\n", cfg.duration_s);
	printf("  \"sample_us\": %.6f,\n", cfg.sample_us);
	printf("  \"loops\": %" PRIu64 ",\n", loops);
	printf("  \"tsc_per_loop_calib\": %.4f,\n", tsc_per_loop);
	printf("  \"interval_tsc\": %.0f,\n", mean);
	printf("  \"tsc_hz\": %.17g,\n", tsc_hz);
	printf("  \"tsc_start\": %" PRIu64 ",\n", t_start);
	printf("  \"tsc_end\": %" PRIu64 ",\n", t_end);
	printf("  \"samples_written\": %" PRIu64 ",\n", n);
	printf("  \"work_mean_tsc\": %.2f,\n", mean);
	printf("  \"work_cv\": %.6f,\n", mean > 0 ? sqrt(var) / mean : 0.0);
	printf("  \"log_full\": %s,\n", n >= capacity ? "true" : "false");
	printf("  \"out\": \"%s\"\n", cfg.out_path);
	printf("}\n");

	fprintf(stderr, "rx_timing: %" PRIu64 " samples, work %.0f TSC "
		"(CV %.3f%%)\n", n, mean, mean > 0 ? 100.0 * sqrt(var) / mean : 0.0);

	free(log);
	return EXIT_SUCCESS;
}
