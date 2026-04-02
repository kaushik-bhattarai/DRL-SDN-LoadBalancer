#!/usr/bin/env python3
"""
Plotting script for the comparative analysis harness.

Reads results_raw.csv and results_summary.csv produced by
comparison_runner.py, generates 6 publication-quality figures
(PNG 150dpi + PDF) with consistent algorithm colours.

Usage:
    python3 plot_comparison.py --input-dir comparison_results/
"""

import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Consistent colour map for all figures
# ---------------------------------------------------------------------------
COLOR_MAP = {
    "round_robin":           "#1f77b4",  # blue
    "weighted_round_robin":  "#ff7f0e",  # orange
    "random":                "#2ca02c",  # green
    "least_connections":     "#d62728",  # red
    "hash_based":            "#9467bd",  # purple
    "ecmp":                  "#8c564b",  # brown
    "drl":                   "#e377c2",  # pink
}

ALGO_ORDER = [
    "round_robin", "weighted_round_robin", "random",
    "least_connections", "hash_based", "ecmp", "drl",
]

NICE_NAME = {
    "round_robin":           "Round Robin",
    "weighted_round_robin":  "Weighted RR",
    "random":                "Random",
    "least_connections":     "Least Conns",
    "hash_based":            "Hash-Based",
    "ecmp":                  "ECMP",
    "drl":                   "DRL (DQN)",
}


def _style():
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.size": 11,
        "axes.grid": True,
        "grid.alpha": 0.3,
    })


def _save(fig, out_dir, name):
    png = os.path.join(out_dir, f"{name}.png")
    pdf = os.path.join(out_dir, f"{name}.pdf")
    fig.savefig(png, dpi=150, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {png}, {pdf}")


def _algos_in(df):
    """Return algorithm list preserving ALGO_ORDER, filtered to available."""
    return [a for a in ALGO_ORDER if a in df["algorithm"].unique()]


# ---------------------------------------------------------------------------
# 1. Fairness vs Time
# ---------------------------------------------------------------------------

def plot_fairness_vs_time(df, out_dir):
    _style()
    fig, ax = plt.subplots(figsize=(12, 5))
    algos = _algos_in(df)

    for algo in algos:
        sub = df[df["algorithm"] == algo]
        grouped = sub.groupby("second")["fairness_index"]
        mean = grouped.mean()
        std = grouped.std().fillna(0)
        t = mean.index.values

        c = COLOR_MAP.get(algo, "#333")
        ax.plot(t, mean.values, label=NICE_NAME.get(algo, algo), color=c)
        ax.fill_between(t, mean.values - std.values, mean.values + std.values,
                        alpha=0.15, color=c)

        # Stabilization marker: first 15 consecutive seconds ≥ 0.85
        vals = mean.values
        window = 15
        for i in range(len(vals) - window):
            if all(v >= 0.85 for v in vals[i:i+window]):
                ax.axvline(t[i], color=c, linestyle=":", alpha=0.5)
                ax.annotate(f"{NICE_NAME.get(algo,'')[:5]} stab",
                            (t[i], 0.87), fontsize=7, color=c, rotation=90,
                            va="bottom")
                break

    ax.axhline(0.85, color="gray", linestyle="--", alpha=0.5, label="Threshold (0.85)")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Jain's Fairness Index")
    ax.set_ylim(0, 1.05)
    ax.set_title("Fairness Over Time — All Algorithms")
    ax.legend(fontsize=8, ncol=4, loc="lower right")
    _save(fig, out_dir, "fairness_vs_time")


# ---------------------------------------------------------------------------
# 2. Latency vs Load (time)
# ---------------------------------------------------------------------------

def plot_latency_vs_load(df, out_dir):
    _style()
    fig, ax = plt.subplots(figsize=(12, 5))
    algos = _algos_in(df)

    for algo in algos:
        sub = df[df["algorithm"] == algo]
        grouped = sub.groupby("second")["avg_rtt"]
        mean = grouped.mean()
        std = grouped.std().fillna(0)
        t = mean.index.values
        c = COLOR_MAP.get(algo, "#333")
        ax.plot(t, mean.values, label=NICE_NAME.get(algo, algo), color=c)
        ax.fill_between(t, mean.values - std.values, mean.values + std.values,
                        alpha=0.15, color=c)

    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Average RTT (ms)")
    ax.set_title("Latency Over Time — All Algorithms")
    ax.legend(fontsize=8, ncol=4, loc="upper left")
    _save(fig, out_dir, "latency_vs_load")


# ---------------------------------------------------------------------------
# 3. Throughput Bar Chart
# ---------------------------------------------------------------------------

def plot_throughput_bar(df, out_dir):
    _style()
    algos = _algos_in(df)
    means, stds, colors = [], [], []
    labels = []

    for algo in algos:
        sub = df[df["algorithm"] == algo]
        means.append(sub["throughput"].mean())
        stds.append(sub["throughput"].std())
        colors.append(COLOR_MAP.get(algo, "#333"))
        labels.append(NICE_NAME.get(algo, algo))

    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.arange(len(labels))
    ax.bar(x, means, yerr=stds, color=colors, capsize=4, edgecolor="white")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_ylabel("Mean Throughput (req/s)")
    ax.set_title("Throughput Comparison")
    _save(fig, out_dir, "throughput_bar")


# ---------------------------------------------------------------------------
# 4. Connection Distribution (stacked area per algorithm)
# ---------------------------------------------------------------------------

def plot_connection_distribution(df, out_dir):
    _style()
    algos = _algos_in(df)
    n = len(algos)
    if n == 0:
        return
    cols_per_row = min(n, 4)
    rows = (n + cols_per_row - 1) // cols_per_row
    fig, axes = plt.subplots(rows, cols_per_row, figsize=(5 * cols_per_row, 4 * rows),
                              squeeze=False, sharex=True)
    axes_flat = axes.flatten()

    for idx, algo in enumerate(algos):
        ax = axes_flat[idx]
        sub = df[df["algorithm"] == algo]
        grouped = sub.groupby("second")[["h1_conns", "h2_conns", "h3_conns"]].mean()
        t = grouped.index.values
        total = grouped.sum(axis=1).replace(0, 1)
        h1_share = grouped["h1_conns"] / total
        h2_share = grouped["h2_conns"] / total
        h3_share = grouped["h3_conns"] / total

        ax.stackplot(t, h1_share.values, h2_share.values, h3_share.values,
                      labels=["h1", "h2", "h3"],
                      colors=["#66c2a5", "#fc8d62", "#8da0cb"], alpha=0.8)
        ax.set_title(NICE_NAME.get(algo, algo), fontsize=10)
        ax.set_ylim(0, 1)
        if idx == 0:
            ax.legend(fontsize=7, loc="lower right")

    for idx in range(n, len(axes_flat)):
        axes_flat[idx].set_visible(False)

    fig.supxlabel("Time (s)")
    fig.supylabel("Connection Share")
    fig.suptitle("Connection Distribution per Algorithm", fontsize=13)
    plt.tight_layout(rect=[0, 0.02, 1, 0.96])
    _save(fig, out_dir, "connection_distribution")


# ---------------------------------------------------------------------------
# 5. Decision Overhead (horizontal bar, log scale)
# ---------------------------------------------------------------------------

def plot_decision_overhead(df, out_dir):
    _style()
    algos = _algos_in(df)
    means, colors, labels = [], [], []

    for algo in algos:
        sub = df[df["algorithm"] == algo]
        m = sub["decision_latency_us"].mean()
        means.append(max(m, 1))  # avoid log(0)
        colors.append(COLOR_MAP.get(algo, "#333"))
        labels.append(NICE_NAME.get(algo, algo))

    fig, ax = plt.subplots(figsize=(8, 4))
    y = np.arange(len(labels))
    bars = ax.barh(y, means, color=colors, edgecolor="white")
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_xscale("log")
    ax.set_xlabel("Decision Latency (µs, log scale)")
    ax.set_title("Decision Overhead per Algorithm")

    # Annotate DRL bar explicitly
    for i, algo in enumerate(algos):
        if algo == "drl":
            ax.annotate(f"  DRL: {means[i]:.0f}µs", (means[i], i),
                        fontsize=9, va="center", fontweight="bold",
                        color=COLOR_MAP["drl"])

    ax.invert_yaxis()
    _save(fig, out_dir, "decision_overhead")


# ---------------------------------------------------------------------------
# 6. Summary Heatmap
# ---------------------------------------------------------------------------

def plot_summary_heatmap(df, out_dir):
    _style()
    algos = _algos_in(df)
    metrics = ["fairness_index", "throughput", "avg_rtt", "p95_rtt",
               "max_imbalance", "decision_latency_us"]
    # higher-is-better: fairness, throughput
    # lower-is-better: avg_rtt, p95_rtt, max_imbalance, decision_latency_us
    invert = {"avg_rtt", "p95_rtt", "max_imbalance", "decision_latency_us"}

    data = np.zeros((len(algos), len(metrics)))
    for i, algo in enumerate(algos):
        sub = df[df["algorithm"] == algo]
        for j, m in enumerate(metrics):
            data[i, j] = sub[m].mean()

    # Normalize each column 0–1
    norm = np.zeros_like(data)
    for j, m in enumerate(metrics):
        col = data[:, j]
        mn, mx = col.min(), col.max()
        if mx - mn < 1e-9:
            norm[:, j] = 1.0
        else:
            norm[:, j] = (col - mn) / (mx - mn)
        if m in invert:
            norm[:, j] = 1.0 - norm[:, j]

    fig, ax = plt.subplots(figsize=(10, 5))
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("rg", ["#d62728", "#f5f5f5", "#2ca02c"])

    im = ax.imshow(norm, cmap=cmap, aspect="auto", vmin=0, vmax=1)
    ax.set_xticks(np.arange(len(metrics)))
    ax.set_xticklabels([m.replace("_", "\n") for m in metrics], fontsize=9)
    ax.set_yticks(np.arange(len(algos)))
    ax.set_yticklabels([NICE_NAME.get(a, a) for a in algos], fontsize=9)

    # Annotate cells with raw values
    for i in range(len(algos)):
        for j in range(len(metrics)):
            val = data[i, j]
            txt = f"{val:.2f}" if val < 1000 else f"{val:.0f}"
            ax.text(j, i, txt, ha="center", va="center", fontsize=8,
                    color="black" if 0.3 < norm[i, j] < 0.7 else "white")

    fig.colorbar(im, ax=ax, label="Score (1 = best)")
    ax.set_title("Algorithm Comparison Heatmap (green = better)")
    plt.tight_layout()
    _save(fig, out_dir, "summary_heatmap")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Plot comparison results.")
    parser.add_argument("--input-dir", type=str, default="comparison_results",
                        help="Directory containing results_raw.csv")
    args = parser.parse_args()

    raw_path = os.path.join(args.input_dir, "results_raw.csv")
    if not os.path.isfile(raw_path):
        print(f"ERROR: {raw_path} not found. Run comparison_runner.py first.")
        sys.exit(1)

    df = pd.read_csv(raw_path)
    print(f"Loaded {len(df)} rows from {raw_path}")
    print(f"Algorithms: {sorted(df['algorithm'].unique())}")

    out_dir = args.input_dir
    print(f"\nGenerating plots → {out_dir}/\n")

    plot_fairness_vs_time(df, out_dir)
    plot_latency_vs_load(df, out_dir)
    plot_throughput_bar(df, out_dir)
    plot_connection_distribution(df, out_dir)
    plot_decision_overhead(df, out_dir)
    plot_summary_heatmap(df, out_dir)

    print("\n✅ All plots generated!")


if __name__ == "__main__":
    main()
