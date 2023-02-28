#include "victim-utils.h"
#include "configuration-utils.h"
#include "util.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <immintrin.h>
#include <unistd.h>

char *victim_names[] = {"nop", "shl", "imul", "avx256mul", "avx512mul", "pclmul512"};
// array of victim functions
int (*victim_functions[])(void *) = {nop_victim, shl_victim, imul_victim, avx256_mul_victim, avx512_mul_victim, pclmul512_victim};

__attribute__((noinline, optimize("-O0"))) int nop_victim(void *varg){
	if(get_integer_value(config, config_size, "num_threads") == 1)
		pin_cpu(get_integer_value(config, config_size, "attacker_core_id"));

	asm volatile(
		".align 64\t\n"
		"loopnop:\n\t"

		"nop\t\n"
		"nop\t\n"

		"jmp loopnop\t\n"
		: 
		: 
		:);

	return 0;
}

__attribute__((noinline, optimize("-O0"))) int shl_victim(void *varg){
	if(get_integer_value(config, config_size, "num_threads") == 1)
		pin_cpu(get_integer_value(config, config_size, "attacker_core_id"));
	struct args_t *arg = varg;
	uint32_t count = arg->selector;
	uint64_t result;
	// printf("shl: pid %d\n", getpid());

	asm volatile(
		".align 64\t\n"
		"loopshl:\n\t"

		"shl $31, %[c]\t\n"

		"jmp loopshl\t\n"
		: [r] "=a" (result)
		: [c] "c" (count)
		:);


	return 0;
}

__attribute__((noinline, optimize("-O0"))) int imul_victim(void *varg){
	if(get_integer_value(config, config_size, "num_threads") == 1)
		pin_cpu(get_integer_value(config, config_size, "attacker_core_id"));
	struct args_t *arg = varg;
	uint32_t count = arg->selector;
	uint64_t result;
	// printf("imul: pid %d\n", getpid());

	int seconds = get_integer_value(config, config_size, "samples") / 1000;
	time_t start_time = time(NULL);
    while (1) {
		result = count * count;
	}
	printf("%ld\n", result);

	// asm volatile(
	// 	".align 64\t\n"
	// 	"loopimul:\n\t"

	// 	"imul %[c], %[c]\t\n"
	// 	"imul %[c], %[c]\t\n"
	// 	"imul %[c], %[c]\t\n"
	// 	"imul %[c], %[c]\t\n"
	// 	"imul %[c], %[c]\t\n"

	// 	"jmp loopimul\t\n"
	// 	: [r] "=a" (result)
	// 	: [c] "r" (count)
	// 	:);

	return 0;
}

__attribute__((noinline, optimize("-O0"))) int avx256_mul_victim(void *varg){
	if(get_integer_value(config, config_size, "num_threads") == 1)
		pin_cpu(get_integer_value(config, config_size, "attacker_core_id"));
	struct args_t *arg = varg;
	int count = (int)arg->selector;
	__m256i m1 = _mm256_set_epi32(count, count, count, count, count, count, count, count);
	__m256i m2 = _mm256_set_epi32(count, count, count, count, count, count, count, count);
	__m256i volatile result;
	volatile int flag = 1;
	// printf("avx256mul: pid %d\n", getpid());

	char *constant_str = get_value(config, config_size, "constant");
	if(strcmp(constant_str, "None") != 0){
		int constant = atoi(constant_str);
		m2 = _mm256_set_epi32(constant, constant, constant, constant, constant, constant, constant, constant);
	}

	int seconds = get_integer_value(config, config_size, "samples") / 1000;
	time_t start_time = time(NULL);
    while (flag) {
		result = _mm256_mul_epu32(m1, m2);
	}
	
	// asm volatile(
	// 	".align 64\t\n"
	// 	"loopavx256mul:\n\t"

	// 	"vpmuludq %1, %2, %0\t\n"

	// 	"jmp loopavx256mul\t\n"
    //     : "=x" (result) // output operand
    //     : "x" (m1), "x" (m2) // input operands
    // );
	
	printf("%lld", result[0]);
	return 0;
}

__attribute__((noinline)) int avx512_mul_victim(void *varg){
	// if(get_integer_value(config, config_size, "num_threads") == 1)
	// 	pin_cpu(get_integer_value(config, config_size, "attacker_core_id"));
	// struct args_t *arg = varg;
	// uint64_t count = (int)arg->selector;

	// __m512i m1 = _mm512_set_epi64(count, count, count, count, count, count, count, count);
	// __m512i m2 = _mm512_set_epi64(count, count, count, count, count, count, count, count);
	// __m512i volatile result;

	// char *constant_str = get_value(config, config_size, "constant");
	// if(strcmp(constant_str, "None") != 0){
	// 	int constant = atoi(constant_str);
	// 	m2 = _mm512_set_epi64(constant, constant, constant, constant, constant, constant, constant, constant);
	// }

	// // print_m512i_binary(_mm512_mullo_epi64(m1, m2));

	// while(1)
	// 	result = _mm512_mullo_epi64(m1, m2);

	// m1 = result;
	return 0;
}

__attribute__((noinline)) int pclmul512_victim(void *varg){
	// if(get_integer_value(config, config_size, "num_threads") == 1)
	// 	pin_cpu(get_integer_value(config, config_size, "attacker_core_id"));
	// struct args_t *arg = varg;
	// uint64_t count = (int)arg->selector;
	// printf("pclmul512\n");

	// __m512i m1 = _mm512_set_epi64(count, count, count, count, count, count, count, count);
	// __m512i m2 = _mm512_set_epi64(count, count, count, count, count, count, count, count);
	// __m512i volatile result;

	// char *constant_str = get_value(config, config_size, "constant");
	// if(strcmp(constant_str, "None") != 0){
	// 	int constant = atoi(constant_str);
	// 	m2 = _mm512_set_epi64(constant, constant, constant, constant, constant, constant, constant, constant);
	// }

	// while(1)
	// 	result = _mm512_clmulepi64_epi128(m1, m2, 0);

	// m1 = result;
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

void print_m256i(__m256i vec, char sign) {
	int values[8];
    _mm256_storeu_si256((__m256i *)values, vec);
    for (int i = 7; i >= 0; i--){
    	if (sign)
			printf("%d ", values[i]);
		else
			printf("%u ", values[i]);
	}

    printf("\n");
}

void print_m256i_binary(__m256i vec) {
	int i, j, arr[8];
	_mm256_storeu_si256((__m256i*)arr, vec);
	for (i = 7; i >= 0; i--) {
	for (j = 31; j >= 0; j--)
		printf("%u", (arr[i] >> j) & 1);

	printf(" ");
	}
	printf("\n");
}
