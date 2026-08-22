/* Non-root smoke test for the victim mechanism.
 *
 * Checks the three things Phase 0 depends on and that the MSR path would
 * otherwise hide: victims actually spin, a live selector write reaches them,
 * and clearing ctl->run makes them exit on their own.
 *
 * Needs no root, so it covers ground the MSR-gated driver cannot reach on a
 * developer machine. It is how the AVX-VNNI SIGILL was found: gas defaults
 * vpdpbusd to its EVEX (AVX-512) encoding, which is illegal on Alder Lake.
 *
 *   cd src && make check          # every victim
 *   cd src && ./bin/smoke avx2_mul
 */
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include "util.h"
#include "victim-utils.h"

#define STACK_SIZE 65536

static double now(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec + t.tv_nsec / 1e9;
}

/* Victims are cloned with SIGCHLD, so they are separate processes and their
 * CPU time never lands in the parent's CLOCK_PROCESS_CPUTIME_ID. Read it per
 * task from /proc instead: field 14 (utime) in ticks. */
static double task_cpu_seconds(int tid)
{
	char path[64], buf[1024];
	snprintf(path, sizeof(path), "/proc/%d/stat", tid);
	FILE *f = fopen(path, "r");
	if (!f)
		return -1.0;
	size_t n = fread(buf, 1, sizeof(buf) - 1, f);
	fclose(f);
	buf[n] = '\0';

	/* comm may contain spaces; start after the closing ')'. */
	char *p = strrchr(buf, ')');
	if (!p)
		return -1.0;
	p += 2;
	long utime = 0, stime = 0;
	int field = 3;
	for (char *tok = strtok(p, " "); tok; tok = strtok(NULL, " "), field++) {
		if (field == 14) utime = atol(tok);
		if (field == 15) { stime = atol(tok); break; }
	}
	return (double)(utime + stime) / sysconf(_SC_CLK_TCK);
}

int main(int argc, char **argv)
{
	const char *name = argc > 1 ? argv[1] : "avx2_mul";
	int nthreads = 2;
	int (*fn)(void *) = get_victim(name);

	char *stacks = mmap(NULL, (size_t)(nthreads + 1) * STACK_SIZE,
			    PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	struct ctl_t *ctl = calloc(1, sizeof(*ctl));
	struct victim_args_t *va = calloc(nthreads, sizeof(*va));
	int tids[8];

	ctl->selector = 0;
	ctl->run = 1;

	for (int i = 0; i < nthreads; i++) {
		va[i].ctl = ctl;
		va[i].core_id = 2 + i * 2;
		tids[i] = clone(fn, stacks + (size_t)(i + 1) * STACK_SIZE,
				CLONE_VM | SIGCHLD, &va[i]);
		if (tids[i] < 0) { perror("clone"); return 1; }
	}

	double w0 = now();
	double c0[8];
	for (int i = 0; i < nthreads; i++)
		c0[i] = task_cpu_seconds(tids[i]);
	usleep(300000);

	/* Live retune: no teardown, no respawn. */
	ctl->selector = 0xFFFFFFFFu;
	__sync_synchronize();
	ctl->epoch++;
	usleep(300000);

	double wall = now() - w0, busy = 0;
	int alive = 0;
	for (int i = 0; i < nthreads; i++) {
		double c = task_cpu_seconds(tids[i]);
		if (c >= 0) { alive++; busy += c - c0[i]; }
	}

	printf("victim              : %s x%d\n", name, nthreads);
	printf("threads alive       : %d/%d\n", alive, nthreads);
	printf("victim CPU / wall   : %.2f  (expect ~%d)\n", busy / wall, nthreads);
	printf("selector now        : %lu (epoch %lu)\n",
	       (unsigned long)ctl->selector, (unsigned long)ctl->epoch);

	double t_stop = now();
	ctl->run = 0;
	__sync_synchronize();
	int clean = 1;
	for (int i = 0; i < nthreads; i++) {
		int st = 0;
		waitpid(tids[i], &st, 0);
		if (!WIFEXITED(st) || WEXITSTATUS(st) != 0) {
			clean = 0;
			printf("  thread %d abnormal exit: status 0x%x%s\n", i, st,
			       WIFSIGNALED(st) ? " (signalled)" : "");
		}
	}
	double exit_ms = (now() - t_stop) * 1000.0;
	printf("exit latency        : %.2f ms\n", exit_ms);

	int ok = alive == nthreads && busy / wall > nthreads * 0.8
		 && exit_ms < 50.0 && clean;
	printf("RESULT              : %s\n", ok ? "PASS" : "FAIL");
	return ok ? 0 : 1;
}
