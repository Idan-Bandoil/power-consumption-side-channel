#include "util.h"
#include "victim-utils.h"
#include <ctype.h>
#include <string.h>

/*
 * Gets the value Time Stamp Counter
 */
uint64_t get_time(void)
{
	uint64_t cycles;
	asm volatile("rdtscp\n\t"
				 "shl $32, %%rdx\n\t"
				 "or %%rdx, %0\n\t"
				 : "=a"(cycles)
				 :
				 : "rcx", "rdx", "memory");

	return cycles;
}

void pin_cpu(size_t core_ID)
{
	cpu_set_t set;
	CPU_ZERO(&set);
	CPU_SET(core_ID, &set);
	if (sched_setaffinity(0, sizeof(cpu_set_t), &set) < 0) {
		printf("Unable to Set Affinity\n");
		exit(EXIT_FAILURE);
	}
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

void strip(char *str) {
  int i, j;
  int len = strlen(str);
  for (i = 0, j = 0; i < len; i++) {
    if (!isspace(str[i])) {
      str[j++] = str[i];
    }
  }
  str[j] = '\0';
}