CONFIGURATIONS = '''TOTAL_PHYSICAL_CORES=`grep '^core id' /proc/cpuinfo | sort -u | wc -l`
TOTAL_LOGICAL_CORES=`grep '^core id' /proc/cpuinfo | wc -l`
samples={samples}	# = (samples / 1000) seconds
outer={outer}
num_thread={num_thread}
attacker_core_id={attacker_core_id}
victims={victims}
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

NOP = 'nop'
SHL = 'shl'
IMUL = 'imul'
AVX256MUL = 'avx256mul'
AVX512MUL = 'avx512mul'
PCLMUL = 'pclmul512'

NOP_VALUES = '0'
SHL_VALUES = f'"0 {2**64-1}"'
IMUL_VALUES = f'"0 {2**32-1}"'
AVX256_MUL_VALUES = f'"0 {2**32-1}"'
AVX512_MUL_VALUES = f'"0 {2**64-1}"'

IMUL_CONSTANT = binary_pattern("10", 64)
AVX256_MUL_CONSTANT = binary_pattern("10", 32)
AVX512_MUL_CONSTANT = binary_pattern("10", 64)

def set_params( samples=100000, 
                outer=1, 
                num_thread='$TOTAL_LOGICAL_CORES', 
                attacker_core_id=0,
                victims=[AVX256MUL],
                date='`date +"%m%d-%H%M"`', 
                values=[AVX256_MUL_VALUES],
                constant=None):
    global CONFIGURATIONS
    assert len(values) == len(victims)

    CONFIGURATIONS = CONFIGURATIONS.format(
        samples = samples, 
        outer = outer,
        num_thread=num_thread,
        attacker_core_id=attacker_core_id,
        victims = '(' + ' '.join(victims) + ')',
        date=date,
        values = '(' + ' '.join(values) + ')',
        values_count=sum(len(x.split(' ')) for x in values),
        constant=constant)
    
def config(file_name='configurations'):
    with open(file_name, 'w') as file:
        file.write(CONFIGURATIONS)

def main():
    set_params(samples=30000, victims=[NOP, SHL, IMUL, AVX256MUL], values=[NOP_VALUES, SHL_VALUES, IMUL_VALUES, AVX256_MUL_VALUES])
    config()

if __name__ == "__main__":
    main()

