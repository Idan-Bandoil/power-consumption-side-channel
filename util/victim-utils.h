#include <immintrin.h>

#ifndef VICTIM_UTILS_H
#define VICTIM_UTILS_H

#define NUM_VICTIMS 2

int imul_victim(void *varg);

int avx_mul_victim(void *varg);

int (*get_victim(char *victim))(void *);

void print_m256i(__m256i vec, char sign);

void print_m256i_binary(__m256i vec);

#endif