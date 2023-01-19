#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <immintrin.h>

#include "../util/freq-utils.h"
#include "../util/rapl-utils.h"
#include "../util/util.h"
#include "../util/configuration-utils.h"

#define TIME_BETWEEN_MEASUREMENTS 1000000L // 1 millisecond

#define STACK_SIZE 8192

static Config config[MAX_CONFIG_SIZE];
int config_size;

static int NUM_VICTIMS = 2;
static char *victim_names[] = {"imul", "avx_mul"};
// declare victim functions
static int imul_victim(void *varg);
static int avx_mul_victim(void *varg);
// array of victim functions
static int (*victim_functions[])(void *) = {imul_victim, avx_mul_victim};


static __attribute__((noinline)) int imul_victim(void *varg){
	if(get_integer_value(config, config_size, "num_threads") == 1)
		pin_cpu(get_integer_value(config, config_size, "attacker_core_id"));
	struct args_t *arg = varg;
	uint64_t count = arg->selector;
	// printf("imul_victim\n");

	asm volatile(
		".align 64\t\n"
		"loopimul:\n\t"

		"imul $0xffffffffffffffff, %0, %%r13\n\t"
		"imul $0xffffffffffffffff, %0, %%r13\n\t"
		"imul $0xffffffffffffffff, %0, %%r13\n\t"
		"imul $0xffffffffffffffff, %0, %%r13\n\t"
		"imul $0xffffffffffffffff, %0, %%r13\n\t"
		"imul $0xffffffffffffffff, %0, %%r13\n\t"
		"imul $0xffffffffffffffff, %0, %%r13\n\t"
		"imul $0xffffffffffffffff, %0, %%r13\n\t"

		"jmp loopimul\n\t"
		: 
		: "r"(count)
		: "r13");

	return 0;
}

static __attribute__((noinline)) int avx_mul_victim(void *varg){
	if(get_integer_value(config, config_size, "num_threads") == 1)
		pin_cpu(get_integer_value(config, config_size, "attacker_core_id"));
	struct args_t *arg = varg;
	int count = (int)arg->selector;
	__m256i m1 = _mm256_set_epi32(count, count, count, count, count, count, count, count);
	__m256i m2 = _mm256_set_epi32(count, count, count, count, count, count, count, count);

	int constant = get_integer_value(config, config_size, "constant");
	if(constant != -1)
		m2 = _mm256_set_epi32(constant, constant, constant, constant, constant, constant, constant, constant);

	while(1)
		_mm256_mul_epu32(m1, m2);

	return 0;
}

// Collects traces
static __attribute__((noinline)) int monitor(void *in){
	struct args_t *arg = (struct args_t *)in;
	int attacker_core_id = get_integer_value(config, config_size, "attacker_core_id");

	// Pin monitor to a single CPU
	pin_cpu(attacker_core_id);

	// Set filename
	// The format is, e.g., ./out/all_02_2330.out
	// where 02 is the selector and 2330 is an index to prevent overwriting files
	char output_filename[64];
	sprintf(output_filename, "./out/all_%02lu.out", arg->selector);

	// Prepare output file
	FILE *output_file = fopen((char *)output_filename, "a");
	if (output_file == NULL) {
		perror("output file");
	}

	// Prepare
	double energy, prev_energy = rapl_msr(attacker_core_id, PP0_ENERGY);
	struct freq_sample_t freq_sample, prev_freq_sample = frequency_msr_raw(attacker_core_id);

	// sleep(10);

	// Collect measurements
	for (uint64_t i = 0; i < arg->iters; i++) {

		// Wait before next measurement
		nanosleep((const struct timespec[]){{0, TIME_BETWEEN_MEASUREMENTS}}, NULL);

		// Collect measurement
		energy = rapl_msr(attacker_core_id, PP0_ENERGY);
		freq_sample = frequency_msr_raw(attacker_core_id);

		// Store measurement
		uint64_t aperf_delta = freq_sample.aperf - prev_freq_sample.aperf;
		uint64_t mperf_delta = freq_sample.mperf - prev_freq_sample.mperf;
		uint32_t khz = (maximum_frequency * aperf_delta) / mperf_delta;
		fprintf(output_file, "%.15f %" PRIu32 "\n", energy - prev_energy, khz);

		// Save current
		prev_energy = energy;
		prev_freq_sample = freq_sample;
	}

	// Clean up
	fclose(output_file);
	return 0;
}

// function that gets victim name and returns the function pointer
int (*get_victim(char *victim))(void *){
	for(int i = 0; i < NUM_VICTIMS; i++){
		if(strcmp(victim, victim_names[i]) == 0){
			return victim_functions[i];
		}
	}
	fprintf(stderr, "Victim not found!\n");
	exit(1);
}

void read_args(int argc, char *argv[], int *ntasks, int *outer, int (**victim)(void *), struct args_t *arg){
	// Check arguments
	if (argc != 5) {
		fprintf(stderr, "Wrong Input! Enter: %s <ntasks> <samples> <outer> <victim>\n", argv[0]);
		exit(EXIT_FAILURE);
	}

	// Read in args
	sscanf(argv[1], "%d", ntasks);
	if (ntasks < 0) {
		fprintf(stderr, "ntasks cannot be negative!\n");
		exit(1);
	}
	sscanf(argv[2], "%" PRIu64, &(arg->iters));
	sscanf(argv[3], "%d", outer);
	if (outer < 0) {
		fprintf(stderr, "outer cannot be negative!\n");
		exit(1);
	}
	// get victim function pointer
	*victim = get_victim(argv[4]);
}

int main(int argc, char *argv[])
{
	// initialize variable to hold victim function pointer
	int (*victim)(void *) = NULL;
	int ntasks, outer;
	struct args_t arg;
	parse_config_file(CONFIG_FILE_NAME, config, &config_size);
	int attacker_core_id = get_integer_value(config, config_size, "attacker_core_id");
	read_args(argc, argv, &ntasks, &outer, &victim, &arg);

	uint64_t selectors[100];
	int num_selectors = read_selectors(selectors);

	// Set the scheduling priority to high to avoid interruptions
	// (lower priorities cause more favorable scheduling, and -20 is the max)
	setpriority(PRIO_PROCESS, 0, -20);

	// Prepare up monitor/attacker
	set_frequency_units(attacker_core_id);
	frequency_msr_raw(attacker_core_id);
	set_rapl_units(attacker_core_id);
	rapl_msr(attacker_core_id, PP0_ENERGY);

	// Allocate memory for the threads
	char *tstacks = mmap(NULL, (ntasks + 1) * STACK_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);

	// Run experiment once for each selector
	for (int i = 0; i < outer * num_selectors; i++) {

		if(i % outer == 0)
			printf("iteration %d/%d\n", i/outer, num_selectors - 1);

		// Set alternating selector
		arg.selector = selectors[i % num_selectors];
		printf("selector: %lu\n", arg.selector);

		// Start victim threads
		int tids[ntasks];
		for (int tnum = 0; tnum < ntasks; tnum++) {
			tids[tnum] = clone(victim, tstacks + (ntasks - tnum) * STACK_SIZE, CLONE_VM | SIGCHLD, &arg);
		}

		// Start the monitor thread
		clone(&monitor, tstacks + (ntasks + 1) * STACK_SIZE, CLONE_VM | SIGCHLD, (void *)&arg);

		// Join monitor thread
		wait(NULL);

		// Kill victim threads
		for (int tnum = 0; tnum < ntasks; tnum++) {
			syscall(SYS_tgkill, tids[tnum], tids[tnum], SIGTERM);

			// Need to join o/w the threads remain as zombies
			// https://askubuntu.com/a/427222/1552488
			wait(NULL);
		}
	}

	// Clean up
	munmap(tstacks, (ntasks + 1) * STACK_SIZE);
}


/*
Pseudo code of whats happening:
for i in range(outer):
	for j in range(num_selectors):
		selector = selectors[j]
		for k in range(ntasks):
			victim(selector)
		monitor() # monitor is on core 0 and measures the energy and frequency for {samples} samples
		wait for monitor to finish and kill all victim threads
*/

