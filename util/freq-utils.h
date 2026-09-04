#ifndef _FREQ_UTILS_H
#define _FREQ_UTILS_H

#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <x86intrin.h>

struct freq_sample_t {
	uint64_t aperf;
	uint64_t mperf;
};

extern unsigned int maximum_frequency;

int set_frequency_units(int core_ID);
struct freq_sample_t frequency_msr_raw(int core_ID);
uint32_t frequency_msr(int core_ID);

/*
 * One-shot read of scaling_cur_freq, in kHz. Opens and closes the file each
 * call, so it is fine for a diagnostic and wrong for a sampling loop -- use
 * the fd-based pair below there.
 */
uint32_t frequency_cpufreq(int cpu_id);

/*
 * scaling_cur_freq for a polling receiver. World-readable, so this is the
 * unprivileged path into the frequency channel; the MSR functions above all
 * need root.
 *
 * Split into open and read because the read happens in a tight loop: holding
 * the descriptor open and preading it keeps a sample down to one syscall,
 * where reopening the file per sample would cost several and put the sampling
 * rate at the mercy of the path walk.
 */
int cpufreq_open(int cpu_id);
uint32_t cpufreq_read(int fd);

/*
 * As cpufreq_open, but returns -1 rather than exiting when the file is
 * missing. The driver reads a victim core's frequency as a per-run validity
 * check and not as data, so a machine without cpufreq should lose the check,
 * not the run.
 */
int cpufreq_try_open(int cpu_id);

#endif