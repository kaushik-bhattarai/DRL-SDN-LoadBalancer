# DRL-SDN Load Balancer — Complete Command Reference

> All commands assume `cwd` is the project root:
> `/home/andis/Documents/major-project/guna/DRL-SDN-LoadBalancer/`

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Controller Startup](#2-controller-startup)
3. [Training](#3-training)
4. [Inference](#4-inference)
5. [Comparative Analysis](#5-comparative-analysis)
6. [Statistical Analysis](#6-statistical-analysis)
7. [Visualization](#7-visualization)
8. [Full Pipeline (Quick Reference)](#8-full-pipeline-quick-reference)

---

## 1. Prerequisites

```bash
# Activate the Ryu virtual environment
source /home/andis/.pyenv/versions/ryu-env3/bin/activate

# Kill any stale Mininet / controller processes
sudo mn -c
sudo pkill -f ryu-manager
sudo pkill -f "python3 -m http.server"
```

---

## 2. Controller Startup

> **Terminal 1** — Must stay running throughout training/inference/comparison.

```bash
# Start Ryu SDN controller with the load-balancing application
ryu-manager --ofp-tcp-listen-port 6633 ryu_controller.py
```

The controller will:
- Listen for OpenFlow 1.3 on port 6633
- Expose REST API at `http://127.0.0.1:8080/sdrlb/`
- Auto-load trained model from `models/final/dqn_final.pth` if `inference.enabled: true` in config

---

## 3. Training

> **Terminal 2** — Requires `sudo` (Mininet needs root).
> The controller (Terminal 1) **must already be running**.

### Default Training (uses config.yaml `training.episodes`)

```bash
sudo /home/andis/.pyenv/versions/ryu-env3/bin/python train.py
```

### Training with Custom Config

```bash
sudo /home/andis/.pyenv/versions/ryu-env3/bin/python train.py --config config.yaml
```

### Training with Debug Logging

```bash
sudo /home/andis/.pyenv/versions/ryu-env3/bin/python train.py --log-level DEBUG
```

### Outputs

| Artifact | Path |
|---|---|
| Final model | `models/final/dqn_final.pth` |
| Best model | `models/checkpoints/best_model.pth` |
| Checkpoints | `models/checkpoints/dqn_ep{N}.pth` |
| Training stats | `logs/training_with_real_load.json` |
| Action log | `action_log.csv` |

---

## 4. Inference

### 4a. Push Trained Weights to Controller (Lightweight)

```bash
python3 inference.py
```

With a specific model:
```bash
python3 inference.py --model models/final/dqn_final.pth
```

### 4b. Full Inference Evaluation (Traffic + Metrics + Plots)

> Requires `sudo` — creates Mininet, generates traffic, collects metrics.

```bash
sudo /home/andis/.pyenv/versions/ryu-env3/bin/python run_inference_eval.py --duration 120
```

Longer evaluation:
```bash
sudo /home/andis/.pyenv/versions/ryu-env3/bin/python run_inference_eval.py --duration 180
```

With specific model and no plot:
```bash
sudo /home/andis/.pyenv/versions/ryu-env3/bin/python run_inference_eval.py \
  --model models/checkpoints/best_model.pth \
  --duration 120 \
  --no-plot
```

### Outputs

| Artifact | Path |
|---|---|
| Eval JSON | `logs/inference_eval_<timestamp>.json` |
| Eval Plot | `plots/inference_eval_<timestamp>.png` |

---

## 5. Comparative Analysis

> Requires `sudo` — runs all 7 algorithms across selected scenarios.
> Controller (Terminal 1) must be running.

### 5a. Static Scenario Only

```bash
sudo /home/andis/.pyenv/versions/ryu-env3/bin/python comparison_runner.py \
  --scenario static \
  --duration 60 \
  --trials 10 \
  --rps 80 \
  --output-dir comparison_results_v4
```

### 5b. Single Dynamic Scenario

```bash
# Failure Recovery
sudo /home/andis/.pyenv/versions/ryu-env3/bin/python comparison_runner.py \
  --scenario failure \
  --duration 60 \
  --trials 10 \
  --rps 80 \
  --output-dir comparison_results_v4

# Heterogeneous Capacity
sudo /home/andis/.pyenv/versions/ryu-env3/bin/python comparison_runner.py \
  --scenario heterogeneous \
  --duration 60 \
  --trials 10 \
  --rps 80 \
  --output-dir comparison_results_v4

# Bursty Saturation
sudo /home/andis/.pyenv/versions/ryu-env3/bin/python comparison_runner.py \
  --scenario bursty \
  --duration 60 \
  --trials 10 \
  --rps 80 \
  --output-dir comparison_results_v4

# Combined Stress
sudo /home/andis/.pyenv/versions/ryu-env3/bin/python comparison_runner.py \
  --scenario combined \
  --duration 60 \
  --trials 10 \
  --rps 80 \
  --output-dir comparison_results_v4
```

### 5c. All Scenarios at Once

```bash
sudo /home/andis/.pyenv/versions/ryu-env3/bin/python comparison_runner.py \
  --scenario all \
  --duration 60 \
  --trials 10 \
  --rps 80 \
  --output-dir comparison_results_v4
```

### 5d. With Bursty Traffic Pattern

```bash
sudo /home/andis/.pyenv/versions/ryu-env3/bin/python comparison_runner.py \
  --scenario all \
  --traffic-pattern bursty \
  --duration 60 \
  --trials 10 \
  --rps 100 \
  --output-dir comparison_results_v4
```

### Outputs

| Artifact | Path |
|---|---|
| Raw per-second CSV | `<output-dir>/static/results_raw.csv` |
| Summary CSV | `<output-dir>/static/results_summary.csv` |
| Text Report | `<output-dir>/static/comparison_report.txt` |
| Dynamic Raw CSV | `<output-dir>/<scenario>/results_raw.csv` |
| Dynamic Summary | `<output-dir>/<scenario>/results_scenario_summary.csv` |

---

## 6. Statistical Analysis

> Runs hypothesis tests (Welch's t-test / Mann-Whitney U) comparing DRL vs baselines.

```bash
python3 statistical_analysis.py comparison_results_v4 --alpha 0.05
```

With stricter significance:
```bash
python3 statistical_analysis.py comparison_results_v4 --alpha 0.01
```

### Outputs

| Artifact | Path |
|---|---|
| JSON results | `<results-dir>/statistical_analysis.json` |
| Terminal report | (printed to stdout) |

---

## 7. Visualization

### 7a. Training Visualization (Rewards, Connections, CPU)

```bash
python3 visualize_results.py
```

Output: `plots/training_summary.png` and `plots/training_summary.pdf`

### 7b. Inference Visualization (6-panel dashboard)

```bash
# Use the latest inference log
python3 visualize_inference.py logs/inference_eval_<timestamp>.json \
  --save plots/inference_dashboard.png
```

### 7c. Static Comparison Plots (6 figures)

```bash
python3 plot_comparison.py --input-dir comparison_results_v4/static
```

Outputs in `comparison_results_v4/static/`:
- `fairness_vs_time.png/.pdf`
- `latency_vs_load.png/.pdf`
- `throughput_bar.png/.pdf`
- `connection_distribution.png/.pdf`
- `decision_overhead.png/.pdf`
- `summary_heatmap.png/.pdf`

### 7d. Dynamic Scenario Plots (5 figures)

```bash
python3 plot_dynamic.py --base-dir comparison_results_v4
```

Outputs in `comparison_results_v4/`:
- `failure_recovery_plot.png`
- `heterogeneous_plot.png`
- `bursty_plot.png`
- `combined_stress_plot.png`
- `updated_summary_heatmap.png`

### 7e. Statistical Effect Size Plot

```bash
python3 plot_statistics.py --json comparison_results_v4/statistical_analysis.json
```

Output: `comparison_results_v4/statistical_effect_sizes.png`

---

## 8. Full Pipeline (Quick Reference)

> Step-by-step sequence for a complete training-to-evaluation cycle.

```bash
# ──────────────────────────────────────────────────
# TERMINAL 1: Start Controller (keep running)
# ──────────────────────────────────────────────────
ryu-manager --ofp-tcp-listen-port 6633 ryu_controller.py


# ──────────────────────────────────────────────────
# TERMINAL 2: Run Pipeline
# ──────────────────────────────────────────────────

# Step 0: Clean up any prior Mininet state
sudo mn -c

# Step 1: Train (1000 episodes, ~12h)
sudo /home/andis/.pyenv/versions/ryu-env3/bin/python train.py

# Step 2: Visualize training results
python3 visualize_results.py

# Step 3: Inference evaluation (180s)
sudo /home/andis/.pyenv/versions/ryu-env3/bin/python run_inference_eval.py --duration 180

# Step 4: Visualize inference
python3 visualize_inference.py logs/inference_eval_*.json --save plots/inference_dashboard.png

# Step 5: Comparative analysis (all scenarios, 10 trials)
sudo /home/andis/.pyenv/versions/ryu-env3/bin/python comparison_runner.py \
  --scenario all --duration 60 --trials 10 --rps 80 \
  --output-dir comparison_results_v4

# Step 6: Static comparison plots
python3 plot_comparison.py --input-dir comparison_results_v4/static

# Step 7: Dynamic scenario plots
python3 plot_dynamic.py --base-dir comparison_results_v4

# Step 8: Statistical analysis
python3 statistical_analysis.py comparison_results_v4 --alpha 0.05

# Step 9: Statistical effect-size plot
python3 plot_statistics.py --json comparison_results_v4/statistical_analysis.json
```

---

## Environment Notes

| Item | Value |
|---|---|
| Python env | `/home/andis/.pyenv/versions/ryu-env3/bin/python` |
| Ryu controller port | `6633` |
| REST API | `http://127.0.0.1:8080/sdrlb/` |
| Virtual IP | `10.0.0.100` |
| Servers | `h1=10.0.0.1`, `h2=10.0.0.2`, `h3=10.0.0.3` |
| Clients | `h4–h8` |
| Fat-tree k | 4 |
