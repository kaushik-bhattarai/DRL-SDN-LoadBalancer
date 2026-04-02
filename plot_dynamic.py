#!/usr/bin/env python3
"""
Plotting script for the dynamic evaluation scenarios.
Reads results from subdirectories under --base-dir.
"""

import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

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
    fig.savefig(png, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {png}")

def get_algos(df):
    return [a for a in ALGO_ORDER if a in df["algorithm"].unique()]

# 1. Failure Recovery Plot
def plot_failure_recovery(base_dir, out_dir):
    path = os.path.join(base_dir, "failure", "results_raw.csv")
    if not os.path.exists(path): return
    df = pd.read_csv(path)
    
    _style()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    algos = get_algos(df)
    
    # Needs failed requests per sec: sum of selections to dead servers
    for algo in algos:
        sub = df[df["algorithm"] == algo]
        grouped = sub.groupby("second")
        
        # We need sum of h1_sels_diff + h2_sels_diff when alive == 0
        # Since raw_rows has h1_sels, we need to diff it per trial, then mean across trials
        
        def compute_failed(group):
            # group is one trial
            group = group.sort_values("second")
            h1_sels_diff = group["h1_sels"].diff().fillna(0).clip(lower=0)
            h2_sels_diff = group["h2_sels"].diff().fillna(0).clip(lower=0)
            fails = []
            for i, r in group.iterrows():
                f = 0
                if r["h1_alive"] == 0:
                    f += h1_sels_diff.loc[i]
                if r["h2_alive"] == 0:
                    f += h2_sels_diff.loc[i]
                fails.append(f)
            return pd.Series(fails, index=group["second"])
            
        # compute across trials
        failed_series = []
        for trial in sub["trial"].unique():
            tr = sub[sub["trial"] == trial]
            fs = compute_failed(tr)
            failed_series.append(fs)
        
        mean_failed = pd.concat(failed_series, axis=1).mean(axis=1)
        mean_fairness = grouped["fairness_alive"].mean()
        
        c = COLOR_MAP.get(algo, "#333")
        name = NICE_NAME.get(algo, algo)
        
        ax1.plot(mean_failed.index, mean_failed.values, label=name, color=c)
        ax2.plot(mean_fairness.index, mean_fairness.values, label=name, color=c)
        
    for ax in [ax1, ax2]:
        ax.axvline(30, color="red", linestyle="--", alpha=0.5)
        ax.axvline(90, color="green", linestyle="--", alpha=0.5)
        ax.axvline(120, color="red", linestyle="--", alpha=0.5)
        ax.axvline(150, color="green", linestyle="--", alpha=0.5)
        
    ax1.set_ylabel("Failed Requests / sec")
    ax1.set_title("Routing to Dead Servers")
    ax1.legend(fontsize=8, ncol=4, loc="upper right")
    
    ax2.set_xlabel("Time (s)")
    ax2.set_ylabel("Fairness (Alive Servers)")
    ax2.set_title("Load Distribution Fairness")
    ax2.set_ylim(0, 1.05)
    
    _save(fig, out_dir, "failure_recovery_plot")

# 2. Heterogeneous Capacity Plot
def plot_heterogeneous(base_dir, out_dir):
    path_raw = os.path.join(base_dir, "heterogeneous", "results_raw.csv")
    path_sum = os.path.join(base_dir, "heterogeneous", "results_scenario_summary.csv")
    if not os.path.exists(path_sum): return
    
    df_sum = pd.read_csv(path_sum)
    _style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
    
    algos = get_algos(df_sum)
    mean_share = [df_sum[df_sum["algorithm"] == a]["h1_traffic_share"].mean() for a in algos]
    mean_rtt = [df_sum[df_sum["algorithm"] == a]["avg_rtt_weighted"].mean() for a in algos]
    colors = [COLOR_MAP.get(a, "#333") for a in algos]
    labels = [NICE_NAME.get(a, a) for a in algos]
    
    x = np.arange(len(algos))
    ax1.bar(x, mean_share, color=colors, edgecolor="white")
    ax1.axhline(0.20, color="black", linestyle="--", label="Optimal (0.20)")
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=45, ha="right")
    ax1.set_ylabel("h1 Traffic Share")
    ax1.set_title("Traffic Distribution (h1 is 0.5x capacity)")
    ax1.legend()
    
    ax2.bar(x, mean_rtt, color=colors, edgecolor="white")
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=45, ha="right")
    ax2.set_ylabel("Weighted Avg RTT (ms)")
    ax2.set_title("Latency Impact of Misrouting")
    
    _save(fig, out_dir, "heterogeneous_plot")

# 3. Bursty Plot
def plot_bursty(base_dir, out_dir):
    path_raw = os.path.join(base_dir, "bursty", "results_raw.csv")
    if not os.path.exists(path_raw): return
    df = pd.read_csv(path_raw)
    
    _style()
    fig, ax = plt.subplots(figsize=(12, 4))
    algos = get_algos(df)
    
    for algo in algos:
        sub = df[df["algorithm"] == algo]
        grouped = sub.groupby("second")
        mean = grouped["avg_rtt"].mean()
        std = grouped["avg_rtt"].std().fillna(0)
        c = COLOR_MAP.get(algo, "#333")
        name = NICE_NAME.get(algo, algo)
        ax.plot(mean.index, mean.values, label=name, color=c)
        ax.fill_between(mean.index, mean.values - std.values, mean.values + std.values, alpha=0.15, color=c)
        
    # Shade burst windows
    # burst when sec % 30 < 15
    for start in range(0, 120, 30):
        ax.axvspan(start, start + 15, color='gray', alpha=0.15)
        
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Avg RTT (ms)")
    ax.set_title("Latency Under Bursty Saturation")
    ax.legend(fontsize=8, ncol=4, loc="upper right")
    
    _save(fig, out_dir, "bursty_plot")

# 4. Combined Stress
def plot_combined_stress(base_dir, out_dir):
    path_raw = os.path.join(base_dir, "combined", "results_raw.csv")
    path_sum = os.path.join(base_dir, "combined", "results_scenario_summary.csv")
    if not os.path.exists(path_raw): return
    df_raw = pd.read_csv(path_raw)
    df_sum = pd.read_csv(path_sum)
    
    _style()
    fig, axes = plt.subplots(2, 2, figsize=(14, 8))
    ax_fair, ax_rtt, ax_fail, ax_comp = axes.flatten()
    
    algos = get_algos(df_raw)
    
    # We plot the timeseries for the first three panels
    # To save logic, we just plot fairness and RTT
    for algo in algos:
        sub = df_raw[df_raw["algorithm"] == algo]
        grouped = sub.groupby("second")
        
        c = COLOR_MAP.get(algo, "#333")
        name = NICE_NAME.get(algo, algo)
        
        f_mean = grouped["fairness_alive"].mean()
        ax_fair.plot(f_mean.index, f_mean.values, label=name, color=c)
        
        r_mean = grouped["avg_rtt"].mean()
        ax_rtt.plot(r_mean.index, r_mean.values, label=name, color=c)
        
    # Normalize composite components from df_sum
    # composite = (fairness * 0.35) + ((1 - norm_latency) * 0.30) + ((1 - norm_failed_rate) * 0.35)
    mean_sum = df_sum.groupby("algorithm").mean().reset_index()
    
    fairs = mean_sum.set_index("algorithm")["stress_fairness_alive"]
    lats = mean_sum.set_index("algorithm")["stress_avg_rtt_ms"]
    fails = mean_sum.set_index("algorithm")["stress_failed_rate"]
    
    norm_lats = (lats - lats.min()) / (lats.max() - lats.min() + 1e-9)
    norm_fails = (fails - fails.min()) / (fails.max() - fails.min() + 1e-9)
    
    comp_scores = (fairs * 0.35) + ((1 - norm_lats) * 0.30) + ((1 - norm_fails) * 0.35)
    
    colors = [COLOR_MAP.get(a, "#333") for a in algos]
    labels = [NICE_NAME.get(a, a) for a in algos]
    
    x = np.arange(len(algos))
    ax_comp.bar(x, [comp_scores[a] for a in algos], color=colors, edgecolor="white")
    ax_comp.set_xticks(x)
    ax_comp.set_xticklabels(labels, rotation=45, ha="right")
    ax_comp.set_title("Composite Stress Score")
    ax_comp.set_ylim(0, 1)
    
    # Fill in failed_requests timeseries using identical logic as failure
    # To keep plotting code concise, we'll plot fails bar chart
    ax_fail.bar(x, [fails[a] for a in algos], color=colors, edgecolor="white")
    ax_fail.set_xticks(x)
    ax_fail.set_xticklabels(labels, rotation=45, ha="right")
    ax_fail.set_title("Total Dead-Server Routes (Rate)")
    
    for ax in [ax_fair, ax_rtt]:
        ax.axvline(60, color="gray", linestyle="--", alpha=0.5)
        ax.axvline(90, color="gray", linestyle="--", alpha=0.5)
        ax.axvline(120, color="gray", linestyle="--", alpha=0.5)
        ax.axvline(150, color="gray", linestyle="--", alpha=0.5)
        ax.set_xlabel("Time (s)")
        
    ax_fair.set_title("Fairness (Alive Servers)")
    ax_fair.legend(fontsize=7, ncol=3)
    ax_rtt.set_title("Average RTT (ms)")
    
    plt.tight_layout()
    _save(fig, out_dir, "combined_stress_plot")

# 5. Updated Summary Heatmap
def plot_updated_heatmap(base_dir, out_dir):
    # Needs metrics from all scenarios
    # - static: avg_rtt, fairness, throughput
    # - failure: failed_request_count
    # - heterogeneous: h1_share (error from 0.20)
    # - bursty: p95_rtt_burst
    # - combined: comp_scores (we recalculate here)
    
    metrics_map = {}
    algos = ALGO_ORDER
    for a in algos:
        metrics_map[a] = {}
        
    def load_val(scene, col, is_error=False, val_target=0):
        path = os.path.join(base_dir, scene, "results_scenario_summary.csv")
        if not os.path.exists(path): return False
        df = pd.read_csv(path).groupby("algorithm").mean().to_dict()[col]
        for a in algos:
            if a in df:
                if is_error:
                    metrics_map[a][f"{scene}_{col}"] = abs(df[a] - val_target)
                else:
                    metrics_map[a][f"{scene}_{col}"] = df[a]
        return True

    # Loading data
    path_static_raw = os.path.join(base_dir, "static", "results_raw.csv")
    if os.path.exists(path_static_raw):
        df = pd.read_csv(path_static_raw).groupby("algorithm").mean()
        for a in algos:
            if a in df.index:
                metrics_map[a]["static_avg_rtt"] = df.loc[a, "avg_rtt"]
                metrics_map[a]["static_fairness"] = df.loc[a, "fairness_index"]

    load_val("failure", "failed_request_count")
    load_val("heterogeneous", "h1_traffic_share", is_error=True, val_target=0.20)
    load_val("bursty", "p95_rtt_burst")
    
    path_comb = os.path.join(base_dir, "combined", "results_scenario_summary.csv")
    if os.path.exists(path_comb):
        df_sum = pd.read_csv(path_comb).groupby("algorithm").mean()
        
        fairs = df_sum["stress_fairness_alive"]
        lats = df_sum["stress_avg_rtt_ms"]
        fails = df_sum["stress_failed_rate"]
        
        norm_lats = (lats - lats.min()) / (lats.max() - lats.min() + 1e-9)
        norm_fails = (fails - fails.min()) / (fails.max() - fails.min() + 1e-9)
        
        comp_scores = (fairs * 0.35) + ((1 - norm_lats) * 0.30) + ((1 - norm_fails) * 0.35)
        for a in algos:
            if a in comp_scores:
                metrics_map[a]["composite_stress_score"] = comp_scores[a]

    # Filter out empty algorithms
    algos = [a for a in algos if metrics_map[a]]
    if not algos: return
    
    metric_cols = [
        ("static_avg_rtt", False),       # lower is better
        ("static_fairness", True),       # higher is better
        ("failure_failed_request_count", False), 
        ("heterogeneous_h1_traffic_share", False), # Error from 0.20, lower is better
        ("bursty_p95_rtt_burst", False),
        ("composite_stress_score", True)
    ]
    
    # Filter only available metrics
    available_cols = []
    for c, _ in metric_cols:
        if any(c in metrics_map[a] for a in algos):
            available_cols.append(c)
            
    if not available_cols: return
    
    data = np.zeros((len(algos), len(available_cols)))
    for i, a in enumerate(algos):
        for j, c in enumerate(available_cols):
            data[i, j] = metrics_map[a].get(c, 0)
            
    norm = np.zeros_like(data)
    for j, c in enumerate(available_cols):
        col = data[:, j]
        mn, mx = col.min(), col.max()
        if mx - mn < 1e-9:
            norm[:, j] = 1.0
        else:
            norm[:, j] = (col - mn) / (mx - mn)
            
        is_higher_better = dict(metric_cols)[c]
        if not is_higher_better:
            norm[:, j] = 1.0 - norm[:, j] # invert so green is always better
            
    _style()
    fig, ax = plt.subplots(figsize=(12, 6))
    cmap = LinearSegmentedColormap.from_list("rg", ["#d62728", "#f5f5f5", "#2ca02c"])
    
    im = ax.imshow(norm, cmap=cmap, aspect="auto", vmin=0, vmax=1)
    
    nice_cols = [c.replace("_", "\n") for c in available_cols]
    ax.set_xticks(np.arange(len(available_cols)))
    ax.set_xticklabels(nice_cols, fontsize=9)
    ax.set_yticks(np.arange(len(algos)))
    ax.set_yticklabels([NICE_NAME.get(a, a) for a in algos], fontsize=9)
    
    for i in range(len(algos)):
        for j in range(len(available_cols)):
            val = data[i, j]
            txt = f"{val:.3f}" if val < 10 else f"{val:.0f}"
            ax.text(j, i, txt, ha="center", va="center", fontsize=8,
                    color="black" if 0.3 < norm[i, j] < 0.7 else "white")
                    
    fig.colorbar(im, ax=ax, label="Score (1 = best)")
    ax.set_title("Comprehensive Evaluation Heatmap (Green = Better)")
    plt.tight_layout()
    _save(fig, out_dir, "updated_summary_heatmap")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", type=str, default="comparison_results")
    args = parser.parse_args()
    
    out_dir = args.base_dir
    os.makedirs(out_dir, exist_ok=True)
    
    print(f"Generating dynamic plots from {args.base_dir} ...")
    plot_failure_recovery(args.base_dir, out_dir)
    plot_heterogeneous(args.base_dir, out_dir)
    plot_bursty(args.base_dir, out_dir)
    plot_combined_stress(args.base_dir, out_dir)
    plot_updated_heatmap(args.base_dir, out_dir)
    
    print("\n✅ All dynamic plots generated!")

if __name__ == "__main__":
    main()
