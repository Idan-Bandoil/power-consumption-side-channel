/* _GNU_SOURCE comes from CFLAGS. */
/*
 * Battery-discharge cross-validation of RAPL (critique item B1.2).
 *
 * Every power number in this thesis comes from one software energy counter,
 * MSR_PKG_ENERGY_STATUS, which on client Intel silicon is a partly-modelled
 * quantity. The standing objection is that the operand effect lives in RAPL's
 * activity model rather than in the die. This harness answers it with an
 * instrument that shares nothing with RAPL: the battery's own current and
 * voltage sensors. If, running on battery, the heavier operand also draws more
 * measured discharge power -- by an amount comparable to what RAPL reports for
 * the same contrast -- then the effect is physically real, not a counter
 * artefact.
 *
 * It alternates a victim pool between two operands on long interleaved arms
 * (battery telemetry updates ~1 Hz and is coarse, so arms are seconds, not the
 * milliseconds the RAPL driver uses), and over each arm records BOTH the RAPL
 * package power (energy delta over the arm) and the mean battery V*I. The two
 * are then compared by analysis.battery.
 *
 * Must run on battery: on mains current_now reports charge current and there is
 * no discharge to measure. The runner refuses battery unless --allow-battery,
 * which exists for exactly this. Turbo is left to the runner's config; Config-A
 * keeps the comparison to the +1.9 W Config-A RAPL figure honest, though the
 * on-battery power state is its own regime and only the *delta* is compared.
 */
#include <errno.h>
#include <getopt.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>
#include <immintrin.h>

#include "../util/rapl-utils.h"
#include "../util/sampler.h"
#include "../util/util.h"
#include "../util/victim-pool.h"
#include "../util/victim-utils.h"

#define BATT_DIR "/sys/class/power_supply/BAT0"
#define PRIORITY_HIGH -20

struct bx_config {
	const char *victim;
	int threads;
	int monitor_core;
	int victim_core_start, victim_core_stride, max_victim_core;
	uint64_t on_sel, off_sel;
	int arm_ms;
	int settle_ms;
	int rounds;
	int poll_ms;
	uint64_t seed;
	const char *out_path;
};

struct arm_row {
	int round;
	int cond;		/* 0 = off, 1 = on */
	double arm_s;
	double rapl_w;
	double batt_w;
	int n_batt;
};

static long read_ll(const char *path, int *ok)
{
	FILE *f = fopen(path, "r");
	if (!f) { *ok = 0; return 0; }
	long v = 0;
	int n = fscanf(f, "%ld", &v);
	fclose(f);
	*ok = (n == 1);
	return v;
}

static int read_status(char *buf, size_t n)
{
	FILE *f = fopen(BATT_DIR "/status", "r");
	if (!f) return 0;
	int ok = fgets(buf, (int)n, f) != NULL;
	fclose(f);
	if (ok) buf[strcspn(buf, "\n")] = '\0';
	return ok;
}

/* Battery discharge power in watts, from current_now (uA) * voltage_now (uV). */
static double read_batt_w(int *ok)
{
	int ok_i = 0, ok_v = 0;
	long i_ua = read_ll(BATT_DIR "/current_now", &ok_i);
	long v_uv = read_ll(BATT_DIR "/voltage_now", &ok_v);
	*ok = ok_i && ok_v;
	if (!*ok) return 0.0;
	/* uA * uV = 1e-12 W; magnitude, since current_now is unsigned here and
	 * the direction is in `status`. */
	return ((double)i_ua * (double)v_uv) / 1e12;
}

static void usage(const char *p)
{
	fprintf(stderr,
"usage: %s [options]\n"
"  --victim NAME            victim to modulate (default ws_l3_x8)\n"
"  --threads N              victim threads (default 4)\n"
"  --monitor-core N         core for this monitor thread (default 0)\n"
"  --victim-core-start N / --victim-core-stride N / --max-victim-core N\n"
"  --on SEL / --off SEL     operands to alternate (default 0xFFFFFFFF / 0)\n"
"  --arm-ms N               per-arm duration (default 4000)\n"
"  --settle-ms N            discarded head of each arm (default 800)\n"
"  --rounds N               interleaved rounds per condition (default 20)\n"
"  --poll-ms N              battery poll interval (default 150)\n"
"  --seed N                 arm-order shuffle seed (default 12345)\n"
"  --out PATH               CSV output path\n", p);
}

static void parse_bx_args(int argc, char **argv, struct bx_config *c)
{
	c->victim = "ws_l3_x8";
	c->threads = 4;
	c->monitor_core = 0;
	c->victim_core_start = 2;
	c->victim_core_stride = 2;
	c->max_victim_core = 11;
	c->on_sel = 4294967295ULL;
	c->off_sel = 0;
	c->arm_ms = 4000;
	c->settle_ms = 800;
	c->rounds = 20;
	c->poll_ms = 150;
	c->seed = 12345;
	c->out_path = "out/battery.csv";

	static struct option opts[] = {
		{ "victim", required_argument, 0, 'v' },
		{ "threads", required_argument, 0, 't' },
		{ "monitor-core", required_argument, 0, 'm' },
		{ "victim-core-start", required_argument, 0, 'c' },
		{ "victim-core-stride", required_argument, 0, 'd' },
		{ "max-victim-core", required_argument, 0, 'M' },
		{ "on", required_argument, 0, 'O' },
		{ "off", required_argument, 0, 'F' },
		{ "arm-ms", required_argument, 0, 'a' },
		{ "settle-ms", required_argument, 0, 's' },
		{ "rounds", required_argument, 0, 'r' },
		{ "poll-ms", required_argument, 0, 'p' },
		{ "seed", required_argument, 0, 'S' },
		{ "out", required_argument, 0, 'o' },
		{ "help", no_argument, 0, 'h' },
		{ 0, 0, 0, 0 }
	};
	int ch;
	while ((ch = getopt_long(argc, argv, "v:t:m:c:d:M:O:F:a:s:r:p:S:o:h", opts, NULL)) != -1) {
		switch (ch) {
		case 'v': c->victim = optarg; break;
		case 't': c->threads = atoi(optarg); break;
		case 'm': c->monitor_core = atoi(optarg); break;
		case 'c': c->victim_core_start = atoi(optarg); break;
		case 'd': c->victim_core_stride = atoi(optarg); break;
		case 'M': c->max_victim_core = atoi(optarg); break;
		case 'O': c->on_sel = strtoull(optarg, NULL, 0); break;
		case 'F': c->off_sel = strtoull(optarg, NULL, 0); break;
		case 'a': c->arm_ms = atoi(optarg); break;
		case 's': c->settle_ms = atoi(optarg); break;
		case 'r': c->rounds = atoi(optarg); break;
		case 'p': c->poll_ms = atoi(optarg); break;
		case 'S': c->seed = strtoull(optarg, NULL, 0); break;
		case 'o': c->out_path = optarg; break;
		case 'h': usage(argv[0]); exit(0);
		default: usage(argv[0]); exit(EXIT_FAILURE);
		}
	}
}

int main(int argc, char *argv[])
{
	struct bx_config cfg;
	parse_bx_args(argc, argv, &cfg);

	int (*victim_func)(void *) = get_victim(cfg.victim);
	if (!victim_func) {
		fprintf(stderr, "unknown victim '%s'\n", cfg.victim);
		return EXIT_FAILURE;
	}

	/* Fail early and loudly if there is no discharge to measure -- a run on
	 * mains is not a cross-check, it is a null by construction. */
	char status[64] = "unknown";
	read_status(status, sizeof(status));
	int batt_ok = 0;
	double batt0 = read_batt_w(&batt_ok);
	if (!batt_ok) {
		fprintf(stderr, "cannot read %s current/voltage\n", BATT_DIR);
		return EXIT_FAILURE;
	}
	fprintf(stderr, "battery: status=%s, instantaneous %.2f W\n", status, batt0);
	if (strcmp(status, "Discharging") != 0)
		fprintf(stderr, "WARNING: battery is not discharging (%s); on mains "
			"there is no discharge power to compare. Unplug the charger.\n",
			status);

	pin_cpu(cfg.monitor_core);
	sched_yield();
	if (setpriority(PRIO_PROCESS, 0, PRIORITY_HIGH) != 0)
		fprintf(stderr, "warning: could not raise priority: %s\n", strerror(errno));

	double tsc_hz = measure_tsc_hz();
	set_rapl_units(cfg.monitor_core);

	struct ctl_t *ctl = calloc(1, sizeof(*ctl));
	if (!ctl) { fprintf(stderr, "oom\n"); return EXIT_FAILURE; }
	ctl->selector = cfg.off_sel;
	ctl->run = 1;

	struct victim_pool_t pool;
	victims_spawn(&pool, ctl, victim_func, cfg.threads,
		      cfg.victim_core_start, cfg.victim_core_stride,
		      cfg.max_victim_core);

	/* Touch both operands before recording so a working-set victim's
	 * per-operand mmap-populate and fill do not land inside an arm. */
	ctl->selector = cfg.on_sel; __sync_synchronize(); usleep(400000);
	ctl->selector = cfg.off_sel; __sync_synchronize(); usleep(400000);

	int total = cfg.rounds * 2;
	struct arm_row *rows = calloc(total, sizeof(*rows));
	if (!rows) { fprintf(stderr, "oom\n"); return EXIT_FAILURE; }

	fprintf(stderr, "battery_xcheck: victim=%s threads=%d rounds=%d arm=%dms "
		"settle=%dms poll=%dms on=%llu off=%llu\n",
		cfg.victim, cfg.threads, cfg.rounds, cfg.arm_ms, cfg.settle_ms,
		cfg.poll_ms, (unsigned long long)cfg.on_sel,
		(unsigned long long)cfg.off_sel);

	uint64_t rng = cfg.seed;
	int idx = 0;
	uint64_t arm_tsc = (uint64_t)((double)cfg.arm_ms * 1e-3 * tsc_hz);

	for (int r = 0; r < cfg.rounds; r++) {
		/* Interleave: randomise which operand leads each round so a slow
		 * discharge drift is common-mode across conditions rather than
		 * aligned with one of them. */
		int first = (int)(xorshift64(&rng) & 1);
		int order[2] = { first, 1 - first };

		for (int j = 0; j < 2; j++) {
			int cond = order[j];
			ctl->selector = cond ? cfg.on_sel : cfg.off_sel;
			__sync_synchronize();
			ctl->epoch++;

			/* Discard the settle head: battery current lags a load
			 * step by hundreds of ms, so the start of an arm still
			 * reflects the previous operand. */
			usleep((useconds_t)cfg.settle_ms * 1000);

			double e0 = rapl_msr(cfg.monitor_core, PKG_ENERGY);
			uint64_t t0 = _rdtsc();
			uint64_t deadline = t0 + arm_tsc;

			double sum_bw = 0.0;
			int n_bw = 0;
			while (_rdtsc() < deadline) {
				int ok = 0;
				double bw = read_batt_w(&ok);
				if (ok) { sum_bw += bw; n_bw++; }
				usleep((useconds_t)cfg.poll_ms * 1000);
			}

			uint64_t t1 = _rdtsc();
			double e1 = rapl_msr(cfg.monitor_core, PKG_ENERGY);
			double arm_s = (double)(t1 - t0) / tsc_hz;

			rows[idx].round = r;
			rows[idx].cond = cond;
			rows[idx].arm_s = arm_s;
			/* rapl_msr returns cumulative joules; the counter can wrap,
			 * but over a ~4 s arm at ~20 W that is ~80 J against a
			 * 2^14-unit * ... range of >200 kJ, so no wrap here. */
			rows[idx].rapl_w = (e1 - e0) / arm_s;
			rows[idx].batt_w = n_bw ? sum_bw / n_bw : 0.0;
			rows[idx].n_batt = n_bw;
			idx++;
		}
		fprintf(stderr, "  round %d/%d done\n", r + 1, cfg.rounds);
	}

	victims_stop(&pool);

	char end_status[64] = "unknown";
	read_status(end_status, sizeof(end_status));

	FILE *f = fopen(cfg.out_path, "w");
	if (!f) { fprintf(stderr, "fopen %s: %s\n", cfg.out_path, strerror(errno)); return EXIT_FAILURE; }
	fprintf(f, "round,cond,arm_s,rapl_w,batt_w,n_batt\n");
	for (int i = 0; i < idx; i++)
		fprintf(f, "%d,%d,%.6f,%.6f,%.6f,%d\n", rows[i].round, rows[i].cond,
			rows[i].arm_s, rows[i].rapl_w, rows[i].batt_w, rows[i].n_batt);
	fclose(f);

	printf("{\n");
	printf("  \"victim\": \"%s\",\n", cfg.victim);
	printf("  \"threads\": %d,\n", cfg.threads);
	printf("  \"on_sel\": %llu,\n", (unsigned long long)cfg.on_sel);
	printf("  \"off_sel\": %llu,\n", (unsigned long long)cfg.off_sel);
	printf("  \"rounds\": %d,\n", cfg.rounds);
	printf("  \"arm_ms\": %d,\n", cfg.arm_ms);
	printf("  \"settle_ms\": %d,\n", cfg.settle_ms);
	printf("  \"poll_ms\": %d,\n", cfg.poll_ms);
	printf("  \"tsc_hz\": %.17g,\n", tsc_hz);
	printf("  \"battery_status_start\": \"%s\",\n", status);
	printf("  \"battery_status_end\": \"%s\",\n", end_status);
	printf("  \"arms_written\": %d,\n", idx);
	printf("  \"out\": \"%s\"\n", cfg.out_path);
	printf("}\n");

	free(rows);
	free(pool.vargs);
	free(ctl);
	return EXIT_SUCCESS;
}
