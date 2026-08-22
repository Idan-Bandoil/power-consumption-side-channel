#ifndef VICTIM_UTILS_H
#define VICTIM_UTILS_H

#include <stdio.h>

/*
 * Victims are looked up by name from a single table in victim-utils.c, so
 * adding one is a single edit there. Every victim runs a tight inline-asm
 * loop and re-reads ctl->selector between bursts, which lets the driver
 * change the tested operand live instead of respawning threads.
 */
int (*get_victim(const char *name))(void *);

void list_victims(FILE *out);

#endif
