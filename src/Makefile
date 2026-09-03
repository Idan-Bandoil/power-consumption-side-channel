CC:= gcc

# -O2 is safe here because every victim's hot loop is inline asm, so the
# compiler cannot hoist or delete the work being measured. Do not add plain-C
# victims without making their results volatile.
CFLAGS:= -D_GNU_SOURCE -m64 -falign-functions=64 -Wall -Wextra -march=native -O2 -MMD -MP
LIBS:= -lpthread -lrt -lm
UTILS:= ../util/util.o ../util/msr-utils.o ../util/rapl-utils.o ../util/freq-utils.o ../util/victim-utils.o ../util/victim-pool.o ../util/sampler.o

all: obj bin out data driver hybrid_detect tx rx_rapl rx_freq rx_timing

driver: obj/driver.o $(UTILS) | bin
	$(CC) -o bin/$@ $^ $(LIBS)

# Covert channel (Phase 2). tx is unprivileged -- it only runs AVX loads and
# writes ctl->selector; rx_rapl needs root for the MSR. They are separate
# binaries because they must be separate processes sharing no memory: the
# channel is the power, not the address space.
tx: obj/covert_tx.o $(UTILS) | bin
	$(CC) -o bin/$@ $^ $(LIBS)

rx_rapl: obj/covert_rx_rapl.o $(UTILS) | bin
	$(CC) -o bin/$@ $^ $(LIBS)

# Tier 2: reads world-readable scaling_cur_freq, so it needs no privilege at
# all. Requires Config-B -- Config-A pins the frequency this receiver lives on.
rx_freq: obj/covert_rx_freq.o $(UTILS) | bin
	$(CC) -o bin/$@ $^ $(LIBS)

# Tier 3: times its own workload and reads nothing at all -- no MSR, no file.
# Also Config-B only, and needs the part to be throttling.
rx_timing: obj/covert_rx_timing.o $(UTILS) | bin
	$(CC) -o bin/$@ $^ $(LIBS)

obj/covert_%.o: covert/%.c | obj
	$(CC) -c $(CFLAGS) -o $@ $<

# Standalone diagnostic: reports per-CPU core type (P/E) via CPUID leaf 0x1A.
hybrid_detect: obj/hybrid_detect.o | bin
	$(CC) -o bin/$@ $^ $(LIBS)

# Non-root check that every victim spins, honours a live selector write, and
# exits cleanly. Catches mis-assembled instructions (see the AVX-VNNI note in
# util/victim-utils.c) that the MSR-gated driver would only hit at run time.
smoke: obj/victim_smoke.o ../util/util.o ../util/victim-utils.o | bin
	$(CC) -o bin/$@ $^ $(LIBS)

obj/victim_smoke.o: ../tests/victim_smoke.c | obj
	$(CC) -c $(CFLAGS) -I../util -o $@ $<

check: smoke
	@fail=0; \
	for v in $$(./bin/driver --list-victims | awk '{print $$1}'); do \
		if ./bin/smoke $$v > /dev/null 2>&1; then \
			printf '  %-14s PASS\n' $$v; \
		else \
			printf '  %-14s FAIL\n' $$v; fail=1; \
		fi; \
	done; \
	exit $$fail

obj/%.o: %.c | obj
	$(CC) -c $(CFLAGS) -o $@ $<

../util/%.o: ../util/%.c
	$(CC) -c $(CFLAGS) -o $@ $<

obj bin out data:
	mkdir -p $@

clean:
	rm -rf bin obj
	rm -rf ../util/*.o ../util/*.d

-include $(wildcard obj/*.d ../util/*.d)

.PHONY: all clean check
