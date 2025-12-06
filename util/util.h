#ifndef _MY_UTIL_H
#define _MY_UTIL_H

#include <assert.h>
#include <inttypes.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <x86intrin.h>

struct args_t {
	uint64_t iters;
	uint64_t selector;
	int target_core_id;
};

uint64_t get_time(void);

void pin_cpu(size_t core_ID);

int read_selectors(uint64_t *selectors);

void read_args(int argc, char *argv[], int *ntasks, int *outer, int (**victim)(void *), char **victim_name, struct args_t *arg);

void strip(char *str);

#endif
