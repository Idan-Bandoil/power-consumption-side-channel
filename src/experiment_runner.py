#!/usr/bin/env python3
import os
import sys
import subprocess
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict
from collections import defaultdict

# Plotting Libraries
import matplotlib.pyplot as plt
import seaborn as sns

# ==============================================================================
# 1. CONFIGURATION & CONSTANTS
# ==============================================================================

# --- System & Hardware Settings ---
# Automatically detect cores
TOTAL_PHYSICAL_CORES = int(subprocess.check_output("grep '^core id' /proc/cpuinfo | sort -u | wc -l", shell=True))
TOTAL_LOGICAL_CORES = int(subprocess.check_output("grep '^core id' /proc/cpuinfo | wc -l", shell=True))

# Core Pinning Strategy
ATTACKER_CORE_ID = 0   # P-Core
VICTIM_CORE_ID = 2     # P-Core (Distinct from Attacker)

# --- Experiment Parameters ---
SAMPLES = 300        # Number of samples
OUTER_LOOPS = 1        # How many times to repeat the full batch
NUM_THREADS = 2 

# --- Experiment Targets ---
# Dictionary mapping the Victim Name to the Input Selectors to test
# Format: { "victim_binary_name": ["input_value_1", "input_value_2", ...] }
EXPERIMENTS: Dict[str, List[str]] = {
    "avx256mul": ["0", "4294967295"]  # 0 and 2^32-1 (All 0s vs All 1s)
}

# --- Paths ---
BASE_DIR = Path(__file__).parent.resolve()
BIN_DIR = BASE_DIR / "bin"
OUT_DIR = BASE_DIR / "out"
DATA_DIR = BASE_DIR / "data"
PLOT_DIR = BASE_DIR / "plot"

# ==============================================================================
# 2. LOGGING SETUP
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# ==============================================================================
# 3. HELPER FUNCTIONS
# ==============================================================================

def check_root():
    """Ensures the script is running with root privileges (required for MSR)."""
    if os.geteuid() != 0:
        logger.error("This script must be run as root (sudo).")
        sys.exit(1)

def run_command(cmd: List[str], cwd: Path = BASE_DIR, background: bool = False):
    """Executes a shell command with error handling."""
    cmd_str = " ".join(cmd)
    logger.debug(f"Executing: {cmd_str}")
    
    if background:
        return subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    try:
        result = subprocess.run(cmd, cwd=cwd, check=True, text=True, capture_output=True)
        return result
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {cmd_str}")
        logger.error(f"STDERR: {e.stderr}")
        sys.exit(1)

# ==============================================================================
# 4. PLOTTING FUNCTION (INTEGRATED)
# ==============================================================================

def generate_density_plot(data_path: Path, output_filename: str):
    """
    Reads .out files from data_path, groups them by victim/selector,
    and generates a Kernel Density Estimate (KDE) plot of Power consumption.
    """
    if not data_path.exists():
        logger.warning(f"Data path {data_path} does not exist. Skipping plot.")
        return

    logger.info(f"Generating plots from data in: {data_path}")
    
    # 1. Data Loading
    # Structure: plot_data["victim_name"]["selector_binary_string"] = [energy_values...]
    plot_data = defaultdict(list)
    
    # Filter for .out files only
    files = sorted([f for f in data_path.iterdir() if f.suffix == '.out'])
    
    for file_path in files:
        # Parse Filename: "victim_selector.out" -> "avx256mul_00.out"
        filename_stem = file_path.stem # removes .out
        
        # Split at the LAST underscore to separate name from selector
        last_underscore_index = filename_stem.rfind('_')
        if last_underscore_index == -1:
            continue
            
        victim_name = filename_stem[:last_underscore_index]
        selector_str = filename_stem[last_underscore_index + 1:]
        
        try:
            # Convert selector ID to Binary String for readable legend (e.g., 2 -> '10')
            selector_bin = bin(int(selector_str))[2:]
            group_key = f"{victim_name} (Input: {selector_bin})"
            
            # Read Data
            # Format: "0.00215 2300000" (Energy_Delta Frequency)
            with open(file_path, 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 1:
                        energy_joules = float(parts[0])
                        # Filter out exact zeros (artifacts of sampling speed) 
                        # Only filter if you want a cleaner graph; otherwise keep them.
                        if energy_joules > 0.000001:
                            # Convert Joules to Watts: P = E / t
                            # t = 1ms (0.001s). So P = E * 1000
                            power_watts = energy_joules * 1000.0
                            plot_data[group_key].append(power_watts)
                            
        except Exception as e:
            logger.error(f"Error processing file {file_path.name}: {e}")

    # 2. Plotting
    if not plot_data:
        logger.warning("No valid data found to plot.")
        return

    plt.figure(figsize=(10, 6))
    
    # Use Seaborn KDE Plot
    for label, energies in plot_data.items():
        if not energies: 
            continue
        sns.kdeplot(energies, fill=True, linewidth=3, label=label)

    # Styling
    plt.title(f'Power Consumption Distribution ({SAMPLES} samples)', fontsize=14)
    plt.xlabel('Power Consumption (Watts)', fontsize=12)
    plt.ylabel('Density', fontsize=12)
    plt.legend(prop={'size': 12}, title='Victim & Input')
    plt.grid(True, linestyle='--', alpha=0.6)
    
    # Ensure plot directory exists
    PLOT_DIR.mkdir(exist_ok=True)
    
    output_path = PLOT_DIR / f"{output_filename}.pdf"
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    logger.info(f"Plot saved to: {output_path}")
    
    plt.show() # Uncomment if running locally with a display

# ==============================================================================
# 5. MAIN EXPERIMENT PHASES
# ==============================================================================

def setup_environment():
    logger.info("--- Phase 1: Environment Setup ---")
    run_command(["sudo", "modprobe", "msr"])
    run_command(["make"])
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir()

def run_experiments():
    logger.info(f"--- Phase 3: Running Experiments (Samples: {SAMPLES}) ---")
    driver_path = BIN_DIR / "driver"
    input_file = BASE_DIR / "input.txt"

    for victim_name, selectors in EXPERIMENTS.items():
        logger.info(f"Targeting Victim: {victim_name}")
        with open(input_file, "w") as f:
            for val in selectors:
                f.write(f"{val}\n")
        
        cmd = [str(driver_path), str(NUM_THREADS), str(SAMPLES), str(OUTER_LOOPS), victim_name, str(ATTACKER_CORE_ID)]
        result = run_command(cmd)
        print(result.stdout)
        
    input_file.unlink(missing_ok=True)

def process_results():
    logger.info("--- Phase 4: Data Processing & Plotting ---")
    
    timestamp = datetime.now().strftime("%m%d-%H%M")
    data_destination = DATA_DIR / f"out-{timestamp}"
    
    # 1. Archive Raw Data
    if OUT_DIR.exists() and any(OUT_DIR.iterdir()):
        logger.info(f"Archiving data to: {data_destination}")
        shutil.copytree(OUT_DIR, data_destination)
    else:
        logger.warning("No output data found. Skipping processing.")
        return

    # 2. Generate Plot using internal function
    generate_density_plot(data_destination, f"hd-power-{timestamp}")

def cleanup():
    logger.info("--- Phase 5: Cleanup ---")
    run_command(["make", "clean"])
    # Do not unload MSR to prevent instability
    # run_command(["modprobe", "-r", "msr"]) 

# ==============================================================================
# 6. ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    check_root()
    try:
        setup_environment()
        run_experiments()
        process_results()
    except KeyboardInterrupt:
        logger.warning("\nExperiment interrupted.")
    finally:
        cleanup()
        logger.info("Done.")
