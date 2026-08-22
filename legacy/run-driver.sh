#!/usr/bin/env bash

sudo python3 ./config.py
source ./configurations
source ../venv/bin/activate

# Load MSR module
sudo modprobe msr
make

# Alert
echo "This script will take about $((((($samples/1000)*$outer*$values_count)/60) + ($warmup * (1-$warmup_in_background) * $warmup_time))) minutes. Reduce 'outer' if you want a shorter run."

### Warm Up ###
if [ $warmup = 1 ]; then
	echo "Warm up"
	if [ $warmup_in_background = 1 ]; then
		(stress-ng -q --matrix $TOTAL_LOGICAL_CORES -t ${warmup_time}m)&
	else
		stress-ng -q --matrix $TOTAL_LOGICAL_CORES -t ${warmup_time}m
	fi
fi

sudo rm -rf out
mkdir out

i=0
while [ $i -lt ${#victims[@]} ]; do
	sudo rm -rf input.txt

	for selector in ${values[$i]}; do
		echo $selector >> input.txt
	done

	echo "Running ${victims[$i]} with $num_thread threads"
	sudo ./bin/driver ${num_thread} ${samples} ${outer} ${victims[$i]}
	i=$((i+1))
done

cp -r out data/out-${date}

python3 ./plot-hd.py --energydist 0 hd-power

make clean