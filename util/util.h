#ifndef _MY_UTIL_H
#define _MY_UTIL_H

#include <assert.h>
#include <inttypes.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <x86intrin.h>

#define MAX_SELECTORS 128

enum sample_mode {
	SAMPLE_EDGE = 0,	/* one record per RAPL counter update */
	SAMPLE_FIXED = 1	/* legacy: one record per fixed busy-wait window */
};

/*
 * Control block shared between the driver and every victim thread.
 *
 * Victims are cloned with CLONE_VM, so they share the driver's address space.
 * Writing `selector` here re-tunes every running victim within one burst
 * (~0.6us) with no thread teardown, which is what makes condition
 * interleaving cheap enough to cancel thermal drift. It is also exactly the
 * modulation primitive the covert-channel transmitter needs.
 */
struct ctl_t {
	volatile uint64_t selector;
	volatile uint64_t epoch;	/* bumped on every selector write */
	volatile int run;
};

/*
 * One cache line each: victims write `bursts` continuously, and packing
 * several into a line would make them fight over it.
 *
 * `bursts` and `bytes_per_burst` let the driver report actual operand
 * throughput, which turns a watt difference into leakage per byte moved --
 * the quantity the traffic-volume sweep is really after.
 */
struct victim_args_t {
	struct ctl_t *ctl;
	int core_id;
	volatile uint64_t bursts;
	uint64_t bytes_per_burst;
} __attribute__((aligned(64)));

struct run_config_t {
	int nthreads;
	uint64_t samples_per_block;
	int blocks_per_condition;
	int settle_samples;
	int attacker_core;
	int victim_core_start;
	int victim_core_stride;
	int max_victim_core;
	int mode;
	int sequential;		/* 1 = deliberately reproduce the old confound */
	uint64_t seed;
	uint64_t fixed_cycles;
	const char *victim_name;
	const char *input_path;
	const char *out_path;
	int (*victim_func)(void *);
};

uint64_t get_time(void);

void pin_cpu(size_t core_ID);

/*
 * Pin a victim and arm PR_SET_PDEATHSIG.
 *
 * Victims are cloned with CLONE_VM, so they share ctl->run with the driver.
 * If the driver dies without clearing it -- a crash, a kill, a failed test --
 * the address space survives in the children and they spin forever at 100%
 * CPU on their pinned cores. Orphans like that silently corrupt every later
 * measurement, so the kernel is made to reap them with the parent.
 */
void victim_pin(int core_ID);

/* Reads newline-separated unsigned selectors; returns the count, bounded by max. */
int read_selectors(const char *path, uint64_t *selectors, int max);

void parse_args(int argc, char *argv[], struct run_config_t *cfg);

/* Deterministic PRNG so a run's block order is reproducible from its seed. */
uint64_t xorshift64(uint64_t *state);
void shuffle_u32(uint32_t *a, int n, uint64_t *state);

#endif
