import matplotlib.pyplot as plt
from itertools import groupby
import seaborn as sns
import numpy as np
import argparse
import config
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
        plt.title(f'{config["samples"]} samples, {config["victims"]}')
        plt.xticks(np.arange(0, 100, 2))
        plt.xlim((0, 100))
        plt.xlabel('Power Consumption (W)')
        plt.ylabel('Density')

        total_energies = sum(energies)
        dist_percentage = round((len(energies) / total_energies) * 100, 2)
        # add the percentage to the plot
        ax = plt.gca()
        y_min, y_max = ax.get_ylim()
        x_min, x_max = ax.get_xlim()
        x_text = x_max - (x_max - x_min) * 0.1  # adjust the x position of the text
        y_text = y_max - (y_max - y_min) * 0.1  # adjust the y position of the text
        plt.text(x_text, y_text, f"{dist_percentage}%", ha='right', va='top', fontsize=12)

        plt.show()
        plt.savefig(f"./plot/{figname}.pdf", dpi=300)


if __name__ == "__main__":
    main()
