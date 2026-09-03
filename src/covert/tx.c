/*
 * Covert-channel transmitter (Phase 2, unprivileged).
 *
 * Modulates package power by switching the operand a pool of victim threads
 * is streaming, which is the ctl->selector primitive the measurement driver
 * already uses to interleave conditions. Nothing here needs root: the
 * transmitter is an ordinary process running ordinary AVX loads.
 *
 * Two properties of the victim set make this cheap. Working-set victims cache
 * WS_SLOTS = 2 filled buffers, so alternating between exactly two selector
 * values costs a pointer swap rather than a refill -- the modulation is
 * therefore a change of *operand*, not of the amount of work done. And the
 * victims re-read ctl->selector every burst (~0.6 us), which bounds the
 * transition time far below any symbol period worth using.
 *
 * Symbol boundaries are absolute TSC deadlines, so jitter does not
 * accumulate: chip i is emitted at t0 + i*chip_tsc regardless of how late
 * chip i-1 was. Chips missed anyway are counted and reported -- a run with a
 * non-zero late_chips has outrun the mechanism, and its bit-error rate is not
 * a property of the channel.
 *
 * The receiver is a separate process (src/covert/rx_rapl.c) sharing no memory
 * with this one. Both read the invariant TSC, which any unprivileged process
 * can do; the receiver still has to find the frame itself, which is what the
 * preamble is for.
 */
#include <errno.h>
#include <getopt.h>
#include <inttypes.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <x86intrin.h>

#include "../../util/sampler.h"
#include "../../util/util.h"
#include "../../util/victim-pool.h"
#include "../../util/victim-utils.h"

/*
 * 13-bit Barker code. Its autocorrelation sidelobes are bounded at 1 against
 * a peak of 13, which is what lets the receiver find the frame by sliding a
 * correlation along a noisy trace rather than being told where to look.
 */
#define DEFAULT_PREAMBLE "1111100110101"

#define MAX_BITS 65536

enum code_t { CODE_MANCHESTER = 0, CODE_NRZ = 1 };

struct tx_config {
	const char *victim;
	int threads;
	int victim_core_start, victim_core_stride, max_victim_core;
	int tx_core;
	uint64_t on_sel, off_sel;
	double symbol_us;
	enum code_t code;
	const char *preamble;
	const char *message;
	int random_bits;
	uint64_t seed;
	int frames;
	double lead_ms, tail_ms, prewarm_ms;
};

static void usage(const char *prog)
{
	fprintf(stderr,
"Usage: %s [options]\n"
"\n"
"  --victim NAME          victim to modulate (default ws_l3_x8)\n"
"  --threads N            victim threads (default 4)\n"
"  --on SEL / --off SEL   the two operands to alternate\n"
"  --symbol-us US         bit period in microseconds (default 4000)\n"
"  --code manchester|nrz  line code (default manchester)\n"
"  --message STR          payload: ASCII, MSB first per byte\n"
"  --random-bits N        payload: N pseudorandom bits from --seed (default 64)\n"
"  --seed N               PRNG seed for --random-bits (default 12345)\n"
"  --frames N             repetitions of [preamble|payload] (default 8)\n"
"  --preamble BITS        sync word as a 0/1 string (default 13-bit Barker)\n"
"  --lead-ms MS           idle before the first frame (default 300)\n"
"  --tail-ms MS           idle after the last frame (default 300)\n"
"  --prewarm-ms MS        fill both operand buffers first (default 600)\n"
"  --tx-core N            core for this control thread (default 12, an E-core)\n"
"  --victim-core-start N / --victim-core-stride N / --max-victim-core N\n"
"\n"
"Writes a JSON ground-truth summary to stdout; progress to stderr.\n", prog);
}

static void parse_tx_args(int argc, char *argv[], struct tx_config *c)
{
	static struct option opts[] = {
		{ "victim",             required_argument, 0, 'v' },
		{ "threads",            required_argument, 0, 't' },
		{ "on",                 required_argument, 0, '1' },
		{ "off",                required_argument, 0, '0' },
		{ "symbol-us",          required_argument, 0, 'u' },
		{ "code",               required_argument, 0, 'C' },
		{ "message",            required_argument, 0, 'm' },
		{ "random-bits",        required_argument, 0, 'R' },
		{ "seed",               required_argument, 0, 'r' },
		{ "frames",             required_argument, 0, 'F' },
		{ "preamble",           required_argument, 0, 'P' },
		{ "lead-ms",            required_argument, 0, 'l' },
		{ "tail-ms",            required_argument, 0, 'T' },
		{ "prewarm-ms",         required_argument, 0, 'w' },
		{ "tx-core",            required_argument, 0, 'x' },
		{ "victim-core-start",  required_argument, 0, 'c' },
		{ "victim-core-stride", required_argument, 0, 'd' },
		{ "max-victim-core",    required_argument, 0, 'M' },
		{ "help",               no_argument,       0, 'h' },
		{ 0, 0, 0, 0 }
	};

	c->victim = "ws_l3_x8";
	c->threads = 4;
	c->victim_core_start = 2;
	c->victim_core_stride = 2;
	c->max_victim_core = 11;
	c->tx_core = 12;
	c->on_sel = 0xFFFFFFFFULL;
	c->off_sel = 0;
	c->symbol_us = 4000.0;
	c->code = CODE_MANCHESTER;
	c->preamble = DEFAULT_PREAMBLE;
	c->message = NULL;
	c->random_bits = 64;
	c->seed = 12345;
	c->frames = 8;
	c->lead_ms = 300.0;
	c->tail_ms = 300.0;
	c->prewarm_ms = 600.0;

	int o;
	while ((o = getopt_long(argc, argv, "", opts, NULL)) != -1) {
		switch (o) {
		case 'v': c->victim = optarg; break;
		case 't': c->threads = atoi(optarg); break;
		case '1': c->on_sel = strtoull(optarg, NULL, 0); break;
		case '0': c->off_sel = strtoull(optarg, NULL, 0); break;
		case 'u': c->symbol_us = atof(optarg); break;
		case 'm': c->message = optarg; break;
		case 'R': c->random_bits = atoi(optarg); break;
		case 'r': c->seed = strtoull(optarg, NULL, 0); break;
		case 'F': c->frames = atoi(optarg); break;
		case 'P': c->preamble = optarg; break;
		case 'l': c->lead_ms = atof(optarg); break;
		case 'T': c->tail_ms = atof(optarg); break;
		case 'w': c->prewarm_ms = atof(optarg); break;
		case 'x': c->tx_core = atoi(optarg); break;
		case 'c': c->victim_core_start = atoi(optarg); break;
		case 'd': c->victim_core_stride = atoi(optarg); break;
		case 'M': c->max_victim_core = atoi(optarg); break;
		case 'h': usage(argv[0]); exit(EXIT_SUCCESS);
		case 'C':
			if (strcmp(optarg, "manchester") == 0) c->code = CODE_MANCHESTER;
			else if (strcmp(optarg, "nrz") == 0) c->code = CODE_NRZ;
			else { fprintf(stderr, "Unknown --code '%s'\n", optarg); exit(EXIT_FAILURE); }
			break;
		default: usage(argv[0]); exit(EXIT_FAILURE);
		}
	}

	if (c->threads < 1) {
		fprintf(stderr, "--threads must be >= 1\n");
		exit(EXIT_FAILURE);
	}
	if (c->symbol_us <= 0) {
		fprintf(stderr, "--symbol-us must be positive\n");
		exit(EXIT_FAILURE);
	}
	if (c->frames < 1) {
		fprintf(stderr, "--frames must be >= 1\n");
		exit(EXIT_FAILURE);
	}
	if (c->seed == 0) {
		/* xorshift64 is absorbing at zero */
		fprintf(stderr, "--seed must be non-zero\n");
		exit(EXIT_FAILURE);
	}
	if (*c->preamble == '\0') {
		fprintf(stderr, "--preamble must not be empty\n");
		exit(EXIT_FAILURE);
	}
	for (const char *p = c->preamble; *p; p++) {
		if (*p != '0' && *p != '1') {
			fprintf(stderr, "--preamble must be a 0/1 string\n");
			exit(EXIT_FAILURE);
		}
	}
	if (c->on_sel == c->off_sel)
		fprintf(stderr, "note: --on equals --off, so this is an A/A "
				"control; the decoder should find nothing\n");
	/* The control thread busy-waits on deadlines, so sharing a core with a
	 * victim would both delay transitions and add load the modulation did
	 * not ask for. */
	for (int i = 0; i < c->threads; i++) {
		if (c->tx_core == c->victim_core_start + i * c->victim_core_stride) {
			fprintf(stderr, "--tx-core %d collides with victim %d\n",
				c->tx_core, i);
			exit(EXIT_FAILURE);
		}
	}
}

/* Payload bits: MSB first per byte for a message, xorshift64 otherwise. */
static int build_payload(const struct tx_config *c, unsigned char *bits, int max)
{
	int n = 0;

	if (c->message) {
		for (const char *p = c->message; *p; p++) {
			if (n + 8 > max) {
				fprintf(stderr, "message too long (max %d bits)\n", max);
				exit(EXIT_FAILURE);
			}
			for (int b = 7; b >= 0; b--)
				bits[n++] = (*p >> b) & 1;
		}
		if (n == 0) {
			fprintf(stderr, "--message is empty\n");
			exit(EXIT_FAILURE);
		}
	} else {
		if (c->random_bits < 1 || c->random_bits > max) {
			fprintf(stderr, "--random-bits must be in 1..%d\n", max);
			exit(EXIT_FAILURE);
		}
		uint64_t rng = c->seed;
		for (n = 0; n < c->random_bits; n++)
			bits[n] = xorshift64(&rng) & 1;
	}
	return n;
}

int main(int argc, char *argv[])
{
	struct tx_config cfg;
	unsigned char payload[MAX_BITS];

	parse_tx_args(argc, argv, &cfg);

	int (*victim_func)(void *) = get_victim(cfg.victim);
	int n_pre = (int)strlen(cfg.preamble);
	int n_pay = build_payload(&cfg, payload, MAX_BITS);
	int bits_per_frame = n_pre + n_pay;
	int chips_per_bit = (cfg.code == CODE_MANCHESTER) ? 2 : 1;

	long total_bits = (long)bits_per_frame * cfg.frames;
	long total_chips = total_bits * chips_per_bit;
	unsigned char *chips = malloc((size_t)total_chips);
	if (!chips) {
		fprintf(stderr, "out of memory for %ld chips\n", total_chips);
		return EXIT_FAILURE;
	}

	/*
	 * Manchester puts a transition inside every bit, so a decision is the
	 * difference between the two halves of one symbol. Drift slower than a
	 * symbol -- which is all of the thermal drift that ruined the
	 * pre-rework datasets -- is common-mode to both halves and cancels,
	 * leaving the receiver no baseline to track.
	 */
	long k = 0;
	for (int f = 0; f < cfg.frames; f++) {
		for (int i = 0; i < bits_per_frame; i++) {
			int bit = (i < n_pre) ? (cfg.preamble[i] == '1')
					      : payload[i - n_pre];
			if (cfg.code == CODE_MANCHESTER) {
				chips[k++] = bit ? 1 : 0;
				chips[k++] = bit ? 0 : 1;
			} else {
				chips[k++] = (unsigned char)bit;
			}
		}
	}

	pin_cpu(cfg.tx_core);
	sched_yield();
	double tsc_hz = measure_tsc_hz();
	uint64_t symbol_tsc = (uint64_t)(cfg.symbol_us * 1e-6 * tsc_hz);
	uint64_t chip_tsc = symbol_tsc / (uint64_t)chips_per_bit;
	if (chip_tsc == 0) {
		fprintf(stderr, "--symbol-us %g is below TSC resolution\n", cfg.symbol_us);
		return EXIT_FAILURE;
	}

	struct ctl_t *ctl = calloc(1, sizeof(*ctl));
	if (!ctl) {
		fprintf(stderr, "out of memory\n");
		return EXIT_FAILURE;
	}
	ctl->selector = cfg.off_sel;
	ctl->run = 1;

	struct victim_pool_t pool;
	victims_spawn(&pool, ctl, victim_func, cfg.threads,
		      cfg.victim_core_start, cfg.victim_core_stride,
		      cfg.max_victim_core);

	fprintf(stderr, "tx: victim=%s threads=%d code=%s symbol=%.0fus "
			"frames=%d bits/frame=%d (%d preamble + %d payload)\n",
		cfg.victim, cfg.threads,
		cfg.code == CODE_MANCHESTER ? "manchester" : "nrz",
		cfg.symbol_us, cfg.frames, bits_per_frame, n_pre, n_pay);

	/*
	 * Touch both operands before transmitting. A working-set victim fills
	 * a buffer per distinct selector value and caches two of them, so the
	 * first use of each value pays an mmap-populate and a fill that later
	 * uses do not. Left inside the frame that would put a large transient
	 * on the first symbols; done here, the package also settles from the
	 * allocation storm before t0.
	 */
	useconds_t half = (useconds_t)(cfg.prewarm_ms * 500.0);	/* us, halved */
	ctl->selector = cfg.on_sel;
	__sync_synchronize();
	usleep(half);
	ctl->selector = cfg.off_sel;
	__sync_synchronize();
	usleep(half);

	uint64_t t0 = _rdtsc() + (uint64_t)(cfg.lead_ms * 1e-3 * tsc_hz);
	uint64_t late_chips = 0, max_late_tsc = 0;

	for (long i = 0; i < total_chips; i++) {
		uint64_t deadline = t0 + (uint64_t)i * chip_tsc;
		int64_t slack = (int64_t)(deadline - _rdtsc());

		if (slack < 0) {
			late_chips++;
			if ((uint64_t)(-slack) > max_late_tsc)
				max_late_tsc = (uint64_t)(-slack);
		} else {
			busy_wait_until(deadline);
		}

		ctl->selector = chips[i] ? cfg.on_sel : cfg.off_sel;
		__sync_synchronize();
		ctl->epoch++;
	}

	uint64_t t_end = t0 + (uint64_t)total_chips * chip_tsc;
	busy_wait_until(t_end);
	ctl->selector = cfg.off_sel;
	__sync_synchronize();
	usleep((useconds_t)(cfg.tail_ms * 1000.0));

	uint64_t bursts = victims_bursts(&pool);
	victims_stop(&pool);

	/*
	 * Ground truth. The framing parameters and the preamble are what a
	 * receiver legitimately knows; payload_bits and tsc_start are for
	 * scoring afterwards, and the decoder must not sync on them.
	 */
	printf("{\n");
	printf("  \"victim\": \"%s\",\n", cfg.victim);
	printf("  \"threads\": %d,\n", cfg.threads);
	printf("  \"on_selector\": %" PRIu64 ",\n", cfg.on_sel);
	printf("  \"off_selector\": %" PRIu64 ",\n", cfg.off_sel);
	printf("  \"code\": \"%s\",\n", cfg.code == CODE_MANCHESTER ? "manchester" : "nrz");
	printf("  \"chips_per_bit\": %d,\n", chips_per_bit);
	printf("  \"symbol_us\": %.6f,\n", cfg.symbol_us);
	printf("  \"symbol_tsc\": %" PRIu64 ",\n", symbol_tsc);
	printf("  \"chip_tsc\": %" PRIu64 ",\n", chip_tsc);
	printf("  \"tsc_hz\": %.17g,\n", tsc_hz);
	printf("  \"tsc_start\": %" PRIu64 ",\n", t0);
	printf("  \"tsc_end\": %" PRIu64 ",\n", t_end);
	printf("  \"frames\": %d,\n", cfg.frames);
	printf("  \"bits_per_frame\": %d,\n", bits_per_frame);
	printf("  \"preamble_bits\": \"%s\",\n", cfg.preamble);
	printf("  \"payload_bits\": \"");
	for (int i = 0; i < n_pay; i++)
		putchar(payload[i] ? '1' : '0');
	printf("\",\n");
	printf("  \"message\": ");
	if (cfg.message) printf("\"%s\",\n", cfg.message); else printf("null,\n");
	printf("  \"seed\": %" PRIu64 ",\n", cfg.seed);
	printf("  \"lead_ms\": %.3f,\n", cfg.lead_ms);
	printf("  \"tail_ms\": %.3f,\n", cfg.tail_ms);
	printf("  \"prewarm_ms\": %.3f,\n", cfg.prewarm_ms);
	printf("  \"tx_core\": %d,\n", cfg.tx_core);
	printf("  \"victim_bursts\": %" PRIu64 ",\n", bursts);
	printf("  \"late_chips\": %" PRIu64 ",\n", late_chips);
	printf("  \"max_late_us\": %.3f\n", 1e6 * (double)max_late_tsc / tsc_hz);
	printf("}\n");

	fprintf(stderr, "tx: done, %ld chips, %" PRIu64 " late\n",
		total_chips, late_chips);

	free(chips);
	free(pool.vargs);
	free(ctl);
	return EXIT_SUCCESS;
}
