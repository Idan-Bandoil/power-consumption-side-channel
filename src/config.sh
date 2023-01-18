TOTAL_PHYSICAL_CORES=`grep '^core id' /proc/cpuinfo | sort -u | wc -l`
TOTAL_LOGICAL_CORES=`grep '^core id' /proc/cpuinfo | wc -l`
samples=100000	# = (samples / 1000) seconds
outer=1
num_thread=$TOTAL_LOGICAL_CORES
victim=avx_mul
date=`date +"%m%d-%H%M"`
warmup=0 # 0 - No warm up, 1 - Warm up
warmup_in_background=0 # 0 - Warm up and only then driver, 1 - Warm up in parallel with driver
warmup_time=3 # minutes
values="0 4294967295"
values_count=2
