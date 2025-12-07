#include <immintrin.h>

#ifndef VICTIM_UTILS_H
#define VICTIM_UTILS_H

#define NUM_VICTIMS 5

__attribute__((noinline)) int nop_victim(void *varg);

__attribute__((noinline)) int shl_victim(void *varg);

__attribute__((noinline)) int imul_victim(void *varg);

__attribute__((noinline)) int avx256_mul_victim(void *varg);

__attribute__((noinline)) int fma_victim(void *varg);

int (*get_victim(char *victim))(void *);

void print_m256i(__m256i vec, char sign);

void print_m256i_binary(__m256i vec);

#endif