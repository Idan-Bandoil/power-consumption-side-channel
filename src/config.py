CONFIGURATIONS = '''TOTAL_PHYSICAL_CORES=`grep '^core id' /proc/cpuinfo | sort -u | wc -l`
TOTAL_LOGICAL_CORES=`grep '^core id' /proc/cpuinfo | wc -l`
samples={samples}	# = (samples / 1000) seconds
outer={outer}
num_thread={num_thread}
attacker_core_id={attacker_core_id}
victim={victim}
date={date}
warmup=0 # 0 - No warm up, 1 - Warm up
warmup_in_background=0 # 0 - Warm up and only then driver, 1 - Warm up in parallel with driver
warmup_time=3 # minutes
values={values}
values_count={values_count}
constant={constant}
'''

def binary_pattern(pattern='1010', length=32):
    # output is the word binary_pattern^(length/len(binary_pattern)))
    return int(pattern * int(length / len(pattern)), 2)

AVX_MUL_VALUES = f'"0 {(2**8)-1} {(2**16)-1} {(2**24)-1} {binary_pattern("1", 32)}"'
AVX_MUL_CONSTANT = binary_pattern("10", 32)

IMUL_VALUES = f'"0 {binary_pattern("1", 32)}"'
IMUL_CONSTANT = binary_pattern("10", 64)

def set_params( samples=100000, 
                outer=1, 
                num_thread='$TOTAL_LOGICAL_CORES', 
                attacker_core_id=0,
                victim='avx_mul',
                date='`date +"%m%d-%H%M"`', 
                values=AVX_MUL_VALUES,
                constant=None):
    global CONFIGURATIONS
    CONFIGURATIONS = CONFIGURATIONS.format(
        samples = samples, 
        outer = outer,
        num_thread=num_thread,
        attacker_core_id=attacker_core_id,
        victim = victim,
        date=date,
        values = values,
        values_count=len(values.split(' ')),
        constant=constant)
    
def config(file_name='configurations'):
    with open(file_name, 'w') as file:
        file.write(CONFIGURATIONS)

def main():
    set_params(samples=50000, victim='imul', values=IMUL_VALUES)
    config()

if __name__ == "__main__":
    main()

