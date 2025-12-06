#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <unistd.h>
#include <sched.h>
#include <fcntl.h>
#include <math.h>
#include <time.h>
#include <immintrin.h>
#include <sys/types.h>
#include <errno.h>

// --- MSR Definitions (Assuming standard Intel offsets) ---
#define MSR_RAPL_POWER_UNIT 0x606
#define MSR_PP0_ENERGY_STATUS 0x639 // Often Core plane energy
#define MSR_PKG_ENERGY_STATUS 0x611 // Package energy

// --- CPUID Definitions ---
#define HYBRID_LEAF 0x1A
#define CORE_TYPE_ATOM 0x20
#define CORE_TYPE_CORE 0x40

double get_energy_unit() {
    int fd = open("/dev/cpu/0/msr", O_RDONLY);
    if (fd < 0) return 0.00006103515625; // Default fallback (1/2^14)
    
    uint64_t data;
    pread(fd, &data, sizeof(data), MSR_RAPL_POWER_UNIT);
    close(fd);
    
    uint8_t energy_unit_offset = (data >> 8) & 0x1F;
    return 1.0 / (1 << energy_unit_offset);
}

double read_msr_energy(int cpu, double unit) {
    char msr_path[64];
    sprintf(msr_path, "/dev/cpu/%d/msr", cpu);
    int fd = open(msr_path, O_RDONLY);
    if (fd < 0) return 0.0;
    
    uint64_t data;
    // Reading Package Energy (PP0 often unreliable for specific core isolation on mobile)
    if (pread(fd, &data, sizeof(data), MSR_PKG_ENERGY_STATUS) != sizeof(data)) {
        close(fd);
        return 0.0;
    }
    close(fd);
    return data * unit;
}

void pin_to_core(int core_id) {
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(core_id, &cpuset);
    sched_setaffinity(0, sizeof(cpu_set_t), &cpuset);
}

// Simple stress load to spike power
void burn_cycles() {
    volatile double x = 1.5;
    for(int i=0; i<10000000; i++) {
        x *= 1.0000001;
        x = sqrt(x);
    }
}

int main() {
    int num_cores = sysconf(_SC_NPROCESSORS_ONLN);
    double unit = get_energy_unit();
    
    printf("Detected %d Logical Cores.\n", num_cores);
    printf("RAPL Energy Unit: %.10f J\n", unit);
    printf("----------------------------------------------------------------\n");
    printf("| CPU | Type        | Native ID | Stress Power (est) | Notes   |\n");
    printf("|-----|-------------|-----------|--------------------|---------|\n");

    for (int i = 0; i < num_cores; i++) {
        pin_to_core(i);
        
        // 1. Identification via CPUID
        uint32_t eax, ebx, ecx, edx;
        __asm__ volatile ("cpuid"
            : "=a" (eax), "=b" (ebx), "=c" (ecx), "=d" (edx)
            : "a" (HYBRID_LEAF), "c" (0));
            
        uint32_t core_type = (eax >> 24);
        char *type_str = "Unknown";
        if (core_type == CORE_TYPE_ATOM) type_str = "E-Core (Atom)";
        if (core_type == CORE_TYPE_CORE) type_str = "P-Core (Core)";

        // 2. Power Test
        // Measure baseline
        double start = read_msr_energy(i, unit);
        usleep(100000); // 0.1s idle
        double idle_end = read_msr_energy(i, unit);
        
        // Measure Load
        double load_start = read_msr_energy(i, unit);
        burn_cycles(); // Run math
        double load_end = read_msr_energy(i, unit);

        double idle_pwr = (idle_end - start) * 10.0; // W approx
        double load_pwr = (load_end - load_start) * 10.0; // W approx
        // Note: This power is PACKAGE power while this core is active.
        
        printf("| %3d | %-11s | 0x%02X      | ~%-6.2f W         | %s |\n", 
               i, type_str, core_type, load_pwr, 
               (load_pwr > 15.0 && core_type == CORE_TYPE_CORE) ? "HIGH" : "LOW");
    }
    printf("----------------------------------------------------------------\n");
    printf("NOTE: On i7-12700H, P-Cores usually map to CPUs 0-11 (HT), E-Cores 12-19.\n");
    return 0;
}
