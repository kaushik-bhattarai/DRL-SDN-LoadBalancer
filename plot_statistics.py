#!/usr/bin/env python3
"""
Plots for Statistical Analysis JSON
Reads statistical_analysis.json and produces heatmaps or bar charts of p-values / Cohen's d.
"""
import argparse
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# ---------------------------------------------------------------------------
# Consistent colour map
# ---------------------------------------------------------------------------
COLOR_MAP = {
    "round_robin":           "#1f77b4",
    "weighted_round_robin":  "#ff7f0e",
    "random":                "#2ca02c",
    "least_connections":     "#d62728",
    "hash_based":            "#9467bd",
    "ecmp":                  "#8c564b",
    "drl":                   "#e377c2",
}

NICE_NAME = {
    "round_robin":           "Round Robin",
    "weighted_round_robin":  "Weighted RR",
    "random":                "Random",
    "least_connections":     "Least Conns",
    "hash_based":            "Hash-Based",
    "ecmp":                  "ECMP",
    "drl":                   "DRL",
}

def plot_effect_size(data, out_dir):
    """Plot Cohen's d for significant DRL wins across all scenarios"""
    plt.rcParams.update({"font.family": "sans-serif", "font.size": 10, "axes.grid": True, "grid.alpha": 0.3})
    
    # Collect data: (Scenario, Metric, Baseline) -> Cohen's d
    plot_data = []
    
    for scenario, metrics in data.items():
        for metric, algos in metrics.items():
            if metric == "higher_better":
                continue
            for algo, r in algos.items():
                if algo == "higher_better": continue
                if r.get("significant", False) and r.get("drl_better", False):
                    # It's a significant win for DRL
                    # Record the effect size (magnitude)
                    d = abs(r["cohens_d"])
                    plot_data.append((scenario.capitalize(), metric, algo, d))

    if not plot_data:
        print("No significant DRL wins to plot effect sizes for.")
        return

    # Group by baseline
    baselines = list(set([x[2] for x in plot_data]))
    baselines.sort()

    fig, ax = plt.subplots(figsize=(10, max(5, len(plot_data)*0.3)))
    
    y = np.arange(len(plot_data))
    labels = [f"{x[0]} - {x[1]} vs {NICE_NAME.get(x[2], x[2])}" for x in plot_data]
    effects = [x[3] for x in plot_data]
    colors = [COLOR_MAP.get(x[2], "#333") for x in plot_data]

    ax.barh(y, effects, color=colors, edgecolor="black", alpha=0.8)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_xlabel("|Cohen's d| (Effect Size)")
    ax.set_title("Significant Validations of DRL Improvement (Effect Size)")
    
    # Draw reference lines for effect sizes
    ax.axvline(0.2, color="gray", linestyle="--", alpha=0.5)
    ax.text(0.2, -1, "Small", rotation=90, color="gray", va="top")
    ax.axvline(0.5, color="gray", linestyle="--", alpha=0.5)
    ax.text(0.5, -1, "Medium", rotation=90, color="gray", va="top")
    ax.axvline(0.8, color="gray", linestyle="--", alpha=0.5)
    ax.text(0.8, -1, "Large", rotation=90, color="gray", va="top")

    plt.tight_layout()
    out_file = os.path.join(out_dir, "statistical_effect_sizes.png")
    fig.savefig(out_file, dpi=150)
    plt.close()
    print(f"Generated: {out_file}")

def main():
    parser = argparse.ArgumentParser("Plot statistical analysis results")
    parser.add_argument("--json", type=str, required=True, help="Path to statistical_analysis.json")
    args = parser.parse_args()

    if not os.path.exists(args.json):
        print(f"File not found: {args.json}")
        sys.exit(1)
        
    with open(args.json, "r") as f:
        data = json.load(f)

    out_dir = os.path.dirname(args.json) or "."
    plot_effect_size(data, out_dir)

if __name__ == "__main__":
    main()
