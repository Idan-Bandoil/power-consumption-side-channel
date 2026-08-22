#include "util.h"
#include "victim-utils.h"

#include <ctype.h>
#include <errno.h>
#include <getopt.h>
#include <sys/prctl.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>

/*
 * Gets the value Time Stamp Counter
 */
uint64_t get_time(void)
{
	uint64_t cycles;
	asm volatile(
		"rdtscp\n\t"
		"shl $32, %%rdx\n\t"
		"or %%rdx, %0\n\t"
		: "=a"(cycles)
		:
		: "rcx", "rdx", "memory"
	);

	return cycles;
}

void pin_cpu(size_t core_ID)
{
	cpu_set_t set;

	CPU_ZERO(&set);
	CPU_SET(core_ID, &set);
	if (sched_setaffinity(0, sizeof(cpu_set_t), &set) < 0)
	{
		fprintf(stderr, "Unable to set affinity to core %zu\n", core_ID);
		exit(EXIT_FAILURE);
	}
}

void victim_pin(int core_ID)
{
	pin_cpu((size_t)core_ID);
	if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0)
		fprintf(stderr, "warning: PR_SET_PDEATHSIG failed on core %d\n", core_ID);
	/* The parent may already be gone; re-check after arming. */
	if (getppid() == 1)
		_exit(0);
	sched_yield();
}

int read_selectors(const char *path, uint64_t *selectors, int max)
{
	int num_selectors = 0;
	size_t len = 0;
	ssize_t read = 0;
	char *line = NULL;
	FILE *selectors_file = NULL;

	selectors_file = fopen(path, "r");
	if (selectors_file == NULL)
	{
		fprintf(stderr, "Cannot open selector file '%s': %s\n", path, strerror(errno));
		exit(EXIT_FAILURE);
	}

	while ((read = getline(&line, &len, selectors_file)) != -1)
	{
		if (read > 0 && line[read - 1] == '\n')
		{
			line[--read] = '\0';
		}

		/* Skip blank lines and '#' comments so selector files can be annotated */
		if (read == 0 || line[0] == '#')
		{
			continue;
		}

		if (num_selectors >= max)
		{
			fprintf(stderr, "Too many selectors in '%s' (max %d)\n", path, max);
			exit(EXIT_FAILURE);
		}

		if (sscanf(line, "%" SCNu64, &(selectors[num_selectors])) != 1)
		{
			fprintf(stderr, "Malformed selector line: '%s'\n", line);
			exit(EXIT_FAILURE);
		}
		num_selectors += 1;
	}

	free(line);
	fclose(selectors_file);

	if (num_selectors == 0)
	{
		fprintf(stderr, "No selectors found in '%s'\n", path);
		exit(EXIT_FAILURE);
	}

	return num_selectors;
}

uint64_t xorshift64(uint64_t *state)
{
	uint64_t x = *state;
	x ^= x << 13;
	x ^= x >> 7;
	x ^= x << 17;
	*state = x;
	return x;
}

void shuffle_u32(uint32_t *a, int n, uint64_t *state)
{
	for (int i = n - 1; i > 0; i--)
	{
		int j = (int)(xorshift64(state) % (uint64_t)(i + 1));
		uint32_t t = a[i];
		a[i] = a[j];
		a[j] = t;
	}
}

static void usage(const char *prog)
{
	fprintf(stderr,
		"Usage: %s --victim NAME [options]\n"
		"\n"
		"  --victim NAME           victim workload (see --list-victims)\n"
		"  --threads N             concurrent victim threads      (default 4)\n"
		"  --samples N             samples kept per block         (default 100)\n"
		"  --blocks N              blocks per condition           (default 100)\n"
		"  --settle N              samples discarded after each\n"
		"                          condition switch               (default 3)\n"
		"  --attacker-core N       core the monitor pins to       (default 0)\n"
		"  --victim-core-start N   first victim core              (default 2)\n"
		"  --victim-core-stride N  stride between victim cores    (default 2)\n"
		"  --max-victim-core N     refuse to schedule past this   (default 11)\n"
		"  --mode edge|fixed       sampling strategy              (default edge)\n"
		"  --order shuffled|sequential\n"
		"                          block order. 'sequential' runs each\n"
		"                          condition as one contiguous phase, which\n"
		"                          reproduces the pre-2026 confound on\n"
		"                          purpose -- use it only to demonstrate the\n"
		"                          artifact, never to measure  (default shuffled)\n"
		"  --fixed-cycles N        window for --mode fixed        (default 2500000)\n"
		"  --seed N                block-order PRNG seed          (default 12345)\n"
		"  --input FILE            selector list                  (default input.txt)\n"
		"  --out FILE              output CSV                     (default out/<victim>.csv)\n"
		"  --list-victims          print available victims and exit\n"
		"\n"
		"Conditions are interleaved in a seeded random block order so that\n"
		"thermal drift is common-mode across them rather than confounded with\n"
		"them. Total run time is roughly\n"
		"    blocks * conditions * (samples + settle) * RAPL update period.\n",
		prog);
}

void parse_args(int argc, char *argv[], struct run_config_t *cfg)
{
	static struct option opts[] = {
		{ "victim",            required_argument, 0, 'v' },
		{ "threads",           required_argument, 0, 't' },
		{ "samples",           required_argument, 0, 's' },
		{ "blocks",            required_argument, 0, 'b' },
		{ "settle",            required_argument, 0, 'S' },
		{ "attacker-core",     required_argument, 0, 'a' },
		{ "victim-core-start", required_argument, 0, 'c' },
		{ "victim-core-stride",required_argument, 0, 'd' },
		{ "max-victim-core",   required_argument, 0, 'M' },
		{ "mode",              required_argument, 0, 'm' },
		{ "order",             required_argument, 0, 'O' },
		{ "fixed-cycles",      required_argument, 0, 'f' },
		{ "seed",              required_argument, 0, 'r' },
		{ "input",             required_argument, 0, 'i' },
		{ "out",               required_argument, 0, 'o' },
		{ "list-victims",      no_argument,       0, 'L' },
		{ "help",              no_argument,       0, 'h' },
		{ 0, 0, 0, 0 }
	};

	/* Defaults: ~20s run, 2 conditions, 100 blocks each. */
	cfg->nthreads = 4;
	cfg->samples_per_block = 100;
	cfg->blocks_per_condition = 100;
	cfg->settle_samples = 3;
	cfg->attacker_core = 0;
	cfg->victim_core_start = 2;
	cfg->victim_core_stride = 2;
	cfg->max_victim_core = 11;
	cfg->mode = SAMPLE_EDGE;
	cfg->sequential = 0;
	cfg->fixed_cycles = 2500000;
	cfg->seed = 12345;
	cfg->victim_name = NULL;
	cfg->input_path = "input.txt";
	cfg->out_path = NULL;
	cfg->victim_func = NULL;

	int c;
	while ((c = getopt_long(argc, argv, "", opts, NULL)) != -1)
	{
		switch (c)
		{
		case 'v': cfg->victim_name = optarg; break;
		case 't': cfg->nthreads = atoi(optarg); break;
		case 's': cfg->samples_per_block = strtoull(optarg, NULL, 10); break;
		case 'b': cfg->blocks_per_condition = atoi(optarg); break;
		case 'S': cfg->settle_samples = atoi(optarg); break;
		case 'a': cfg->attacker_core = atoi(optarg); break;
		case 'c': cfg->victim_core_start = atoi(optarg); break;
		case 'd': cfg->victim_core_stride = atoi(optarg); break;
		case 'M': cfg->max_victim_core = atoi(optarg); break;
		case 'f': cfg->fixed_cycles = strtoull(optarg, NULL, 10); break;
		case 'r': cfg->seed = strtoull(optarg, NULL, 10); break;
		case 'i': cfg->input_path = optarg; break;
		case 'o': cfg->out_path = optarg; break;
		case 'L': list_victims(stdout); exit(EXIT_SUCCESS);
		case 'h': usage(argv[0]); exit(EXIT_SUCCESS);
		case 'm':
			if (strcmp(optarg, "edge") == 0) cfg->mode = SAMPLE_EDGE;
			else if (strcmp(optarg, "fixed") == 0) cfg->mode = SAMPLE_FIXED;
			else { fprintf(stderr, "Unknown --mode '%s'\n", optarg); exit(EXIT_FAILURE); }
			break;
		case 'O':
			if (strcmp(optarg, "shuffled") == 0) cfg->sequential = 0;
			else if (strcmp(optarg, "sequential") == 0) cfg->sequential = 1;
			else { fprintf(stderr, "Unknown --order '%s'\n", optarg); exit(EXIT_FAILURE); }
			break;
		default:
			usage(argv[0]);
			exit(EXIT_FAILURE);
		}
	}

	if (cfg->victim_name == NULL)
	{
		fprintf(stderr, "Missing required --victim\n\n");
		usage(argv[0]);
		exit(EXIT_FAILURE);
	}
	if (cfg->nthreads < 1)
	{
		fprintf(stderr, "--threads must be >= 1\n");
		exit(EXIT_FAILURE);
	}
	if (cfg->blocks_per_condition < 1 || cfg->samples_per_block < 1)
	{
		fprintf(stderr, "--blocks and --samples must be >= 1\n");
		exit(EXIT_FAILURE);
	}
	if (cfg->seed == 0)
	{
		/* xorshift64 is absorbing at zero */
		fprintf(stderr, "--seed must be non-zero\n");
		exit(EXIT_FAILURE);
	}

	cfg->victim_func = get_victim(cfg->victim_name);
}
