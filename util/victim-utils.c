#include "victim-utils.h"
#include "configuration-utils.h"
#include "util.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <immintrin.h>

char *victim_names[] = {"imul", "avx_mul"};
// array of victim functions
int (*victim_functions[])(void *) = {imul_victim, avx_mul_victim};

__attribute__((noinline)) int imul_victim(void *varg){
	if(get_integer_value(config, config_size, "num_threads") == 1)
		pin_cpu(get_integer_value(config, config_size, "attacker_core_id"));
	struct args_t *arg = varg;
	uint32_t count = arg->selector;
	uint64_t result = 0;
	// printf("imul_victim\n");

	while(1){
		result = count * count;
	}
	printf("%ld\n", result);

	// asm volatile(
	// 	".align 64\t\n"
	// 	"loopimul:\n\t"

	// 	"imul $0xffffffffffffffff, %0, %%r13\n\t"
	// 	"imul $0xffffffffffffffff, %0, %%r13\n\t"
	// 	"imul $0xffffffffffffffff, %0, %%r13\n\t"
	// 	"imul $0xffffffffffffffff, %0, %%r13\n\t"
	// 	"imul $0xffffffffffffffff, %0, %%r13\n\t"
	// 	"imul $0xffffffffffffffff, %0, %%r13\n\t"
	// 	"imul $0xffffffffffffffff, %0, %%r13\n\t"
	// 	"imul $0xffffffffffffffff, %0, %%r13\n\t"

	// 	"jmp loopimul\n\t"
	// 	: 
	// 	: "r"(count)
	// 	: "r13");

	return 0;
}

__attribute__((noinline)) int avx_mul_victim(void *varg){
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