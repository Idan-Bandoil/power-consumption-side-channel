#ifndef VICTIM_UTILS_H
#define VICTIM_UTILS_H

#define NUM_VICTIMS 2

int imul_victim(void *varg);

int avx_mul_victim(void *varg);

int (*get_victim(char *victim))(void *);

#endif