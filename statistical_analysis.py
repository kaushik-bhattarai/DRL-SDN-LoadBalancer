#!/usr/bin/env python3
"""
Statistical Analysis of DRL vs Baseline Comparison Results.

Reads per-scenario CSV results from the comparison harness, computes
per-algorithm summary statistics, and performs pairwise hypothesis tests
(DRL vs each baseline).  Automatically selects Welch's t-test or
Mann-Whitney U depending on normality (Shapiro-Wilk).

Usage:
    python3 statistical_analysis.py [comparison_results_dir]
    python3 statistical_analysis.py comparison_results_v2 --alpha 0.01
"""

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
from scipy import stats

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALGORITHMS = [
    "round_robin",
    "weighted_round_robin",
    "random",
    "least_connections",
    "hash_based",
    "ecmp",
    "drl",
]

# Metrics to test per scenario.  For static, we read from the raw CSV
# (per-second rows) and aggregate per trial.  For dynamic scenarios, we
# read from the scenario summary CSV that has one row per (algo, trial).
SCENARIO_METRICS = {
    "static": {
        "source": "raw",               # Aggregate from per-second raw CSV
        "file": "results_raw.csv",
        "metrics": {
            "fairness_index": {"agg": "mean", "higher_better": True},
            "avg_rtt":        {"agg": "mean", "higher_better": False},
            "throughput":     {"agg": "sum",  "higher_better": True},
        },
    },
    "failure": {
        "source": "summary",
        "file": "results_scenario_summary.csv",
        "metrics": {
            "time_to_adapt":         {"higher_better": False},
            "failed_request_count":  {"higher_better": False},
            "fairness_among_alive":  {"higher_better": True},
        },
    },
    "heterogeneous": {
        "source": "summary",
        "file": "results_scenario_summary.csv",
        "metrics": {
            "h1_traffic_share":  {"higher_better": False},
            "avg_rtt_weighted":  {"higher_better": False},
            "throughput_loss":   {"higher_better": False},
        },
    },
    "bursty": {
        "source": "summary",
        "file": "results_scenario_summary.csv",
        "metrics": {
            "p95_rtt_burst":             {"higher_better": False},
            "queue_saturation_events":   {"higher_better": False},
            "recovery_time":             {"higher_better": False},
        },
    },
    "combined": {
        "source": "summary",
        "file": "results_scenario_summary.csv",
        "metrics": {
            "stress_failed_rate":     {"higher_better": False},
            "stress_fairness_alive":  {"higher_better": True},
            "stress_avg_rtt_ms":      {"higher_better": False},
        },
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normality_test(data, alpha=0.05):
    """Shapiro-Wilk normality test.  Returns True if data appears normal."""
    if len(data) < 8:
        return False           # Too few samples for a reliable test
    _, p = stats.shapiro(data)
    return p > alpha


def pairwise_test(drl_vals, baseline_vals, alpha=0.05):
    """
    Choose Welch's t-test or Mann-Whitney U based on normality.
    Returns a result dict.
    """
    drl_normal = normality_test(drl_vals, alpha)
    base_normal = normality_test(baseline_vals, alpha)

    if drl_normal and base_normal:
        stat, p = stats.ttest_ind(drl_vals, baseline_vals, equal_var=False)
        test_name = "Welch's t-test"
    else:
        stat, p = stats.mannwhitneyu(
            drl_vals, baseline_vals, alternative="two-sided"
        )
        test_name = "Mann-Whitney U"

    # Effect size — Cohen's d (pooled std)
    pooled_std = np.sqrt(
        (np.std(drl_vals, ddof=1) ** 2 + np.std(baseline_vals, ddof=1) ** 2) / 2
    )
    cohens_d = (
        (np.mean(drl_vals) - np.mean(baseline_vals)) / pooled_std
        if pooled_std > 0
        else 0.0
    )

    return {
        "test": test_name,
        "statistic": float(stat),
        "p_value": float(p),
        "significant": p < alpha,
        "cohens_d": round(float(cohens_d), 4),
        "drl_mean": round(float(np.mean(drl_vals)), 6),
        "drl_std": round(float(np.std(drl_vals, ddof=1)), 6),
        "drl_n": len(drl_vals),
        "baseline_mean": round(float(np.mean(baseline_vals)), 6),
        "baseline_std": round(float(np.std(baseline_vals, ddof=1)), 6),
        "baseline_n": len(baseline_vals),
    }


def _aggregate_static_raw(df):
    """
    Given a per-second raw CSV for the static scenario, aggregate each
    (algorithm, trial) pair into a single row with mean/sum metrics.
    Returns a DataFrame with one row per (algorithm, trial).
    """
    rows = []
    for (algo, trial), group in df.groupby(["algorithm", "trial"]):
        row = {"algorithm": algo, "trial": trial}
        for metric, spec in SCENARIO_METRICS["static"]["metrics"].items():
            if metric not in group.columns:
                continue
            vals = group[metric].dropna()
            if spec["agg"] == "mean":
                row[metric] = vals.mean()
            elif spec["agg"] == "sum":
                row[metric] = vals.sum()
            else:
                row[metric] = vals.mean()
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyse_scenario(scenario_dir, scenario_name, alpha=0.05):
    """Analyse one scenario's results.  Returns nested dict of comparisons."""
    spec = SCENARIO_METRICS.get(scenario_name)
    if spec is None:
        return {}

    csv_path = os.path.join(scenario_dir, spec["file"])
    if not os.path.isfile(csv_path):
        print(f"  ⚠️  File not found: {csv_path}")
        return {}

    df = pd.read_csv(csv_path)

    # For static, we aggregate the per-second raw rows into per-trial values
    if spec["source"] == "raw":
        df = _aggregate_static_raw(df)

    results = {}
    for metric, mspec in spec["metrics"].items():
        if metric not in df.columns:
            print(f"  ⚠️  Metric '{metric}' not found in columns {list(df.columns)}")
            continue

        drl_data = df[df["algorithm"] == "drl"][metric].dropna().values
        if len(drl_data) < 3:
            print(f"  ⚠️  Only {len(drl_data)} DRL samples for {metric} — skipping")
            continue

        results[metric] = {"higher_better": mspec.get("higher_better", False)}

        for algo in ALGORITHMS:
            if algo == "drl":
                continue
            base_data = df[df["algorithm"] == algo][metric].dropna().values
            if len(base_data) < 3:
                continue
            r = pairwise_test(drl_data, base_data, alpha=alpha)
            r["drl_better"] = (
                (r["drl_mean"] > r["baseline_mean"])
                if mspec.get("higher_better")
                else (r["drl_mean"] < r["baseline_mean"])
            )
            results[metric][algo] = r

    return results


def print_report(all_results, alpha):
    """Pretty-print the statistical analysis report."""
    print("\n" + "=" * 78)
    print("  STATISTICAL ANALYSIS REPORT")
    print(f"  Significance level α = {alpha}")
    print(f"  Generated: {datetime.now().isoformat()}")
    print("=" * 78)

    # Summary counters
    total_tests = 0
    sig_drl_wins = 0
    sig_drl_losses = 0
    non_sig = 0

    for scenario, metrics in all_results.items():
        print(f"\n{'─' * 78}")
        print(f"  Scenario: {scenario.upper()}")
        print(f"{'─' * 78}")

        for metric, data in metrics.items():
            if metric == "higher_better":
                continue
            higher_better = data.get("higher_better", False)
            direction = "↑" if higher_better else "↓"
            print(f"\n  Metric: {metric}  ({direction} = better)")
            print(f"  {'Algorithm':<25s} {'DRL Mean':>12s} {'Base Mean':>12s} "
                  f"{'p-value':>10s} {'d':>8s} {'Sig?':>6s} {'Winner':>10s} {'Test'}")
            print(f"  {'─' * 100}")

            for algo in ALGORITHMS:
                if algo == "drl" or algo not in data:
                    continue
                r = data[algo]
                total_tests += 1

                sig_str = "✅ YES" if r["significant"] else "❌  NO"
                if r["significant"]:
                    if r["drl_better"]:
                        winner = "DRL  ✓"
                        sig_drl_wins += 1
                    else:
                        winner = f"{algo[:8]}  ✗"
                        sig_drl_losses += 1
                else:
                    winner = "—"
                    non_sig += 1

                print(
                    f"  {algo:<25s} "
                    f"{r['drl_mean']:>12.4f} {r['baseline_mean']:>12.4f} "
                    f"{r['p_value']:>10.4f} {r['cohens_d']:>8.3f} "
                    f"{sig_str:>6s} {winner:>10s}  {r['test']}"
                )

    print(f"\n{'=' * 78}")
    print(f"  SUMMARY: {total_tests} tests | "
          f"{sig_drl_wins} DRL wins | {sig_drl_losses} DRL losses | {non_sig} non-significant")
    print("=" * 78)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Statistical analysis of DRL vs baseline comparison results."
    )
    parser.add_argument(
        "base_dir",
        nargs="?",
        default="comparison_results",
        help="Root directory containing per-scenario subdirectories",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.05,
        help="Significance level (default: 0.05)",
    )
    args = parser.parse_args()

    all_results = {}
    for scenario in SCENARIO_METRICS:
        scenario_dir = os.path.join(args.base_dir, scenario)
        if not os.path.isdir(scenario_dir):
            print(f"\n  ⚠️  Scenario directory not found: {scenario_dir}")
            continue
        print(f"\n  Analysing scenario: {scenario} ...")
        results = analyse_scenario(scenario_dir, scenario, alpha=args.alpha)
        if results:
            all_results[scenario] = results

    if all_results:
        print_report(all_results, args.alpha)

        # Save JSON
        out_path = os.path.join(args.base_dir, "statistical_analysis.json")
        # Convert numpy types for JSON serialization
        def _clean(obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            if isinstance(obj, np.bool_):
                return bool(obj)
            if isinstance(obj, dict):
                return {k: _clean(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_clean(v) for v in obj]
            return obj

        with open(out_path, "w") as f:
            json.dump(_clean(all_results), f, indent=2)
        print(f"\n  Full results saved: {out_path}")
    else:
        print("\n  No data to analyse.")


if __name__ == "__main__":
    main()
