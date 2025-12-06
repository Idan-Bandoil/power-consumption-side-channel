#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <sched.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <sys/mman.h>
#include <immintrin.h>
#include <unistd.h>

// --- Custom Utils ---
#include "../util/freq-utils.h"
#include "../util/rapl-utils.h"
#include "../util/util.h"
#include "../util/victim-utils.h"

// ==============================================================================
// 1. CONFIGURATION & CONSTANTS
// ==============================================================================

// System Configuration
#define STACK_SIZE              8192
#define MAX_FILENAME_SIZE       64
#define MAX_SELECTORS           100
#define PRIORITY_HIGH           -20  // Highest priority to prevent context switches

// Core Pinning (Specific to i7-12700H Architecture)
#define ATTACKER_CORE_ID        0    // Fixed P-Core for Monitor
#define VICTIM_START_CORE_ID    2    // Start Victims at P-Core 2
#define MAX_P_CORE_ID           11   // Safety limit for P-Cores
#define CORE_OFFSET_STRIDE      2    // Skip every other core to avoid HT sharing with same victim

// Timing & Measurements
// 2,000,000 cycles is approx 1ms on a 2.3GHz base clock.
// We use this to ensure we don't sample faster than the RAPL register updates.
#define CYCLES_PER_MS           2000000 
#define THREAD_INIT_DELAY_US    1000 // 1ms delay to let threads settle

// Global shared state (populated by argument parsing)
char *victim_name;

// Data structure for in-memory logging
typedef struct {
    double energy;
    uint64_t aperf;
    uint64_t mperf;
} measurement_t;

// ==============================================================================
// 2. HELPER FUNCTIONS
// ==============================================================================

/**
 * Optimized busy-wait loop.
 * Keeps the CPU frequency high (preventing C-state drops) while minimizing
 * memory subsystem traffic by using _mm_pause().
 */
static inline void busy_wait(uint64_t cycles) {
    uint64_t start = _rdtsc();
    while ((_rdtsc() - start) < cycles) {
        _mm_pause(); 
    }
}

/**
 * Flushes the in-memory trace log to a disk file.
 * Performed strictly AFTER measurements to avoid I/O noise.
 */
static int save_trace_to_file(const char *v_name, uint64_t selector, measurement_t *log, uint64_t count) {
    char filename[MAX_FILENAME_SIZE];
    sprintf(filename, "./out/%s_%02lu.out", v_name, selector);

    FILE *f = fopen(filename, "a");
    if (f == NULL) {
        perror("Error opening output file");
        return -1;
    }

    for (uint64_t i = 0; i < count; i++) {
        uint32_t khz = 0;
        // Calculate frequency from MPERF/APERF
        if (log[i].mperf > 0) {
            khz = (maximum_frequency * log[i].aperf) / log[i].mperf;
        }
        
        // Write format: [Energy_Delta] [Frequency_KHz]
        fprintf(f, "%.15f %" PRIu32 "\n", log[i].energy, khz);
    }

    fclose(f);
    return 0;
}

// ==============================================================================
// 3. MONITOR THREAD (THE ATTACKER)
// ==============================================================================

static __attribute__((noinline)) int monitor(void *in) {
    struct args_t *arg = (struct args_t *)in;

    // 1. Setup Phase
    pin_cpu(ATTACKER_CORE_ID);
    sched_yield(); // Ensure OS applies affinity

    // Allocate trace buffer (CRITICAL: Do this before timing loop)
    size_t buffer_size = sizeof(measurement_t) * arg->iters;
    measurement_t *trace_log = malloc(buffer_size);
    if (!trace_log) {
        perror("Trace buffer allocation failed");
        exit(1);
    }
    memset(trace_log, 0, buffer_size); // Fault in pages

    // 2. Warmup Phase
    // Force CPU to wake up and scale frequency
    busy_wait(CYCLES_PER_MS);

    // 3. Baseline Measurement
    double prev_energy = rapl_msr(ATTACKER_CORE_ID, PP0_ENERGY);
    struct freq_sample_t prev_freq = frequency_msr_raw(ATTACKER_CORE_ID);
    
    double curr_energy;
    struct freq_sample_t curr_freq;

    // 4. Measurement Loop (The Critical Section)
    for (uint64_t i = 0; i < arg->iters; i++) {
        // Spin to keep frequency locked and wait for RAPL update
        busy_wait(CYCLES_PER_MS);

        // Read Hardware Counters
        curr_energy = rapl_msr(ATTACKER_CORE_ID, PP0_ENERGY);
        curr_freq = frequency_msr_raw(ATTACKER_CORE_ID);

        // Log Data (Fast memory write)
        trace_log[i].energy = curr_energy - prev_energy;
        trace_log[i].aperf  = curr_freq.aperf - prev_freq.aperf;
        trace_log[i].mperf  = curr_freq.mperf - prev_freq.mperf;

        // Update History
        prev_energy = curr_energy;
        prev_freq   = curr_freq;
    }

    // 5. Cleanup & Output (Offline)
    if (save_trace_to_file(victim_name, arg->selector, trace_log, arg->iters) != 0)
    {
        free(trace_log);
        return -1;
    }

    free(trace_log);
    return 0;
}

// ==============================================================================
// 4. MAIN EXPERIMENT CONTROL
// ==============================================================================

void initialize_hardware(int attacker_core) {
    // Maximize process priority
    setpriority(PRIO_PROCESS, 0, PRIORITY_HIGH);

    // Initialize MSR interfaces
    set_frequency_units(attacker_core);
    frequency_msr_raw(attacker_core);
    
    set_rapl_units(attacker_core);
    rapl_msr(attacker_core, PP0_ENERGY);
}

void spawn_victims(int ntasks, int (*victim_func)(void*), char *stack_base, struct args_t *arg, int *thread_ids) {
    for (int i = 0; i < ntasks; i++) {
        // Calculate distinct P-Core ID for each victim
        // Logic: Start at 2, skip 2 (2, 4, 6...) to avoid HyperThread sharing with Monitor (0)
        arg->target_core_id = VICTIM_START_CORE_ID + (i * CORE_OFFSET_STRIDE);

        if (arg->target_core_id > MAX_P_CORE_ID) {
            printf("WARNING: Victim assigned to core %d (Potential E-Core/Invalid)\n", arg->target_core_id);
        }

        // Clone thread
        // Note: Stack calculation moves downwards from top of memory block
        char *stack_ptr = stack_base + (ntasks - i) * STACK_SIZE;
        
        thread_ids[i] = clone(
            victim_func,
            stack_ptr,
            CLONE_VM | SIGCHLD,
            arg
        );

        // Allow thread to pin itself before starting the next one
        usleep(THREAD_INIT_DELAY_US);
    }
}

void terminate_victims(int ntasks, int *thread_ids) {
    for (int i = 0; i < ntasks; i++) {
        syscall(SYS_tgkill, thread_ids[i], thread_ids[i], SIGTERM);
        wait(NULL); // Prevent zombie processes
    }
}

int main(int argc, char *argv[]) {
    // Configuration Variables
    int (*victim_func)(void *) = NULL;
    int ntasks = 0, outer = 0, num_selectors = 0;
    int attacker_core_id = 0;
    uint64_t selectors[MAX_SELECTORS] = {0};
    struct args_t arg = {0};

    read_args(argc, argv, &ntasks, &outer, &victim_func, &victim_name, &arg);
    
    num_selectors = read_selectors(selectors);

    // 2. Hardware Setup
	attacker_core_id = arg.target_core_id;
    initialize_hardware(attacker_core_id);

    // 3. Memory Setup
    // Allocate shared stack space for all threads + monitor
    char *thread_stacks = mmap(
        NULL,
        (ntasks + 1) * STACK_SIZE,
        PROT_READ | PROT_WRITE,
        MAP_PRIVATE | MAP_ANONYMOUS,
        -1, 0
    );

    if (thread_stacks == MAP_FAILED) {
        perror("mmap failed");
        return 1;
    }

    // 4. Experiment Loop
    int total_iterations = outer * num_selectors;
    int victim_tids[ntasks];

    for (int i = 0; i < total_iterations; i++) {
        // Progress Logging
        if (i % outer == 0) {
            printf("iteration %d/%d\n", i / outer, num_selectors - 1);
        }

        // Configure Experiment Run
        arg.selector = selectors[i % num_selectors];
        printf("selector: %lu\n", arg.selector);

        // A. Start Victim Threads
        spawn_victims(ntasks, victim_func, thread_stacks, &arg, victim_tids);

        // B. Start Monitor (Attacker)
        // Monitor gets the last stack slot
        clone(
            &monitor,
            thread_stacks + (ntasks + 1) * STACK_SIZE,
            CLONE_VM | SIGCHLD,
            (void *)&arg
        );

        // C. Wait for Monitor to Finish
        wait(NULL);

        // D. Cleanup Victims
        terminate_victims(ntasks, victim_tids);
    }

    // 5. Final Cleanup
    munmap(thread_stacks, (ntasks + 1) * STACK_SIZE);
    return 0;
}
