import matplotlib.pyplot as plt
from itertools import groupby
import seaborn as sns
import numpy as np
import argparse
import os

def main():
    os.makedirs('plot', exist_ok=True)

    # Parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--energydist')
    parser.add_argument('figname')
    parser.add_argument('sample_count')
    
    args = parser.parse_args()
    energy_dist = args.energydist
    figname = args.figname
    sample_count = args.sample_count

    if (energy_dist):
        if energy_dist == '0':
            all_subdirs = [d[0] for d in os.walk('data') if d[0] != 'data']
            # put in energy_dist the most recent folder in data
            energy_dist = max(all_subdirs, key=os.path.getmtime) + '/'
        data = {}
        files = sorted(os.listdir(energy_dist))
        for key, group in groupby(files, key=lambda name: name[:name.find('.')]):
            data[key] = []
            for file in group:
                with open(energy_dist + file, 'r') as f:
                    energies = [round(float(x[:x.find(' ')]) * 1000, 8) for x in f.readlines()]
                data[key] += energies
        for key, energies in data.items():
            i = key.find('_')
            victim_name, number = key[:i], bin(int(key[i + 1:]))[2:]
            sns.distplot(energies, hist=False, kde=True, kde_kws = {'shade': True, 'linewidth': 3}, label=victim_name + ' ' + number)

        plt.legend(prop={'size': 16}, title = 'value')
        plt.title(f'{sample_count} samples')
        plt.xticks(np.arange(0, 100, 2))
        plt.xlim((0, 100))
        plt.xlabel('Power Consumption (W)')
        plt.ylabel('Density')

        plt.show()
        plt.savefig(f"./plot/{figname}.pdf", dpi=300)


if __name__ == "__main__":
    main()
