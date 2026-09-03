/* _GNU_SOURCE comes from CFLAGS; clone(2) and tgkill need it. */
#include <errno.h>
#include <sched.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

#include "victim-pool.h"

#define STACK_SIZE 65536

void victims_spawn(struct victim_pool_t *p, struct ctl_t *ctl,
		   int (*fn)(void *), int n,
		   int core_start, int core_stride, int max_core)
{
	p->ctl = ctl;
	p->n = n;
	p->stack_bytes = (size_t)(n + 1) * STACK_SIZE;
	p->stacks = mmap(NULL, p->stack_bytes, PROT_READ | PROT_WRITE,
			 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	p->vargs = calloc(n, sizeof(*p->vargs));
	p->tids = calloc(n, sizeof(*p->tids));
	if (p->stacks == MAP_FAILED || !p->vargs || !p->tids) {
		fprintf(stderr, "victims_spawn: out of memory\n");
		exit(EXIT_FAILURE);
	}

	for (int i = 0; i < n; i++) {
		p->vargs[i].ctl = ctl;
		p->vargs[i].core_id = core_start + i * core_stride;

		if (p->vargs[i].core_id > max_core) {
			fprintf(stderr,
				"Victim %d would land on core %d, past --max-victim-core %d.\n"
				"Reduce --threads or raise the limit.\n",
				i, p->vargs[i].core_id, max_core);
			exit(EXIT_FAILURE);
		}

		/* Each victim gets its own args struct: the shared one the old
		 * driver reused was mutated between clones and only worked
		 * because of the sleep that followed. */
		p->tids[i] = clone(fn, p->stacks + (size_t)(i + 1) * STACK_SIZE,
				   CLONE_VM | SIGCHLD, &p->vargs[i]);
		if (p->tids[i] < 0) {
			fprintf(stderr, "clone victim %d: %s\n", i, strerror(errno));
			exit(EXIT_FAILURE);
		}
	}
}

void victims_stop(struct victim_pool_t *p)
{
	p->ctl->run = 0;
	__sync_synchronize();

	/* Victims poll ctl->run once per burst (sub-microsecond), so this is
	 * normally immediate; the kill is only a backstop. */
	for (int waited = 0; waited < 200; waited++) {
		int alive = 0;
		for (int i = 0; i < p->n; i++) {
			if (p->tids[i] > 0 && waitpid(p->tids[i], NULL, WNOHANG) == 0)
				alive++;
			else
				p->tids[i] = -1;
		}
		if (!alive)
			goto done;
		usleep(10000);
	}

	for (int i = 0; i < p->n; i++) {
		if (p->tids[i] > 0) {
			syscall(SYS_tgkill, p->tids[i], p->tids[i], SIGTERM);
			waitpid(p->tids[i], NULL, 0);
		}
	}

done:
	free(p->tids);
	p->tids = NULL;
	munmap(p->stacks, p->stack_bytes);
	p->stacks = NULL;
	/* vargs outlives the threads: the caller still reads burst counts. */
}

uint64_t victims_bursts(const struct victim_pool_t *p)
{
	uint64_t total = 0;
	for (int i = 0; i < p->n; i++)
		total += p->vargs[i].bursts;
	return total;
}
