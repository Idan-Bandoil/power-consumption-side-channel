import matplotlib.pyplot as plt
from itertools import groupby
import seaborn as sns
import numpy as np
import argparse
import os

def parse_config():
    config = {}
    with open('configurations', 'r') as f:
        for line in f.readlines():
            if line[0] == '#':
                continue
            line = line[:line.find('#')]
            key, value = line.split('=')
            key = key.strip() # Remove trailing and leading spaces
            config[key] = value.strip()
    return config

def main():
    config = parse_config()

    # Prepare output directory
    try:
        os.makedirs('plot')
    except:
        pass

    # Parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--energydist')
    parser.add_argument('figname')
    args = parser.parse_args()
    energy_dist = args.energydist
    figname = args.figname

    if (energy_dist):
        if energy_dist == '0':
            all_subdirs = [d[0] for d in os.walk('data') if d[0] != 'data']
            # put in energy_dist the most recent folder in data
            energy_dist = max(all_subdirs, key=os.path.getmtime) + '/'
        data = {}
        files = sorted(os.listdir(energy_dist))
        for key, group in groupby(files, key=lambda name: name[name.find('_')+1:name.find('.')]):
            key = int(key)
            data[key] = []
            for file in group:
                with open(energy_dist + file, 'r') as f:
                    energies = [round(float(x[:x.find(' ')]) * 1000, 8) for x in f.readlines()]
                data[key] += energies
        for number, energies in data.items():
            label = '0' * (32 - len(bin(number)[2:])) + bin(number)[2:]
            sns.distplot(energies, hist=False, kde=True, kde_kws = {'shade': True, 'linewidth': 3}, label=label)

        plt.legend(prop={'size': 16}, title = 'value')
        plt.title(f'{config["samples"]} samples, {config["victim"]}')
        plt.xticks(np.arange(15, 50, 1))
        plt.xlim((15, 50))
        plt.xlabel('Power Consumption (W)')
        plt.ylabel('Density')
        plt.show()
        plt.savefig(f"./plot/{figname}.pdf", dpi=300)


if __name__ == "__main__":
    main()
