CONFIGURATIONS = '''TOTAL_PHYSICAL_CORES=`grep '^core id' /proc/cpuinfo | sort -u | wc -l`
TOTAL_LOGICAL_CORES=`grep '^core id' /proc/cpuinfo | wc -l`
samples={samples}	# = (samples / 1000) seconds
outer={outer}
num_thread={num_thread}
attacker_core={attacker_core}
victim={victim}
date={date}
warmup=0 # 0 - No warm up, 1 - Warm up
warmup_in_background=0 # 0 - Warm up and only then driver, 1 - Warm up in parallel with driver
warmup_time=3 # minutes
values={values}
values_count={values_count}
'''


def binary_pattern(pattern='1010', length=32):
    # output is the word binary_pattern^(length/len(binary_pattern)))
    return int(pattern * int(length / len(pattern)), 2)

AVX_MUL_VALUES = f'"0 {binary_pattern("1", 32)}"'

def set_params(samples=100000, outer=1, num_thread='$TOTAL_LOGICAL_CORES', victim='avx_mul',
                date='`date +"%m%d-%H%M"`', 
                values=AVX_MUL_VALUES):
    global CONFIGURATIONS
    CONFIGURATIONS = CONFIGURATIONS.format(
        samples = samples, 
        outer = outer,
        num_thread=num_thread,
        attacker_core=0,
        victim = victim,
        date=date,
        values = values,
        values_count=len(values.split(' ')))
    
def config(file_name='config.sh'):
    with open(file_name, 'w') as file:
        file.write(CONFIGURATIONS)

def main():
    set_params()
    config()

if __name__ == "__main__":
    main()