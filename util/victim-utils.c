#include "victim-utils.h"
#include "util.h"

#include <immintrin.h>
#include <sys/mman.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/*
 * Iterations of the 8-wide unrolled body between selector re-reads.
 *
 * One burst is 8 * AVX_BURST instructions, roughly 2000 cycles, so the
 * re-read costs well under 1% of the loop and the driver can switch the
 * tested operand in under a microsecond -- three orders of magnitude below
 * the ~1ms RAPL update period we sample at.
 */
#define AVX_BURST 512

#define STR_(x) #x
#define STR(x) STR_(x)

/*
 * One accounting step per burst, shared by every victim body.
 *
 * The burst count is the throughput figure. The epoch check is the receiving
 * end of the driver's and transmitter's selector writes: it counts how many
 * distinct epochs this victim actually observed, which is the only evidence
 * that a modulation reached the die rather than merely being scheduled. Both
 * fields live in the victim's own cache line, and ctl->epoch shares a line
 * with ctl->selector, which the victim is about to read anyway -- so this
 * costs a compare against a register.
 */
#define VICTIM_TICK(a, ctl)                                                   \
	do {                                                                  \
		(a)->bursts++;                                                \
		uint64_t ep_ = (ctl)->epoch;                                  \
		if (ep_ != (a)->last_epoch) {                                 \
			(a)->last_epoch = ep_;                                \
			(a)->epochs_seen++;                                   \
		}                                                             \
	} while (0)

#define YMM_CLOBBERS \
	"ymm0", "ymm1", "ymm2", "ymm3", "ymm4", \
	"ymm5", "ymm6", "ymm7", "ymm8", "ymm9"

/*
 * Eight independent destinations so the loop is throughput-bound on the
 * vector ports rather than serialised by result latency.
 */
#define UNROLL8_3OP(insn, R) \
	insn " %%" R "1, %%" R "0, %%" R "2\n\t" \
	insn " %%" R "1, %%" R "0, %%" R "3\n\t" \
	insn " %%" R "1, %%" R "0, %%" R "4\n\t" \
	insn " %%" R "1, %%" R "0, %%" R "5\n\t" \
	insn " %%" R "1, %%" R "0, %%" R "6\n\t" \
	insn " %%" R "1, %%" R "0, %%" R "7\n\t" \
	insn " %%" R "1, %%" R "0, %%" R "8\n\t" \
	insn " %%" R "1, %%" R "0, %%" R "9\n\t"

#define UNROLL8_2OP(insn, R) \
	insn " %%" R "0, %%" R "2\n\t" \
	insn " %%" R "0, %%" R "3\n\t" \
	insn " %%" R "0, %%" R "4\n\t" \
	insn " %%" R "0, %%" R "5\n\t" \
	insn " %%" R "0, %%" R "6\n\t" \
	insn " %%" R "0, %%" R "7\n\t" \
	insn " %%" R "0, %%" R "8\n\t" \
	insn " %%" R "0, %%" R "9\n\t"

/* Accumulating instructions (FMA, VNNI) read their destination. */
#define ZERO_DESTS \
	"vpxor %%ymm2, %%ymm2, %%ymm2\n\t" \
	"vpxor %%ymm3, %%ymm3, %%ymm3\n\t" \
	"vpxor %%ymm4, %%ymm4, %%ymm4\n\t" \
	"vpxor %%ymm5, %%ymm5, %%ymm5\n\t" \
	"vpxor %%ymm6, %%ymm6, %%ymm6\n\t" \
	"vpxor %%ymm7, %%ymm7, %%ymm7\n\t" \
	"vpxor %%ymm8, %%ymm8, %%ymm8\n\t" \
	"vpxor %%ymm9, %%ymm9, %%ymm9\n\t"

#define NO_ZERO ""

/*
 * Both operand registers are loaded with the same value, so for a given
 * instruction the only thing varying across conditions is the bit pattern
 * under test. Note the deliberate contrast this creates: vpand/vpor are
 * identity on equal inputs (result Hamming weight tracks the operand),
 * while vpxor always yields zero (result Hamming weight is pinned at 0).
 * Comparing them separates input-driven from output-driven leakage.
 */
#define DEFINE_VEC_VICTIM(fname, unroll, insn, R, zero)                       \
	static __attribute__((noinline)) int fname(void *varg)                \
	{                                                                     \
		struct victim_args_t *a = varg;                               \
		struct ctl_t *ctl = a->ctl;                                   \
		__m256i vec __attribute__((aligned(32)));                     \
		uint64_t cached;                                              \
                                                                              \
		victim_pin(a->core_id);                                          \
		sched_yield();                                                \
                                                                              \
		cached = ctl->selector;                                       \
		vec = _mm256_set1_epi32((int)(uint32_t)cached);               \
                                                                              \
		while (ctl->run) {                                            \
			VICTIM_TICK(a, ctl);                                  \
			uint64_t s = ctl->selector;                           \
			if (s != cached) {                                    \
				cached = s;                                   \
				vec = _mm256_set1_epi32((int)(uint32_t)s);    \
			}                                                     \
			asm volatile(                                         \
				"vmovdqa %[src], %%ymm0\n\t"                  \
				"vmovdqa %%ymm0, %%ymm1\n\t"                  \
				zero                                          \
				"mov $" STR(AVX_BURST) ", %%rcx\n\t"          \
				"1:\n\t"                                      \
				unroll(insn, R)                               \
				"sub $1, %%rcx\n\t"                           \
				"jnz 1b\n\t"                                  \
				"vzeroupper\n\t"                              \
				:                                             \
				: [src] "m" (vec)                             \
				: "rcx", "memory", YMM_CLOBBERS);             \
		}                                                             \
		_exit(0);                                                     \
		return 0;                                                     \
	}

/* ---- Baselines -------------------------------------------------------- */

static __attribute__((noinline)) int idle_victim(void *varg)
{
	struct victim_args_t *a = varg;
	struct ctl_t *ctl = a->ctl;

	victim_pin(a->core_id);

	while (ctl->run) {
		VICTIM_TICK(a, ctl);
		for (int i = 0; i < AVX_BURST * 8; i++)
			_mm_pause();
	}
	_exit(0);
	return 0;
}

static __attribute__((noinline)) int nop_victim(void *varg)
{
	struct victim_args_t *a = varg;
	struct ctl_t *ctl = a->ctl;

	victim_pin(a->core_id);

	while (ctl->run) {
		VICTIM_TICK(a, ctl);
		asm volatile(
			"mov $" STR(AVX_BURST) ", %%rcx\n\t"
			"1:\n\t"
			"nop\n\t" "nop\n\t" "nop\n\t" "nop\n\t"
			"nop\n\t" "nop\n\t" "nop\n\t" "nop\n\t"
			"sub $1, %%rcx\n\t"
			"jnz 1b\n\t"
			: : : "rcx");
	}
	_exit(0);
	return 0;
}

/* ---- Scalar ----------------------------------------------------------- */

/*
 * Rotate rather than shift: rol preserves Hamming weight indefinitely, so
 * this victim varies bit *position* at fixed weight -- the control needed to
 * show that the leakage tracks weight rather than a particular bit lane.
 */
static __attribute__((noinline)) int scalar_rol_victim(void *varg)
{
	struct victim_args_t *a = varg;
	struct ctl_t *ctl = a->ctl;
	uint64_t s;

	victim_pin(a->core_id);

	while (ctl->run) {
		VICTIM_TICK(a, ctl);
		s = ctl->selector;
		asm volatile(
			"mov %[v], %%rax\n\t"
			"mov %[v], %%rdx\n\t"
			"mov %[v], %%rsi\n\t"
			"mov %[v], %%rdi\n\t"
			"mov $" STR(AVX_BURST) ", %%rcx\n\t"
			"1:\n\t"
			"rol $1, %%rax\n\t"
			"rol $1, %%rdx\n\t"
			"rol $1, %%rsi\n\t"
			"rol $1, %%rdi\n\t"
			"rol $1, %%rax\n\t"
			"rol $1, %%rdx\n\t"
			"rol $1, %%rsi\n\t"
			"rol $1, %%rdi\n\t"
			"sub $1, %%rcx\n\t"
			"jnz 1b\n\t"
			:
			: [v] "r" (s)
			: "rcx", "rax", "rdx", "rsi", "rdi");
	}
	_exit(0);
	return 0;
}

static __attribute__((noinline)) int scalar_imul_victim(void *varg)
{
	struct victim_args_t *a = varg;
	struct ctl_t *ctl = a->ctl;
	uint64_t s;

	victim_pin(a->core_id);

	while (ctl->run) {
		VICTIM_TICK(a, ctl);
		s = ctl->selector;
		/* Accumulators are re-seeded every burst so they cannot drift
		 * to an absorbing value and decouple from the selector. */
		asm volatile(
			"mov %[v], %%r8\n\t"
			"mov %[v], %%rax\n\t"
			"mov %[v], %%rdx\n\t"
			"mov %[v], %%rsi\n\t"
			"mov %[v], %%rdi\n\t"
			"mov $" STR(AVX_BURST) ", %%rcx\n\t"
			"1:\n\t"
			"imul %%r8, %%rax\n\t"
			"imul %%r8, %%rdx\n\t"
			"imul %%r8, %%rsi\n\t"
			"imul %%r8, %%rdi\n\t"
			"imul %%r8, %%rax\n\t"
			"imul %%r8, %%rdx\n\t"
			"imul %%r8, %%rsi\n\t"
			"imul %%r8, %%rdi\n\t"
			"sub $1, %%rcx\n\t"
			"jnz 1b\n\t"
			:
			: [v] "r" (s)
			: "rcx", "r8", "rax", "rdx", "rsi", "rdi");
	}
	_exit(0);
	return 0;
}

/* ---- Vector instruction family ---------------------------------------- */

DEFINE_VEC_VICTIM(avx2_mul_victim,  UNROLL8_3OP, "vpmuludq",    "ymm", NO_ZERO)
DEFINE_VEC_VICTIM(avx2_add_victim,  UNROLL8_3OP, "vpaddd",      "ymm", NO_ZERO)
DEFINE_VEC_VICTIM(avx2_and_victim,  UNROLL8_3OP, "vpand",       "ymm", NO_ZERO)
DEFINE_VEC_VICTIM(avx2_or_victim,   UNROLL8_3OP, "vpor",        "ymm", NO_ZERO)
DEFINE_VEC_VICTIM(avx2_xor_victim,  UNROLL8_3OP, "vpxor",       "ymm", NO_ZERO)
DEFINE_VEC_VICTIM(avx2_shift_victim,UNROLL8_3OP, "vpsllvd",     "ymm", NO_ZERO)
DEFINE_VEC_VICTIM(avx2_mov_victim,  UNROLL8_2OP, "vmovdqa",     "ymm", NO_ZERO)
DEFINE_VEC_VICTIM(sse_mul_victim,   UNROLL8_3OP, "vpmuludq",    "xmm", NO_ZERO)
DEFINE_VEC_VICTIM(avx2_fma_victim,  UNROLL8_3OP, "vfmadd231ps", "ymm", ZERO_DESTS)

#ifdef __AVXVNNI__
/*
 * vpdpbusd is the int8 dot-product AVX-VNNI uses; it is what quantised ML
 * inference actually executes on this part, and the reason it is worth
 * characterising separately from plain integer multiply.
 *
 * The {vex} pseudo-prefix is mandatory. The mnemonic exists in both AVX-VNNI
 * (VEX) and AVX512-VNNI (EVEX), and gas defaults to EVEX -- which SIGILLs on
 * Alder Lake, where AVX-512 is fused off. It is spelled %{vex%} because bare
 * braces in an inline-asm template mean dialect alternatives to GCC, which
 * would strip them and leave a bogus 'vex' mnemonic. Check with:
 *     objdump -d util/victim-utils.o | grep vpdpbusd
 * VEX encodings start c4; an EVEX 62 prefix means this got mis-assembled.
 */
DEFINE_VEC_VICTIM(avx2_vnni_victim, UNROLL8_3OP, "%{vex%} vpdpbusd", "ymm", ZERO_DESTS)
#endif

/* ---- Memory-traffic variants ------------------------------------------ *
 *
 * The register-only victims above keep the operand in ymm0/ymm1 for a whole
 * burst, so its bit pattern never crosses a bus. Classical power analysis
 * attributes most data-dependent draw to bus and memory capacitance rather
 * than ALU internals, and the original -O0 victim kept its operands and a
 * volatile result on the stack -- reloading and restoring them every
 * iteration. These variants restore that traffic in a controlled way, as a
 * 2x2 over loads and stores, so the two can be separated:
 *
 *   avx2_mul        register only        (no traffic)
 *   avx2_mul_ld     loads  + multiply
 *   avx2_mul_st     multiply + stores
 *   avx2_mul_ldst   loads + multiply + stores   (closest to the old victim)
 *   avx2_load       loads only, no ALU work
 *
 * Eight independent slots keep the unrolled body free of address conflicts
 * and store-forwarding stalls; 512 bytes stays resident in L1.
 */
#define MEM_SLOTS 16	/* 0-7 source, 8-15 destination */

#define LOAD8 \
	"vmovdqa    0(%%rax), %%ymm0\n\t" \
	"vmovdqa   32(%%rax), %%ymm1\n\t" \
	"vmovdqa   64(%%rax), %%ymm2\n\t" \
	"vmovdqa   96(%%rax), %%ymm3\n\t" \
	"vmovdqa  128(%%rax), %%ymm4\n\t" \
	"vmovdqa  160(%%rax), %%ymm5\n\t" \
	"vmovdqa  192(%%rax), %%ymm6\n\t" \
	"vmovdqa  224(%%rax), %%ymm7\n\t"

#define MUL8 \
	"vpmuludq %%ymm0, %%ymm0, %%ymm8\n\t" \
	"vpmuludq %%ymm1, %%ymm1, %%ymm9\n\t" \
	"vpmuludq %%ymm2, %%ymm2, %%ymm10\n\t" \
	"vpmuludq %%ymm3, %%ymm3, %%ymm11\n\t" \
	"vpmuludq %%ymm4, %%ymm4, %%ymm12\n\t" \
	"vpmuludq %%ymm5, %%ymm5, %%ymm13\n\t" \
	"vpmuludq %%ymm6, %%ymm6, %%ymm14\n\t" \
	"vpmuludq %%ymm7, %%ymm7, %%ymm15\n\t"

#define STORE8 \
	"vmovdqa %%ymm8,  256(%%rax)\n\t" \
	"vmovdqa %%ymm9,  288(%%rax)\n\t" \
	"vmovdqa %%ymm10, 320(%%rax)\n\t" \
	"vmovdqa %%ymm11, 352(%%rax)\n\t" \
	"vmovdqa %%ymm12, 384(%%rax)\n\t" \
	"vmovdqa %%ymm13, 416(%%rax)\n\t" \
	"vmovdqa %%ymm14, 448(%%rax)\n\t" \
	"vmovdqa %%ymm15, 480(%%rax)\n\t"

#define ALL_YMM_CLOBBERS \
	"ymm0", "ymm1", "ymm2", "ymm3", "ymm4", "ymm5", "ymm6", "ymm7", \
	"ymm8", "ymm9", "ymm10", "ymm11", "ymm12", "ymm13", "ymm14", "ymm15"

#define DEFINE_MEM_VICTIM(fname, preamble, body, periter)                              \
	static __attribute__((noinline)) int fname(void *varg)                \
	{                                                                     \
		struct victim_args_t *a = varg;                               \
		struct ctl_t *ctl = a->ctl;                                   \
		__m256i buf[MEM_SLOTS] __attribute__((aligned(64)));          \
		uint64_t cached;                                              \
                                                                              \
		victim_pin(a->core_id);                                          \
		sched_yield();                                                \
		a->bytes_per_burst = (uint64_t)AVX_BURST * (periter);         \
                                                                              \
		cached = ctl->selector;                                       \
		for (int i = 0; i < MEM_SLOTS; i++)                           \
			buf[i] = _mm256_set1_epi32((int)(uint32_t)cached);    \
                                                                              \
		while (ctl->run) {                                            \
			VICTIM_TICK(a, ctl);                                  \
			uint64_t s = ctl->selector;                           \
			if (s != cached) {                                    \
				cached = s;                                   \
				for (int i = 0; i < MEM_SLOTS; i++)           \
					buf[i] = _mm256_set1_epi32((int)(uint32_t)s); \
			}                                                     \
			asm volatile(                                         \
				"mov %[p], %%rax\n\t"                         \
				preamble                                      \
				"mov $" STR(AVX_BURST) ", %%rcx\n\t"          \
				"1:\n\t"                                      \
				body                                          \
				"sub $1, %%rcx\n\t"                           \
				"jnz 1b\n\t"                                  \
				"vzeroupper\n\t"                              \
				:                                             \
				: [p] "r" (buf)                               \
				: "rax", "rcx", "memory", ALL_YMM_CLOBBERS);  \
		}                                                             \
		_exit(0);                                                     \
		return 0;                                                     \
	}

DEFINE_MEM_VICTIM(avx2_mul_ld_victim,   "", LOAD8 MUL8, 256)
DEFINE_MEM_VICTIM(avx2_mul_ldst_victim, "", LOAD8 MUL8 STORE8, 512)
DEFINE_MEM_VICTIM(avx2_load_victim,     "", LOAD8, 256)

/* Operand fetched once, outside the loop: stores carry the traffic. */
DEFINE_MEM_VICTIM(avx2_mul_st_victim,
		  "vmovdqa 0(%%rax), %%ymm0\n\t"
		  "vmovdqa %%ymm0, %%ymm1\n\t"
		  "vmovdqa %%ymm0, %%ymm2\n\t"
		  "vmovdqa %%ymm0, %%ymm3\n\t"
		  "vmovdqa %%ymm0, %%ymm4\n\t"
		  "vmovdqa %%ymm0, %%ymm5\n\t"
		  "vmovdqa %%ymm0, %%ymm6\n\t"
		  "vmovdqa %%ymm0, %%ymm7\n\t",
		  MUL8 STORE8, 256)

/* ---- Traffic-volume sweep --------------------------------------------- *
 *
 * The variants above establish that operand movement leaks and register-
 * resident operands do not. These vary how much movement there is, along two
 * independent axes:
 *
 *   loads per iteration  1 / 2 / 4 / 8   at a fixed L1-resident working set
 *   working set          16K / 512K / 4M / 32M  at a fixed 8 loads
 *
 * Sized for this part: L1d 48K and L2 1.25M per P-core, L3 24M shared. With
 * four victim threads the 4M variant totals 16M and still fits L3, while 32M
 * each is far past it and must come from DRAM.
 *
 * Two buffers are held per victim, one per selector value seen, so switching
 * conditions is a pointer swap rather than a refill of the whole working set
 * -- otherwise every block boundary would inject a large burst of write
 * traffic into the measurement.
 */
#define WS_SLOTS 2

struct ws_cache {
	unsigned char *slot[WS_SLOTS];
	uint64_t val[WS_SLOTS];
	int filled[WS_SLOTS];
	int next;
	size_t bytes;
	/*
	 * 0 = fill with one repeated word. Otherwise the word index is masked
	 * with this to choose between the selector's two halves, so the value
	 * is the alternation period in words: 8 for 32 bytes, 16 for 64.
	 */
	int ab_mask;
	/*
	 * Phase 3 sparsity mode. When set, ws_get reads the selector as a
	 * density rather than an operand: its low 16 bits are the number of
	 * nonzero 32-bit words per 1024 (0..1024), its high 32 bits the nonzero
	 * pattern (0 is taken as 0xFFFFFFFF). The load stream, the addresses
	 * touched and the byte rate are identical to ws_l3_x8 at every density,
	 * so only operand *content* -- how many words are zero -- varies between
	 * conditions, and work_balance still holds.
	 */
	int sparse;
	/*
	 * Placement granularity for the sparse fill, in 32-bit words. At a fixed
	 * density both settings hold the mean Hamming weight identical; they differ
	 * in switching, and the direction is the opposite of what word-to-word
	 * distance suggests. 1 (scattered) spreads non-zero words with a period of
	 * 1-8 words at every density swept, so every 32-byte load carries the same
	 * 256-bit pattern and NOTHING toggles between consecutive loads or lines --
	 * the 0/P alternation is spatial, inside one transfer. 16 (blocked) groups
	 * them into 64-byte runs, so consecutive lines alternate all-zero/all-ones
	 * and toggle up to 512 bits each. Scattered is therefore the static arm and
	 * blocked the switching arm; blocked-minus-scattered is the switching term
	 * (critique E2). tests/fillcheck.c measures and asserts this. Ignored
	 * unless `sparse`.
	 */
	int sparse_block;
	/*
	 * I.i.d. fill mode (critique E2b). When set, ws_get reads the selector's
	 * low 16 bits as a per-1024 *bit* probability and sets every bit of every
	 * word independently, so the words genuinely differ rather than repeating
	 * one value. It tests whether the Hamming-weight slope survives on
	 * non-degenerate data at the same mean bit density. Reads the selector as
	 * a density like `sparse`, and takes precedence over it.
	 */
	int iid;
};

static void ws_fill(void *p, size_t bytes, uint32_t v)
{
	uint32_t *q = p;
	for (size_t i = 0; i < bytes / sizeof(*q); i++)
		q[i] = v;
}

/*
 * Alternate two words with a period of `mask` words, so consecutive transfers
 * of that size carry different bit patterns. mask is a single bit of the word
 * index, so it flips every `mask` words: 8 for a 32-byte period, 16 for 64.
 */
static void ws_fill_ab(void *p, size_t bytes, uint32_t a, uint32_t b, int mask)
{
	uint32_t *q = p;
	for (size_t i = 0; i < bytes / sizeof(*q); i++)
		q[i] = (i & (size_t)mask) ? b : a;
}

/*
 * Fill the buffer to a controlled density: `per1024` nonzero words out of every
 * 1024, the rest zero, spread uniformly by a Bresenham accumulator so the
 * density is the same across the buffer and every streamed 256-byte read sees a
 * representative mix rather than a run of zeros followed by a run of nonzeros.
 *
 * This is the Phase 3 sparsity knob. Because the count of nonzeros changes but
 * the buffer size, the addresses streamed and the loads issued do not, the two
 * conditions of a sparsity contrast issue identical loads. Their *achieved* rate
 * still differs by up to ~2-4%, with random sign: the two conditions live in two
 * different mmaps, and that is critique C4's two-buffer placement effect, not a
 * work difference (results/20260930-132043-phase1_sparsity_mixture: corr with
 * the power effect +0.14; the same-buffer A/A matches to 0.04%).
 *
 * Measured by critique E2 in that session, and correcting what this comment
 * first assumed: (1) at the densities swept the spread has a period of 1-8
 * words, so every 32-byte load is identical and nothing toggles between
 * transfers -- the 0/P alternation is inside one load, not between loads, so
 * this fill does NOT add switching; (2) a mixture does not inherit the cheap
 * zero word by word -- it pays Phase 1's whole step once any word is non-zero
 * and then follows the uniform-word law in *mean Hamming weight*. So density is
 * recoverable, but through the per-bit weight term rather than the zero step,
 * and what power tracks is mean bit density rather than the zero count as such.
 */
static void ws_fill_sparse(void *p, size_t bytes, uint32_t per1024, uint32_t pattern,
			   int block_words)
{
	uint32_t *q = p;
	size_t n = bytes / sizeof(*q);
	uint32_t acc = 0;

	if (per1024 > 1024)
		per1024 = 1024;
	if (block_words < 1)
		block_words = 1;
	/*
	 * Bresenham at block granularity: decide each block once and fill it
	 * whole. At block_words = 1 this is the original per-word spread. Density
	 * is per1024/1024 either way -- the fraction of nonzero *blocks* equals
	 * the fraction of nonzero words when every block is uniform -- so the mean
	 * Hamming weight is identical across block sizes and only the switching
	 * rate changes.
	 */
	for (size_t base = 0; base < n; base += (size_t)block_words) {
		acc += per1024;
		uint32_t v = 0;
		if (acc >= 1024) {
			acc -= 1024;
			v = pattern;
		}
		size_t end = base + (size_t)block_words;
		if (end > n)
			end = n;
		for (size_t i = base; i < end; i++)
			q[i] = v;
	}
}

/*
 * I.i.d. fill: every bit of every word set independently with probability
 * per1024/1024. Mean Hamming weight per word is 32*per1024/1024, the same as a
 * sparse fill at the matching density, but the words differ from one another --
 * which is the point, since Phase 1's slope was measured on one repeated word.
 * Seeded from the selector so the buffer is reproducible from the logged value.
 */
static void ws_fill_iid(void *p, size_t bytes, uint32_t per1024, uint64_t seed)
{
	uint32_t *q = p;
	size_t n = bytes / sizeof(*q);

	if (per1024 > 1024)
		per1024 = 1024;
	/* splitmix-style seed so a small selector still gives well-mixed bits. */
	uint64_t s = seed * 0x9E3779B97F4A7C15ULL + 1;
	for (size_t i = 0; i < n; i++) {
		uint32_t w = 0;
		for (int b = 0; b < 32; b++) {
			s ^= s << 13; s ^= s >> 7; s ^= s << 17;	/* xorshift64 */
			if ((uint32_t)(s & 1023) < per1024)
				w |= (1u << b);
		}
		q[i] = w;
	}
}

/*
 * The one place the fill mode is decoded from the selector, shared by ws_get
 * (what the victim streams) and ws_fill_probe (what the test measures), so the
 * test can never drift from the victim. iid takes precedence over sparse; both
 * read the low 16 bits as a per-1024 density; sparse also reads the high 32 as
 * the nonzero pattern. ab_mask and the plain single-word fill are the
 * non-density modes.
 */
static void ws_apply_fill(unsigned char *buf, size_t bytes, uint64_t sel,
			  int sparse, int sparse_block, int iid, int ab_mask)
{
	if (iid) {
		ws_fill_iid(buf, bytes, (uint32_t)(sel & 0xffff), sel);
	} else if (sparse) {
		uint32_t per1024 = (uint32_t)(sel & 0xffff);
		uint32_t patt = (uint32_t)(sel >> 32);
		if (patt == 0)
			patt = 0xffffffff;
		ws_fill_sparse(buf, bytes, per1024, patt,
			       sparse_block > 0 ? sparse_block : 1);
	} else if (ab_mask) {
		ws_fill_ab(buf, bytes, (uint32_t)sel, (uint32_t)(sel >> 32), ab_mask);
	} else {
		ws_fill(buf, bytes, (uint32_t)sel);
	}
}

static void ws_init(struct ws_cache *c, size_t bytes, int ab_mask)
{
	c->bytes = bytes;
	c->next = 0;
	c->ab_mask = ab_mask;
	c->sparse = 0;
	c->sparse_block = 1;
	c->iid = 0;
	for (int i = 0; i < WS_SLOTS; i++) {
		c->filled[i] = 0;
		c->slot[i] = mmap(NULL, bytes, PROT_READ | PROT_WRITE,
				  MAP_PRIVATE | MAP_ANONYMOUS | MAP_POPULATE, -1, 0);
		if (c->slot[i] == MAP_FAILED) {
			fprintf(stderr, "victim: mmap %zu bytes failed\n", bytes);
			_exit(1);
		}
	}
}

static unsigned char *ws_get(struct ws_cache *c, uint64_t sel)
{
	for (int i = 0; i < WS_SLOTS; i++)
		if (c->filled[i] && c->val[i] == sel)
			return c->slot[i];

	int i = c->next;
	c->next = (c->next + 1) % WS_SLOTS;
	ws_apply_fill(c->slot[i], c->bytes, sel,
		      c->sparse, c->sparse_block, c->iid, c->ab_mask);
	c->val[i] = sel;
	c->filled[i] = 1;
	return c->slot[i];
}

/*
 * Test hook (critique E2): fill a caller-provided buffer exactly as a victim in
 * the given mode would for `sel`, and report the realised zero-word fraction,
 * mean Hamming weight per word, and mean per-word Hamming distance. The whole
 * x-axis of the sparsity sweeps is the requested density; a fill bug would draw
 * a smooth, monotone, wrong curve that every validity gate would pass, so the
 * fill is checked against ground truth before a session trusts it. Uses the
 * same ws_apply_fill the victim does.
 */
void ws_fill_probe(unsigned char *buf, size_t bytes, uint64_t sel,
		   int sparse, int sparse_block, int iid, int ab_mask,
		   double *zero_frac, double *mean_hw, double *mean_hd)
{
	ws_apply_fill(buf, bytes, sel, sparse, sparse_block, iid, ab_mask);

	uint32_t *q = (uint32_t *)buf;
	size_t n = bytes / sizeof(*q);
	size_t zeros = 0;
	uint64_t total_hw = 0, total_hd = 0;
	for (size_t i = 0; i < n; i++) {
		if (q[i] == 0)
			zeros++;
		total_hw += (uint64_t)__builtin_popcount(q[i]);
		if (i > 0)
			total_hd += (uint64_t)__builtin_popcount(q[i] ^ q[i - 1]);
	}
	if (zero_frac)
		*zero_frac = n ? (double)zeros / (double)n : 0.0;
	if (mean_hw)
		*mean_hw = n ? (double)total_hw / (double)n : 0.0;
	if (mean_hd)
		*mean_hd = n > 1 ? (double)total_hd / (double)(n - 1) : 0.0;
}

#define WS_LD1 "vmovdqa    0(%[p],%[o]), %%ymm0\n\t"
#define WS_LD2 WS_LD1 \
	"vmovdqa   32(%[p],%[o]), %%ymm1\n\t"
#define WS_LD4 WS_LD2 \
	"vmovdqa   64(%[p],%[o]), %%ymm2\n\t" \
	"vmovdqa   96(%[p],%[o]), %%ymm3\n\t"
#define WS_LD8 WS_LD4 \
	"vmovdqa  128(%[p],%[o]), %%ymm4\n\t" \
	"vmovdqa  160(%[p],%[o]), %%ymm5\n\t" \
	"vmovdqa  192(%[p],%[o]), %%ymm6\n\t" \
	"vmovdqa  224(%[p],%[o]), %%ymm7\n\t"

/* Sizes are powers of two so the cursor wraps with a mask. */
#define DEFINE_WS_VICTIM_MODE(fname, bytes, loads, step, ab_mask, is_sparse, blk, is_iid) \
	static __attribute__((noinline)) int fname(void *varg)                \
	{                                                                     \
		struct victim_args_t *a = varg;                               \
		struct ctl_t *ctl = a->ctl;                                   \
		struct ws_cache cache;                                        \
		uint64_t off = 0, mask = (uint64_t)(bytes) - (step);          \
                                                                              \
		victim_pin(a->core_id);                                          \
		sched_yield();                                                \
		/* is_sparse, not sparse: a macro parameter named `sparse`    \
		 * would be textually substituted into `cache.sparse`,        \
		 * rewriting the member access to `cache.0` and failing to    \
		 * compile. */                                                \
		ws_init(&cache, (bytes), (ab_mask));                          \
		cache.sparse = (is_sparse);                                   \
		cache.sparse_block = (blk);                                   \
		cache.iid = (is_iid);                                         \
		a->bytes_per_burst = (uint64_t)AVX_BURST * (step);            \
                                                                              \
		while (ctl->run) {                                            \
			VICTIM_TICK(a, ctl);                                  \
			unsigned char *buf = ws_get(&cache, ctl->selector);   \
			asm volatile(                                         \
				"mov $" STR(AVX_BURST) ", %%rcx\n\t"          \
				"1:\n\t"                                      \
				loads                                         \
				"add $" STR(step) ", %[o]\n\t"                \
				"and %[m], %[o]\n\t"                          \
				"sub $1, %%rcx\n\t"                           \
				"jnz 1b\n\t"                                  \
				"vzeroupper\n\t"                              \
				: [o] "+r" (off)                              \
				: [p] "r" (buf), [m] "r" (mask)               \
				: "rcx", "memory", YMM_CLOBBERS);             \
		}                                                             \
		_exit(0);                                                     \
		return 0;                                                     \
	}

#define DEFINE_WS_VICTIM(fname, bytes, loads, step)                           \
	DEFINE_WS_VICTIM_MODE(fname, bytes, loads, step, 0, 0, 1, 0)

/* Alternate every 32 bytes: every ymm load differs from the one before it. */
#define DEFINE_WS_AB_VICTIM(fname, bytes, loads, step)                        \
	DEFINE_WS_VICTIM_MODE(fname, bytes, loads, step, 8, 0, 1, 0)

/* Alternate every 64 bytes: every cache line differs from the one before it. */
#define DEFINE_WS_AB64_VICTIM(fname, bytes, loads, step)                      \
	DEFINE_WS_VICTIM_MODE(fname, bytes, loads, step, 16, 0, 1, 0)

/*
 * Phase 3 sparsity: the selector's low 16 bits are the nonzero-word count per
 * 1024 and its high 32 bits the nonzero pattern (0 -> all-ones). Same load
 * stream as the plain ws_*_x8 victim at every density. Scattered spread
 * (block = 1): every 32-byte load is the same pattern, so this is the STATIC
 * arm -- a mixture with no switching between transfers (see ws_cache).
 */
#define DEFINE_WS_SPARSE_VICTIM(fname, bytes, loads, step)                     \
	DEFINE_WS_VICTIM_MODE(fname, bytes, loads, step, 0, 1, 1, 0)

/*
 * Blocked sparsity (critique E2): the same density and mean Hamming weight, but
 * non-zero words grouped into 64-byte (16-word) runs, so consecutive lines
 * alternate between all-zero and all-ones -- the SWITCHING arm. Blocked minus
 * scattered at a fixed density is the switching term. (Designed first as the
 * switching-free arm, from word-to-word distance; at transfer granularity it is
 * the reverse, which tests/fillcheck.c now asserts.)
 */
#define DEFINE_WS_SPARSE_BLK_VICTIM(fname, bytes, loads, step)                 \
	DEFINE_WS_VICTIM_MODE(fname, bytes, loads, step, 0, 1, 16, 0)

/*
 * I.i.d. sparsity (critique E2b): the selector's low 16 bits are a per-1024
 * *bit* probability and every bit is drawn independently, so words differ from
 * one another at the same mean bit density. Tests whether the Hamming-weight
 * slope survives on non-degenerate data.
 */
#define DEFINE_WS_IID_VICTIM(fname, bytes, loads, step)                        \
	DEFINE_WS_VICTIM_MODE(fname, bytes, loads, step, 0, 0, 1, 1)

/* Axis 1: loads per iteration, working set pinned in L1. */
DEFINE_WS_VICTIM(ws_l1_x1_victim, 16384, WS_LD1, 32)
DEFINE_WS_VICTIM(ws_l1_x2_victim, 16384, WS_LD2, 64)
DEFINE_WS_VICTIM(ws_l1_x4_victim, 16384, WS_LD4, 128)
DEFINE_WS_VICTIM(ws_l1_x8_victim, 16384, WS_LD8, 256)

/* Axis 2: working set, loads pinned at 8 per iteration. */
DEFINE_WS_VICTIM(ws_l2_x8_victim,   524288, WS_LD8, 256)
DEFINE_WS_VICTIM(ws_l3_x8_victim,  4194304, WS_LD8, 256)
DEFINE_WS_VICTIM(ws_dram_x8_victim, 33554432, WS_LD8, 256)

/* ---- Hamming distance ------------------------------------------------- *
 *
 * Every victim above fills its working set with one repeated word, so the
 * data stream is constant and the Hamming distance between consecutive
 * transfers is zero by construction. That is a confound, not a detail:
 * classical DPA models leakage as switching activity -- bits that flip
 * between successive values on a bus -- while the Hamming-weight sweep can
 * only see the static weight of the value. Nothing measured so far can tell
 * the two apart, because HD has been pinned at 0 throughout.
 *
 * These variants split the 64-bit selector -- low half is word A, high half
 * is word B -- and alternate them. Pick A and B with equal Hamming weight and
 * the mean weight of the stream is unchanged while the number of bits flipping
 * per transfer is 8 * HD(A, B); HW is common-mode and HD is the only thing
 * that moves.
 *
 * With B == A the fill is bit-identical to the single-word one, so
 * ws_l3_x8_ab with both halves equal *is* ws_l3_x8, which is what the HD
 * sweep uses as its baseline condition and what ties it to earlier sessions.
 *
 * The alternation period decides *which* path sees the switching, and one
 * period cannot cover both. At 32 bytes every ymm load differs from the one
 * before it, which is the toggling the load ports and the L1 read path see --
 * but a 64-byte line is then a fixed A-then-B composite, so consecutive line
 * fills from L2 or L3 are identical and that path sees no switching at all.
 * ws_l3_x8_ab64 alternates every 64 bytes instead: consecutive lines differ,
 * at the cost of halving the load-to-load toggle rate. A null at one
 * granularity alone would be open to the objection that the bus in question
 * never saw a transition; running both closes it.
 */
DEFINE_WS_AB_VICTIM(ws_l1_x8_ab_victim,   16384, WS_LD8, 256)
DEFINE_WS_AB_VICTIM(ws_l3_x8_ab_victim, 4194304, WS_LD8, 256)
DEFINE_WS_AB_VICTIM(ws_dram_x8_ab_victim, 33554432, WS_LD8, 256)
DEFINE_WS_AB64_VICTIM(ws_l3_x8_ab64_victim, 4194304, WS_LD8, 256)

/* ---- Sparsity, on a traffic-bearing victim (Phase 3) ------------------ *
 *
 * These stream an L2/L3/DRAM-resident working set exactly as ws_*_x8 does, but
 * fill it to a selector-chosen density: fraction f of the 32-bit words carry a
 * nonzero pattern, the rest are zero, spread uniformly. The byte rate and the
 * addresses touched are identical for every f, so a contrast between two
 * densities is a contrast in operand *content* alone and passes work_balance --
 * the same guarantee the Hamming-weight sweep relied on, one level up.
 *
 * This is the direct test of proposal goal (2), reproducing the lost Figure 2:
 * post-ReLU activations are 50-90% zero and input-dependent, and Phase 1 found
 * the zero operand anomalously cheap, so density should be recoverable from
 * package power. ws_sparse_l3_x8 is the primary probe (best-conditioned depth in
 * Phase 1); L2 and DRAM bracket it for a depth x sparsity cross. At density 1024
 * (all words nonzero, default all-ones pattern) each is bit-identical to the
 * plain ws_*_x8 holding 0xFFFFFFFF, which anchors it to the Phase 1 sessions.
 */
DEFINE_WS_SPARSE_VICTIM(ws_sparse_l2_x8_victim,     524288, WS_LD8, 256)
DEFINE_WS_SPARSE_VICTIM(ws_sparse_l3_x8_victim,    4194304, WS_LD8, 256)
DEFINE_WS_SPARSE_VICTIM(ws_sparse_dram_x8_victim, 33554432, WS_LD8, 256)

/*
 * Critique E2 controls at L3. The blocked variant holds density and mean Hamming
 * weight identical to ws_sparse_l3_x8 but groups zeros into cache-line runs, so
 * it toggles between lines where the scattered one does not: blocked-minus-
 * scattered is the switching term, and scattered alone is the clean static
 * per-transfer-vs-per-stream curve. The iid variant fills genuinely differing
 * words at a controlled mean bit density, to test whether Phase 1's weight slope
 * survives non-degenerate data.
 */
DEFINE_WS_SPARSE_BLK_VICTIM(ws_sparse_l3_x8_blk_victim, 4194304, WS_LD8, 256)
DEFINE_WS_IID_VICTIM(ws_iid_l3_x8_victim, 4194304, WS_LD8, 256)

/* ---- Instruction family, on a traffic-bearing victim ------------------ *
 *
 * The DEFINE_VEC_VICTIM instruction set above is register-resident, and
 * register-resident operands do not leak measurably -- so a per-instruction
 * table built on it would be a table of noise. These victims put the same
 * instructions behind the ws_l3_x8 load stream: 8 x 32-byte loads per
 * iteration from a 4M working set, then 8 independent operations on what was
 * loaded, into a separate bank of destination registers.
 *
 * The load stream is byte-for-byte identical across the family, and the loop
 * is load-bound with plenty of margin -- 256 bytes per iteration from L3 is
 * ~16 cycles at the measured per-thread bandwidth, against at most 4 cycles
 * for 8 independent ALU operations -- so every variant moves operands at the
 * same rate and the instruction is the only thing that varies. Whether that
 * held is checkable after the fact: the throughput column must come out flat,
 * and pJ/byte corrects it if it does not.
 *
 * Both source registers of each operation are the same loaded register, which
 * is what makes the result Hamming weight predictable and turns the family
 * into a contrast rather than a list. For an operand v broadcast through the
 * buffer, op(v, v) gives:
 *
 *   vmovdqa    v            result HW tracks the operand
 *   vpand      v            same
 *   vpor       v            same
 *   vpxor      0            result pinned at 0 whatever the operand
 *   vpsllvd    v << v       0 for both operands tested (a shift of >= 32)
 *   vpaddd     2v           HW 31 at 0xFFFFFFFF, 0 at 0
 *   vpmuludq   v * v        HW 32 per 64-bit lane at 0xFFFFFFFF, 0 at 0
 *
 * So vpand and vpor against vpxor and vpsllvd is the input-versus-output
 * contrast: identical input traffic, identical instruction cost class, and a
 * result that either tracks the operand or is pinned at zero. If the leakage
 * is entirely input-driven the four agree; if the result contributes, the
 * first two sit above the last two.
 *
 * vfmadd231ps and vpdpbusd accumulate into their destination, so the
 * destination bank is re-zeroed once per burst -- as scalar_imul does, and
 * for the same reason: an accumulator left to drift decouples from the
 * selector. Their operands are bit patterns rather than numbers; 0x00000000
 * is +0.0f and 0xFFFFFFFF is a NaN, neither of which is a denormal, so no
 * microcode assist should fire. The throughput column is the check.
 */
#define ZERO_HI8 \
	"vpxor %%ymm8, %%ymm8, %%ymm8\n\t" \
	"vpxor %%ymm9, %%ymm9, %%ymm9\n\t" \
	"vpxor %%ymm10, %%ymm10, %%ymm10\n\t" \
	"vpxor %%ymm11, %%ymm11, %%ymm11\n\t" \
	"vpxor %%ymm12, %%ymm12, %%ymm12\n\t" \
	"vpxor %%ymm13, %%ymm13, %%ymm13\n\t" \
	"vpxor %%ymm14, %%ymm14, %%ymm14\n\t" \
	"vpxor %%ymm15, %%ymm15, %%ymm15\n\t"

#define WS_OP8_3(insn) \
	insn " %%ymm0, %%ymm0, %%ymm8\n\t" \
	insn " %%ymm1, %%ymm1, %%ymm9\n\t" \
	insn " %%ymm2, %%ymm2, %%ymm10\n\t" \
	insn " %%ymm3, %%ymm3, %%ymm11\n\t" \
	insn " %%ymm4, %%ymm4, %%ymm12\n\t" \
	insn " %%ymm5, %%ymm5, %%ymm13\n\t" \
	insn " %%ymm6, %%ymm6, %%ymm14\n\t" \
	insn " %%ymm7, %%ymm7, %%ymm15\n\t"

#define WS_OP8_2(insn) \
	insn " %%ymm0, %%ymm8\n\t" \
	insn " %%ymm1, %%ymm9\n\t" \
	insn " %%ymm2, %%ymm10\n\t" \
	insn " %%ymm3, %%ymm11\n\t" \
	insn " %%ymm4, %%ymm12\n\t" \
	insn " %%ymm5, %%ymm13\n\t" \
	insn " %%ymm6, %%ymm14\n\t" \
	insn " %%ymm7, %%ymm15\n\t"

#define DEFINE_WS_OP_VICTIM(fname, bytes, unroll, insn, step, zero, is_sparse) \
	static __attribute__((noinline)) int fname(void *varg)                \
	{                                                                     \
		struct victim_args_t *a = varg;                               \
		struct ctl_t *ctl = a->ctl;                                   \
		struct ws_cache cache;                                        \
		uint64_t off = 0, mask = (uint64_t)(bytes) - (step);          \
                                                                              \
		victim_pin(a->core_id);                                       \
		sched_yield();                                                \
		/* is_sparse, not sparse -- see DEFINE_WS_VICTIM_MODE. */      \
		ws_init(&cache, (bytes), 0); cache.sparse = (is_sparse);      \
		a->bytes_per_burst = (uint64_t)AVX_BURST * (step);            \
                                                                              \
		while (ctl->run) {                                            \
			VICTIM_TICK(a, ctl);                                  \
			unsigned char *buf = ws_get(&cache, ctl->selector);   \
			asm volatile(                                         \
				zero                                          \
				"mov $" STR(AVX_BURST) ", %%rcx\n\t"          \
				"1:\n\t"                                      \
				WS_LD8                                        \
				unroll(insn)                                  \
				"add $" STR(step) ", %[o]\n\t"                \
				"and %[m], %[o]\n\t"                          \
				"sub $1, %%rcx\n\t"                           \
				"jnz 1b\n\t"                                  \
				"vzeroupper\n\t"                              \
				: [o] "+r" (off)                              \
				: [p] "r" (buf), [m] "r" (mask)               \
				: "rcx", "memory", ALL_YMM_CLOBBERS);         \
		}                                                             \
		_exit(0);                                                     \
		return 0;                                                     \
	}

#define WS_OP_L3 4194304

DEFINE_WS_OP_VICTIM(ws_op_mov_victim,   WS_OP_L3, WS_OP8_2, "vmovdqa",      256, NO_ZERO, 0)
DEFINE_WS_OP_VICTIM(ws_op_and_victim,   WS_OP_L3, WS_OP8_3, "vpand",        256, NO_ZERO, 0)
DEFINE_WS_OP_VICTIM(ws_op_or_victim,    WS_OP_L3, WS_OP8_3, "vpor",         256, NO_ZERO, 0)
DEFINE_WS_OP_VICTIM(ws_op_xor_victim,   WS_OP_L3, WS_OP8_3, "vpxor",        256, NO_ZERO, 0)
DEFINE_WS_OP_VICTIM(ws_op_add_victim,   WS_OP_L3, WS_OP8_3, "vpaddd",       256, NO_ZERO, 0)
DEFINE_WS_OP_VICTIM(ws_op_mul_victim,   WS_OP_L3, WS_OP8_3, "vpmuludq",     256, NO_ZERO, 0)
DEFINE_WS_OP_VICTIM(ws_op_shift_victim, WS_OP_L3, WS_OP8_3, "vpsllvd",      256, NO_ZERO, 0)
DEFINE_WS_OP_VICTIM(ws_op_fma_victim,   WS_OP_L3, WS_OP8_3, "vfmadd231ps",  256, ZERO_HI8, 0)

#ifdef __AVXVNNI__
/* See the AVX-VNNI note on avx2_vnni above: %{vex%} is mandatory here too. */
DEFINE_WS_OP_VICTIM(ws_op_vnni_victim,  WS_OP_L3, WS_OP8_3, "%{vex%} vpdpbusd", 256, ZERO_HI8, 0)
#endif

/* ---- Sparse activation stream through a compute op (Phase 3, item 2) ---- *
 *
 * These are the ws_op_* family with the sparse fill turned on: the load stream
 * carries a density-controlled operand (selector low 16 bits = nonzero words per
 * 1024, high 32 bits = the packed activation pattern) and the op runs on it. The
 * load stream, addresses and op count are identical for every density, so
 * work_balance holds and the only question is whether the *compute* draws
 * differently when more of its input is zero, on top of the movement channel
 * that ws_sparse_l3_x8 already measures.
 *
 * ws_sparse_op_vnni is the headline: vpdpbusd is exactly the int8 dot product
 * quantised inference issues, so this is activation sparsity seen through the
 * MAC array (Phase 1 found vpdpbusd leaks +0.20 W register-resident). Read it
 * paired against ws_sparse_op_mov -- movement-only at the same density -- to
 * separate the compute channel from the load channel, as the instruction table
 * is read against loads_only. Both source operands of each op are the same
 * loaded activation word, so a zero word contributes a zero partial product;
 * a follow-up can hold a dense weight fixed and vary only the activation.
 */
DEFINE_WS_OP_VICTIM(ws_sparse_op_mov_victim, WS_OP_L3, WS_OP8_2, "vmovdqa", 256, NO_ZERO, 1)
#ifdef __AVXVNNI__
DEFINE_WS_OP_VICTIM(ws_sparse_op_vnni_victim, WS_OP_L3, WS_OP8_3, "%{vex%} vpdpbusd", 256, ZERO_HI8, 1)
#endif

/* ---- Lookup table ----------------------------------------------------- */

struct victim_entry {
	const char *name;
	int (*fn)(void *);
	const char *desc;
};

static const struct victim_entry victims[] = {
	{ "idle",        idle_victim,        "pause loop (power floor / channel OFF state)" },
	{ "nop",         nop_victim,         "scalar nop loop" },
	{ "scalar_rol",  scalar_rol_victim,  "64-bit rotate; constant Hamming weight" },
	{ "scalar_imul", scalar_imul_victim, "64-bit integer multiply" },
	{ "avx2_mul",    avx2_mul_victim,    "vpmuludq ymm (256-bit integer multiply)" },
	{ "avx256mul",   avx2_mul_victim,    "alias of avx2_mul (legacy name)" },
	{ "avx2_add",    avx2_add_victim,    "vpaddd ymm" },
	{ "avx2_and",    avx2_and_victim,    "vpand ymm (result HW tracks operand)" },
	{ "avx2_or",     avx2_or_victim,     "vpor ymm (result HW tracks operand)" },
	{ "avx2_xor",    avx2_xor_victim,    "vpxor ymm (result always zero)" },
	{ "avx2_shift",  avx2_shift_victim,  "vpsllvd ymm (variable shift)" },
	{ "avx2_mov",    avx2_mov_victim,    "vmovdqa ymm (movement, no ALU work)" },
	{ "avx2_fma",    avx2_fma_victim,    "vfmadd231ps ymm" },
#ifdef __AVXVNNI__
	{ "avx2_vnni",   avx2_vnni_victim,   "vpdpbusd ymm (AVX-VNNI int8 dot product)" },
#endif
	{ "sse_mul",     sse_mul_victim,     "vpmuludq xmm (128-bit, width comparison)" },
	{ "avx2_mul_ld",   avx2_mul_ld_victim,   "vpmuludq with operands reloaded from L1 each iteration" },
	{ "avx2_mul_st",   avx2_mul_st_victim,   "vpmuludq with results stored to L1 each iteration" },
	{ "avx2_mul_ldst", avx2_mul_ldst_victim, "vpmuludq with both loads and stores (closest to the original -O0 victim)" },
	{ "avx2_load",     avx2_load_victim,     "vmovdqa loads only, no ALU work" },
	{ "ws_l1_x1",   ws_l1_x1_victim,   "1 load/iter,  16K working set (L1)" },
	{ "ws_l1_x2",   ws_l1_x2_victim,   "2 loads/iter, 16K working set (L1)" },
	{ "ws_l1_x4",   ws_l1_x4_victim,   "4 loads/iter, 16K working set (L1)" },
	{ "ws_l1_x8",   ws_l1_x8_victim,   "8 loads/iter, 16K working set (L1)" },
	{ "ws_l2_x8",   ws_l2_x8_victim,   "8 loads/iter, 512K working set (L2)" },
	{ "ws_l3_x8",   ws_l3_x8_victim,   "8 loads/iter, 4M working set (L3)" },
	{ "ws_dram_x8", ws_dram_x8_victim, "8 loads/iter, 32M working set (DRAM)" },
	{ "ws_l1_x8_ab",   ws_l1_x8_ab_victim,   "as ws_l1_x8, alternating the selector's two 32-bit halves" },
	{ "ws_l3_x8_ab",   ws_l3_x8_ab_victim,   "as ws_l3_x8, alternating the selector's two 32-bit halves" },
	{ "ws_dram_x8_ab", ws_dram_x8_ab_victim, "as ws_dram_x8, alternating the selector's two 32-bit halves" },
	{ "ws_l3_x8_ab64", ws_l3_x8_ab64_victim, "as ws_l3_x8_ab, but alternating every 64 bytes (one cache line)" },
	{ "ws_sparse_l2_x8",   ws_sparse_l2_x8_victim,   "ws_l2_x8 stream at selector-controlled density (Phase 3 sparsity)" },
	{ "ws_sparse_l3_x8",   ws_sparse_l3_x8_victim,   "ws_l3_x8 stream at selector-controlled density (Phase 3 sparsity)" },
	{ "ws_sparse_dram_x8", ws_sparse_dram_x8_victim, "ws_dram_x8 stream at selector-controlled density (Phase 3 sparsity)" },
	{ "ws_sparse_l3_x8_blk", ws_sparse_l3_x8_blk_victim, "as ws_sparse_l3_x8 but zeros in 64-byte runs: toggles between lines (E2 switching arm)" },
	{ "ws_iid_l3_x8",      ws_iid_l3_x8_victim,      "ws_l3_x8 stream, each bit iid at selector density (E2 slope control)" },
	{ "ws_op_mov",   ws_op_mov_victim,   "ws_l3_x8 loads + 8 vmovdqa reg-reg (movement, no compute)" },
	{ "ws_op_and",   ws_op_and_victim,   "ws_l3_x8 loads + 8 vpand (result HW tracks operand)" },
	{ "ws_op_or",    ws_op_or_victim,    "ws_l3_x8 loads + 8 vpor (result HW tracks operand)" },
	{ "ws_op_xor",   ws_op_xor_victim,   "ws_l3_x8 loads + 8 vpxor (result always zero)" },
	{ "ws_op_add",   ws_op_add_victim,   "ws_l3_x8 loads + 8 vpaddd" },
	{ "ws_op_mul",   ws_op_mul_victim,   "ws_l3_x8 loads + 8 vpmuludq" },
	{ "ws_op_shift", ws_op_shift_victim, "ws_l3_x8 loads + 8 vpsllvd (variable shift)" },
	{ "ws_op_fma",   ws_op_fma_victim,   "ws_l3_x8 loads + 8 vfmadd231ps" },
#ifdef __AVXVNNI__
	{ "ws_op_vnni",  ws_op_vnni_victim,  "ws_l3_x8 loads + 8 vpdpbusd (AVX-VNNI int8 dot product)" },
#endif
	{ "ws_sparse_op_mov",  ws_sparse_op_mov_victim,  "sparse activation stream + 8 vmovdqa (movement-only reference)" },
#ifdef __AVXVNNI__
	{ "ws_sparse_op_vnni", ws_sparse_op_vnni_victim, "sparse activation stream + 8 vpdpbusd (Phase 3 int8 activation sparsity)" },
#endif
};

#define NUM_VICTIMS (sizeof(victims) / sizeof(victims[0]))

int (*get_victim(const char *victim))(void *)
{
	for (size_t i = 0; i < NUM_VICTIMS; i++) {
		if (strcmp(victim, victims[i].name) == 0)
			return victims[i].fn;
	}
	fprintf(stderr, "Unknown victim '%s'. Available:\n", victim);
	list_victims(stderr);
	exit(EXIT_FAILURE);
}

void list_victims(FILE *out)
{
	for (size_t i = 0; i < NUM_VICTIMS; i++)
		fprintf(out, "  %-13s %s\n", victims[i].name, victims[i].desc);
#ifndef __AVXVNNI__
	fprintf(out, "  (avx2_vnni omitted: built without AVX-VNNI support)\n");
#endif
}
