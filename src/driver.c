#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <immintrin.h>

#include "../util/freq-utils.h"
#include "../util/rapl-utils.h"
#include "../util/util.h"

volatile static int attacker_core_ID = 0;
static int NUM_VICTIMS = 2;
static char *victim_names[] = {"mul", "avx_mul"};
// declare victim functions
static int mul_victim(void *varg);
static int avx_mul_victim(void *varg);
// array of victim functions
static int (*victim_functions[])(void *) = {mul_victim, avx_mul_victim};
static int constant = 2863311530; // 0xAAAAAAAA

#define TIME_BETWEEN_MEASUREMENTS 1000000L // 1 millisecond

#define STACK_SIZE 8192

#define INT_LEN 32

struct args_t {
	uint64_t iters;
	uint64_t selector;
};

void print_bits(int n){
	for(int i = INT_LEN - 1; i >= 0; i--){
		printf("%d", (n >> i) & 1);
	}
	printf("\n");
}

void print_float_double_binary(float f){
 unsigned long *float_as_int = (unsigned long *)&f;
 int i;

 for (i=0; i<=31; i++)
   {
    if (i==1)
      printf(" "); // Space after sign field
    if (i==9)
      printf(" "); // Space after exponent field

    if ((*float_as_int >> (31-i)) & 1)
      printf("1");
    else
      printf("0");
   }
 printf("\n");
}

static __attribute__((noinline)) int mul_victim(void *varg){
	struct args_t *arg = varg;
	uint64_t count = arg->selector;
	// printf("mul_victim\n");
	uint64_t result;

	while(1)
		result = count * count;

	// asm volatile(
	// 	".align 64\t\n"
	// 	"loopmul:\n\t"

	// 	"imul $0xffffffffffffffff, %0, %%r13\n\t"
	// 	"imul $0xffffffffffffffff, %0, %%r13\n\t"
	// 	"imul $0xffffffffffffffff, %0, %%r13\n\t"
	// 	"imul $0xffffffffffffffff, %0, %%r13\n\t"
	// 	"imul $0xffffffffffffffff, %0, %%r13\n\t"
	// 	"imul $0xffffffffffffffff, %0, %%r13\n\t"
	// 	"imul $0xffffffffffffffff, %0, %%r13\n\t"
	// 	"imul $0xffffffffffffffff, %0, %%r13\n\t"

	// 	"jmp loopmul\n\t"
	// 	: 
	// 	: "r"(count)
	// 	: "r13");

	return 0;
}

static __attribute__((noinline)) int avx_mul_victim(void *varg){
	// pin_cpu(attacker_core_ID);
	struct args_t *arg = varg;
	int count = (int)arg->selector;
	// printf("avx_mul_victim\n");

	__m256i m1 = _mm256_set_epi32(count, count, count, count, count, count, count, count);
	// __m256i m_const = _mm256_set_epi32(constant, constant, constant, constant, constant, constant, constant, constant);

	while(1){
		_mm256_mul_epu32(m1, m1);
	}

	return 0;
}

// Collects traces
static __attribute__((noinline)) int monitor(void *in){
	struct args_t *arg = (struct args_t *)in;

	// Pin monitor to a single CPU
	pin_cpu(attacker_core_ID);

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
	double energy, prev_energy = rapl_msr(attacker_core_ID, PP0_ENERGY);
	struct freq_sample_t freq_sample, prev_freq_sample = frequency_msr_raw(attacker_core_ID);

	// sleep(10);

	// Collect measurements
	for (uint64_t i = 0; i < arg->iters; i++) {

		// Wait before next measurement
		nanosleep((const struct timespec[]){{0, TIME_BETWEEN_MEASUREMENTS}}, NULL);

		// Collect measurement
		energy = rapl_msr(attacker_core_ID, PP0_ENERGY);
		freq_sample = frequency_msr_raw(attacker_core_ID);

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

// this functin should get a parameter to put the victim function pointer in
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

int read_selectors(uint64_t *selectors){
	// Open the selector file
	FILE *selectors_file = fopen("input.txt", "r");
	if (selectors_file == NULL)
		perror("fopen error");

	// Read the selectors file line by line
	int num_selectors = 0;
	size_t len = 0;
	ssize_t read = 0;
	char *line = NULL;
	while ((read = getline(&line, &len, selectors_file)) != -1) {
		if (line[read - 1] == '\n')
			line[--read] = '\0';

		// Read selector
		sscanf(line, "%lu", &(selectors[num_selectors]));
		num_selectors += 1;
	}

	// Clean up
	fclose(selectors_file);

	return num_selectors;
}

int main(int argc, char *argv[])
{
	// initialize variable to hold victim function pointer
	int (*victim)(void *) = NULL;
	int ntasks, outer;
	struct args_t arg;
	read_args(argc, argv, &ntasks, &outer, &victim, &arg);

	uint64_t selectors[100];
	int num_selectors = read_selectors(selectors);

	// Set the scheduling priority to high to avoid interruptions
	// (lower priorities cause more favorable scheduling, and -20 is the max)
	setpriority(PRIO_PROCESS, 0, -20);

	// Prepare up monitor/attacker
	set_frequency_units(attacker_core_ID);
	frequency_msr_raw(attacker_core_ID);
	set_rapl_units(attacker_core_ID);
	rapl_msr(attacker_core_ID, PP0_ENERGY);

	// Allocate memory for the threads
	char *tstacks = mmap(NULL, (ntasks + 1) * STACK_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);

	// Run experiment once for each selector
	for (int i = 0; i < outer * num_selectors; i++) {

		if(i % outer == 0)
			printf("iteration %d/%d\n", i/outer, num_selectors - 1);

		// Set alternating selector
		arg.selector = selectors[i % num_selectors];
		printf("selector: %lu\n", arg.selector);
		print_bits(arg.selector);

		// Start mul_victim threads
		int tids[ntasks];
		for (int tnum = 0; tnum < ntasks; tnum++) {
			tids[tnum] = clone(victim, tstacks + (ntasks - tnum) * STACK_SIZE, CLONE_VM | SIGCHLD, &arg);
		}

		// Start the monitor thread
		clone(&monitor, tstacks + (ntasks + 1) * STACK_SIZE, CLONE_VM | SIGCHLD, (void *)&arg);

		// Join monitor thread
		wait(NULL);

		// Kill mul_victim threads
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
			mul_victim(selector)
		monitor() # monitor is on core 0 and measures the energy and frequency for {samples} samples
		wait for monitor to finish and kill all mul_victim threads
*/

