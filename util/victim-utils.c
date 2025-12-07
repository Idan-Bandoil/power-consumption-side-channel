#include "victim-utils.h"
#include "util.h"
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <string.h>
#include <immintrin.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/wait.h>

char *victim_names[] = {"nop", "shl", "imul", "avx256mul", "fma"};
// array of victim functions
int (*victim_functions[])(void *) = {nop_victim, shl_victim, imul_victim, avx256_mul_victim, fma_victim};

__attribute__((noinline)) int nop_victim(void *varg){
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

__attribute__((noinline)) int shl_victim(void *varg){
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

__attribute__((noinline)) int imul_victim(void *varg){
	struct args_t *arg = varg;
	uint32_t count = arg->selector;
	uint64_t result;
	// printf("imul: pid %d\n", getpid());

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

__attribute__((noinline)) 
int avx256_mul_victim(
	void *varg
){
	struct args_t *arg = varg;
	int count = (int)arg->selector;
	int target_core_id = arg->target_core_id;

	// **FORCE PINNING IMMEDIATELY**
    pin_cpu(target_core_id);
    sched_yield(); // Give the OS a hint to switch contexts

	__m256i m1 = _mm256_set_epi32(count, count, count, count, count, count, count, count);
	__m256i m2 = _mm256_set_epi32(count, count, count, count, count, count, count, count);
	__m256i volatile result;
	volatile int flag = 1;

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

__attribute__((noinline)) int fma_victim(void *varg){
	struct args_t *arg = varg;
	int count = (int)arg->selector, constant;
	double result;
	volatile int flag = 1;

    while (flag) {
		result = fma(count, count, count);
	}

	printf("%f\n", result);
	
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
