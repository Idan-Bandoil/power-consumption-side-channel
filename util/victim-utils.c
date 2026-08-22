#include "victim-utils.h"
#include "util.h"

#include <immintrin.h>
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
		pin_cpu(a->core_id);                                          \
		sched_yield();                                                \
                                                                              \
		cached = ctl->selector;                                       \
		vec = _mm256_set1_epi32((int)(uint32_t)cached);               \
                                                                              \
		while (ctl->run) {                                            \
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

	pin_cpu(a->core_id);
	sched_yield();

	while (ctl->run) {
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

	pin_cpu(a->core_id);
	sched_yield();

	while (ctl->run) {
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

	pin_cpu(a->core_id);
	sched_yield();

	while (ctl->run) {
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

	pin_cpu(a->core_id);
	sched_yield();

	while (ctl->run) {
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

#define DEFINE_MEM_VICTIM(fname, preamble, body)                              \
	static __attribute__((noinline)) int fname(void *varg)                \
	{                                                                     \
		struct victim_args_t *a = varg;                               \
		struct ctl_t *ctl = a->ctl;                                   \
		__m256i buf[MEM_SLOTS] __attribute__((aligned(64)));          \
		uint64_t cached;                                              \
                                                                              \
		pin_cpu(a->core_id);                                          \
		sched_yield();                                                \
                                                                              \
		cached = ctl->selector;                                       \
		for (int i = 0; i < MEM_SLOTS; i++)                           \
			buf[i] = _mm256_set1_epi32((int)(uint32_t)cached);    \
                                                                              \
		while (ctl->run) {                                            \
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

DEFINE_MEM_VICTIM(avx2_mul_ld_victim,   "", LOAD8 MUL8)
DEFINE_MEM_VICTIM(avx2_mul_ldst_victim, "", LOAD8 MUL8 STORE8)
DEFINE_MEM_VICTIM(avx2_load_victim,     "", LOAD8)

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
		  MUL8 STORE8)

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
