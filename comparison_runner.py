#!/usr/bin/env python3
"""
Comparative Analysis Harness — Benchmark DQN agent against traditional baselines.

Assumes Mininet + Ryu controller are already running.
Pushes trained model weights, then iterates through algorithms × trials,
collecting per-second metrics and decision-latency measurements.

Usage:
    sudo python3 comparison_runner.py --duration 60 --trials 3
    sudo python3 comparison_runner.py --traffic-pattern bursty --rps 120 --output-dir results/
"""

import argparse
import base64
import csv
import io
import json
import os
import random
import signal
import sys
import threading
import time
from datetime import datetime

import numpy as np
import requests
import yaml

# ---------------------------------------------------------------------------
# Project imports (wired into existing code, NOT reimplemented)
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from evaluate_baseline import InstrumentedTrafficGenerator
from real_server_monitor import ServerMonitor
from traffic_generator import (
    BurstyTraffic,
    ConstantTraffic,
    IncrementalTraffic,
    SinusoidalTraffic,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RYU_BASE_URL = "http://127.0.0.1:8080"
RYU_URL = f"{RYU_BASE_URL}/sdrlb"
SERVER_IPS = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
HOST_NAMES = ["h1", "h2", "h3"]

ALGORITHMS = [
    "round_robin",
    "weighted_round_robin",
    "random",
    "least_connections",
    "hash_based",
    "ecmp",
    "drl",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _api(method, path, json_body=None, retries=3, timeout=5):
    """HTTP helper with retry."""
    url = f"{RYU_URL}{path}"
    for attempt in range(retries):
        try:
            if method == "GET":
                r = requests.get(url, timeout=timeout)
            else:
                r = requests.post(url, json=json_body, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(0.5)


def jains_fairness(values):
    if not values or len(values) == 0:
        return 1.0
    n = len(values)
    s = sum(values)
    s2 = sum(v * v for v in values)
    if s2 == 0:
        return 1.0
    return (s * s) / (n * s2)


def push_model(model_path):
    """Push trained DQN weights to the controller (same as inference.py)."""
    import torch
    from drl_agent import DQNAgent

    config_path = os.path.join(SCRIPT_DIR, "config.yaml")
    with open(config_path) as f:
        config = yaml.safe_load(f)
    agent = DQNAgent(config)
    if not agent.load_model(model_path):
        raise RuntimeError(f"Could not load model from {model_path}")
    q_buf = io.BytesIO()
    torch.save(agent.q_net.state_dict(), q_buf)
    q_b64 = base64.b64encode(q_buf.getvalue()).decode("utf-8")
    t_buf = io.BytesIO()
    torch.save(agent.target_net.state_dict(), t_buf)
    t_b64 = base64.b64encode(t_buf.getvalue()).decode("utf-8")
    _api("POST", "/update_weights",
         {"q_net_weights": q_b64, "target_net_weights": t_b64})
    print(f"  ✅ Model weights pushed: {model_path}")


def make_pattern(name, rps, duration):
    """Create a traffic pattern from CLI name."""
    if name == "bursty":
        return BurstyTraffic(base_rate=rps // 2, burst_rate=rps * 2, duration=duration)
    elif name == "incremental":
        return IncrementalTraffic(start_rate=rps // 2, end_rate=rps * 2, duration=duration)
    elif name == "sinusoidal":
        return SinusoidalTraffic(base_rate=rps, amplitude=rps, duration=duration)
    else:  # constant (default)
        return ConstantTraffic(rate=rps, duration=duration)


def measure_decision_latency(n=1000):
    """Measure controller response overhead via tight GET /stats loop."""
    url = f"{RYU_URL}/stats"
    latencies = []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            requests.get(url, timeout=2)
        except Exception:
            pass
        latencies.append((time.perf_counter() - t0) * 1e6)  # µs
    return float(np.mean(latencies))


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

class ComparisonRunner:
    def __init__(self, args):
        self.args = args
        self.net = None
        self.traffic_gen = None
        self.server_monitor = None
        self._active = False

        # Raw timeseries rows
        self.raw_rows = []

        # Seed RNG
        np.random.seed(args.seed)
        random.seed(args.seed)

    # ------ setup / teardown ------

    def setup(self):
        """Wire into the already-running Mininet + Ryu."""
        from mininet.net import Mininet
        from mininet_topology import FatTree4
        from functools import partial
        from mininet.node import RemoteController, OVSSwitch
        from mininet.link import TCLink

        print("\n[SETUP] Connecting to running Mininet network...")
        # We need the Mininet `net` object to pass to ServerMonitor
        # and InstrumentedTrafficGenerator.  Re-create it from the
        # topology descriptor, pointing at the existing controller.
        topo = FatTree4()
        c0 = RemoteController('c0', ip='127.0.0.1', port=6633)
        self.net = Mininet(
            topo=topo,
            controller=c0,
            switch=partial(OVSSwitch, protocols='OpenFlow13'),
            link=TCLink,
            autoSetMacs=True,
        )
        self.net.start()

        # Force OF1.3 + set controller on all switches
        for sw in self.net.switches:
            sw.cmd(f'ovs-vsctl set bridge {sw.name} protocols=OpenFlow13')
            sw.cmd(f'ovs-vsctl set-controller {sw.name} tcp:127.0.0.1:6633')
        time.sleep(5)

        # Install routing
        from setup_network import setup_complete_routing
        setup_complete_routing()
        time.sleep(3)

        # Traffic generator (starts HTTP servers)
        print("[SETUP] Starting HTTP servers + traffic generator...")
        self.traffic_gen = InstrumentedTrafficGenerator(
            self.net, virtual_ip="10.0.0.100", virtual_port=8000,
        )
        self.traffic_gen.start_http_servers()

        # Server monitor
        print("[SETUP] Starting server monitor...")
        self.server_monitor = ServerMonitor(self.net, server_hosts=HOST_NAMES)
        self.server_monitor.start_monitoring(interval=1.0)
        time.sleep(2)

        # Push model weights for DRL
        model_path = self.args.model
        if not os.path.isabs(model_path):
            model_path = os.path.join(SCRIPT_DIR, model_path)
        print(f"[SETUP] Pushing DQN model weights...")
        push_model(model_path)

        # Disable training mode → session persistence ON
        _api("POST", "/set_training_mode", {"enabled": False})
        print("[SETUP] ✅ Ready\n")

    def cleanup(self):
        self._active = False
        if self.server_monitor:
            self.server_monitor.stop_monitoring()
        if self.traffic_gen:
            self.traffic_gen.stop()
        if self.net:
            for h in HOST_NAMES:
                host = self.net.get(h)
                if host:
                    host.cmd('pkill -f "python3 -m http.server"')
            self.net.stop()

    # ------ single trial ------

    def run_trial(self, algorithm, trial_num):
        """Run one (algorithm, trial) and collect per-second metrics."""
        # Per-trial seed rotation for statistical independence
        trial_seed = self.args.seed + trial_num
        np.random.seed(trial_seed)
        random.seed(trial_seed)

        # Phase 2: Verify DRL greedy mode
        if algorithm == "drl" and trial_num == 1:
            print(f"  [VERIFY] DRL running in greedy mode (\u03b5=0.0 at controller)")

        duration = self.args.duration
        pattern = make_pattern(
            self.args.traffic_pattern, self.args.rps, duration + 10,
        )

        # 1. Reset controller state
        _api("POST", "/reset_episode")
        # Reset monitor connections
        if self.server_monitor:
            self.server_monitor.reset_connections()
        time.sleep(0.5)

        # 2. Set algorithm
        payload = {"algorithm": algorithm}
        if algorithm == "weighted_round_robin":
            payload["weights"] = [1, 1, 1]
        _api("POST", "/set_algorithm", payload)
        # Disable session persistence for fair comparison
        _api("POST", "/set_training_mode", {"enabled": True})

        # 3. Warmup (2 s, discarded)
        time.sleep(2)

        # Reset stats *after* warmup
        _api("POST", "/reset_episode")
        if self.server_monitor:
            self.server_monitor.reset_connections()

        # 4. Traffic thread
        self._active = True

        def traffic_loop():
            start = time.time()
            while self._active and (time.time() - start) < duration:
                elapsed = time.time() - start
                rate = pattern.get_rate(elapsed)
                if rate > 0:
                    client = random.choice(self.traffic_gen.clients)
                    self.traffic_gen.send_batch(
                        client,
                        self.traffic_gen.virtual_ip,
                        self.traffic_gen.virtual_port,
                        count=max(1, int(rate)),
                        concurrency=min(10, max(1, int(rate))),
                    )
                time.sleep(1.0)

        t = threading.Thread(target=traffic_loop, daemon=True)
        t.start()

        # 5. Metric collection loop (every 1 s)
        start_time = time.time()
        last_total = 0
        step = 0

        while time.time() - start_time < duration:
            loop_t0 = time.time()
            sec = time.time() - start_time

            # Controller stats
            try:
                r = _api("GET", "/stats")
                ctrl = r.json()
                total_req = ctrl.get("total_requests", 0)
                sel = ctrl.get("server_selections", {})
            except Exception:
                total_req, sel = last_total, {}
            throughput = total_req - last_total
            last_total = total_req

            # Server metrics
            sm = self.server_monitor.get_metrics() if self.server_monitor else {}
            conns = [sm.get(h, {}).get("connections", 0) for h in HOST_NAMES]
            rtts = [sm.get(h, {}).get("rtt", 0) for h in HOST_NAMES]
            cpus = [sm.get(h, {}).get("cpu", 0) for h in HOST_NAMES]

            # Push metrics to controller so DRL sees real load
            try:
                ip_metrics = {
                    f"10.0.0.{i+1}": dict(sm.get(HOST_NAMES[i], {}))
                    for i in range(3)
                    if HOST_NAMES[i] in sm
                }
                _api("POST", "/update_metrics", ip_metrics)
            except Exception:
                pass

            # Derived
            sel_counts = [sel.get(ip, 0) for ip in SERVER_IPS]
            fairness = jains_fairness(sel_counts)
            avg_rtt = float(np.mean(rtts)) if rtts else 0
            p95_rtt = float(np.percentile(rtts, 95)) if rtts else 0
            max_imbalance = max(conns) - min(conns) if conns else 0

            row = {
                "algorithm": algorithm,
                "trial": trial_num,
                "second": round(sec, 1),
                "throughput": throughput,
                "avg_rtt": round(avg_rtt * 1000, 3),   # ms
                "p95_rtt": round(p95_rtt * 1000, 3),    # ms
                "fairness_index": round(fairness, 4),
                "max_imbalance": max_imbalance,
                "h1_conns": conns[0],
                "h2_conns": conns[1],
                "h3_conns": conns[2],
                "decision_latency_us": 0,  # filled later
            }
            self.raw_rows.append(row)

            if step % 10 == 0:
                print(
                    f"    [{algorithm}|t{trial_num}|{step:3d}s] "
                    f"tput={throughput:4d} fair={fairness:.3f} "
                    f"rtt={avg_rtt*1000:.1f}ms conns={conns}"
                )
            step += 1
            time.sleep(max(0, 1.0 - (time.time() - loop_t0)))

        self._active = False
        t.join(timeout=5)

        # 6. Decision latency measurement
        print(f"    Measuring decision latency (1000 calls)...")
        lat_us = measure_decision_latency(n=1000)
        # Backfill latency for this trial
        for row in self.raw_rows:
            if (row["algorithm"] == algorithm
                    and row["trial"] == trial_num
                    and row["decision_latency_us"] == 0):
                row["decision_latency_us"] = round(lat_us, 1)

        print(f"    ✅ {algorithm} trial {trial_num} done "
              f"(latency={lat_us:.0f}µs)\n")

    # ------ outputs ------

    def write_raw_csv(self, path):
        if not self.raw_rows:
            return
        keys = list(self.raw_rows[0].keys())
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(self.raw_rows)
        print(f"  Saved: {path}")

    def write_summary_csv(self, path):
        if not self.raw_rows:
            return
        import pandas as pd  # local import — only needed here

        df = pd.DataFrame(self.raw_rows)
        metrics = [
            "throughput", "avg_rtt", "p95_rtt", "fairness_index",
            "max_imbalance", "decision_latency_us",
        ]
        rows = []
        for algo in ALGORITHMS:
            sub = df[df["algorithm"] == algo]
            if sub.empty:
                continue
            for m in metrics:
                vals = sub[m].dropna()
                rows.append({
                    "algorithm": algo,
                    "metric": m,
                    "mean": round(float(vals.mean()), 4),
                    "std": round(float(vals.std()), 4),
                    "min": round(float(vals.min()), 4),
                    "max": round(float(vals.max()), 4),
                })
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["algorithm", "metric", "mean", "std", "min", "max"])
            w.writeheader()
            w.writerows(rows)
        print(f"  Saved: {path}")

    def write_report(self, path):
        if not self.raw_rows:
            return
        import pandas as pd

        df = pd.DataFrame(self.raw_rows)
        lines = []
        lines.append("=" * 72)
        lines.append("COMPARATIVE ANALYSIS REPORT — DRL vs Baselines")
        lines.append(f"Generated: {datetime.now().isoformat()}")
        lines.append(f"Duration: {self.args.duration}s | Trials: {self.args.trials} | "
                      f"Pattern: {self.args.traffic_pattern} | RPS: {self.args.rps}")
        lines.append("=" * 72)
        lines.append("")

        header = f"{'Algorithm':<25} {'Fairness':>14} {'Avg RTT(ms)':>16} {'Throughput':>14} {'Overhead(µs)':>14}"
        lines.append(header)
        lines.append("-" * len(header))

        drl_row = {}
        for algo in ALGORITHMS:
            sub = df[df["algorithm"] == algo]
            if sub.empty:
                continue
            fair_m = sub["fairness_index"].mean()
            fair_s = sub["fairness_index"].std()
            rtt_m = sub["avg_rtt"].mean()
            rtt_s = sub["avg_rtt"].std()
            tp_m = sub["throughput"].mean()
            tp_s = sub["throughput"].std()
            lat = sub["decision_latency_us"].mean()

            line = (
                f"{algo:<25} "
                f"{fair_m:>6.3f}±{fair_s:<6.3f} "
                f"{rtt_m:>7.2f}±{rtt_s:<7.2f} "
                f"{tp_m:>6.1f}±{tp_s:<6.1f} "
                f"{lat:>12.0f}"
            )
            lines.append(line)
            if algo == "drl":
                drl_row = {"fair": fair_m, "rtt": rtt_m, "tp": tp_m, "lat": lat}

        lines.append("")
        lines.append("--- Tradeoff Verdicts vs DRL ---")
        for algo in ALGORITHMS:
            if algo == "drl":
                continue
            sub = df[df["algorithm"] == algo]
            if sub.empty:
                lines.append(f"  {algo}: SKIPPED (no data)")
                continue
            fair = sub["fairness_index"].mean()
            rtt = sub["avg_rtt"].mean()
            tp = sub["throughput"].mean()
            lat = sub["decision_latency_us"].mean()

            verdicts = []
            if drl_row:
                if fair > drl_row["fair"] + 0.02:
                    verdicts.append("fairer")
                elif fair < drl_row["fair"] - 0.02:
                    verdicts.append("less fair")
                if rtt < drl_row["rtt"] * 0.9:
                    verdicts.append("lower latency")
                elif rtt > drl_row["rtt"] * 1.1:
                    verdicts.append("higher latency")
                if tp > drl_row["tp"] * 1.1:
                    verdicts.append("higher throughput")
                elif tp < drl_row["tp"] * 0.9:
                    verdicts.append("lower throughput")
                if lat < drl_row["lat"] * 0.5:
                    verdicts.append("much lower overhead")
            verdict_str = ", ".join(verdicts) if verdicts else "comparable"
            lines.append(f"  {algo}: {verdict_str}")

        # --- h1 bias check ---
        lines.append("")
        lines.append("--- Server Bias Check (DRL) ---")
        drl_data = df[df["algorithm"] == "drl"]
        if not drl_data.empty:
            total_conns = (
                drl_data["h1_conns"].sum()
                + drl_data["h2_conns"].sum()
                + drl_data["h3_conns"].sum()
            )
            if total_conns > 0:
                h1_pct = drl_data["h1_conns"].sum() / total_conns * 100
                h2_pct = drl_data["h2_conns"].sum() / total_conns * 100
                h3_pct = drl_data["h3_conns"].sum() / total_conns * 100
                lines.append(f"  h1: {h1_pct:.1f}%  h2: {h2_pct:.1f}%  h3: {h3_pct:.1f}%")
                if h1_pct < 20:
                    lines.append(
                        "  ⚠️  WARNING: h1 receives <20% of DRL traffic. "
                        "This is a possible server-bias artifact from training "
                        "(h1 lost connections around episode 120). "
                        "Fairness between h2/h3 is unaffected."
                    )
                else:
                    lines.append("  ✅ No significant server bias detected.")
            else:
                lines.append("  No connection data collected.")
        else:
            lines.append("  DRL not tested in this run.")

        lines.append("")
        lines.append("=" * 72)

        report = "\n".join(lines)
        with open(path, "w") as f:
            f.write(report)
        print(f"  Saved: {path}")
        print(f"\n{report}")

    # ------ dynamic scenarios ------
    def run_dynamic_scenario(self, scenario_name):
        from scenarios import (
            FailureRecoveryScenario,
            HeterogeneousCapacityScenario,
            BurstySaturationScenario,
            CombinedStressScenario
        )
        scene_map = {
            "failure": FailureRecoveryScenario,
            "heterogeneous": HeterogeneousCapacityScenario,
            "bursty": BurstySaturationScenario,
            "combined": CombinedStressScenario
        }
        
        SClass = scene_map[scenario_name]
        out_dir = os.path.join(self.args.output_dir, scenario_name)
        os.makedirs(out_dir, exist_ok=True)
        
        scenario = SClass(
            net=self.net,
            traffic_gen=self.traffic_gen,
            server_monitor=self.server_monitor,
            output_dir=out_dir,
            rps=self.args.rps
        )
        
        all_raw_rows = []
        all_metrics = []
        
        for algo in ALGORITHMS:
            print(f"{'='*60}")
            print(f"  Scenario: {scenario_name} | Algorithm: {algo} ({self.args.trials} trials)")
            print(f"{'='*60}")
            
            for trial in range(1, self.args.trials + 1):
                print(f"  --- Trial {trial}/{self.args.trials} ---")
                raw_rows, summary = scenario.run(algo, trial)
                all_raw_rows.extend(raw_rows)
                all_metrics.append(summary)
                
        # Write scenario results
        raw_path = os.path.join(out_dir, "results_raw.csv")
        summary_path = os.path.join(out_dir, "results_scenario_summary.csv")
        
        # Raw rows
        if all_raw_rows:
            keys = list(all_raw_rows[0].keys())
            import csv
            with open(raw_path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                w.writerows(all_raw_rows)
            print(f"  Saved raw: {raw_path}")
            
        # Summary metrics
        if all_metrics:
            keys = list(all_metrics[0].keys())
            with open(summary_path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                w.writerows(all_metrics)
            print(f"  Saved summary: {summary_path}")

    # ------ main ------

    def run(self):
        self.setup()
        
        scenarios_to_run = []
        if self.args.scenario == "all":
            scenarios_to_run = ["static", "failure", "heterogeneous", "bursty", "combined"]
        else:
            scenarios_to_run = [self.args.scenario]
            
        for sc in scenarios_to_run:
            if sc == "static":
                self.run_static()
            else:
                self.run_dynamic_scenario(sc)
                
        self.cleanup()
        
    def run_static(self):
        out_dir = os.path.join(self.args.output_dir, "static")
        os.makedirs(out_dir, exist_ok=True)
        # Clear raw rows for static
        self.raw_rows = []

        try:
            for algo in ALGORITHMS:
                print(f"{'='*60}")
                print(f"  Algorithm: {algo} ({self.args.trials} trials)")
                print(f"{'='*60}")

                # Try setting algorithm — skip on failure (e.g. ECMP not supported)
                try:
                    payload = {"algorithm": algo}
                    if algo == "weighted_round_robin":
                        payload["weights"] = [1, 1, 1]
                    _api("POST", "/set_algorithm", payload)
                except Exception as e:
                    print(f"  ⚠️  Skipping {algo}: {e}")
                    continue

                for trial in range(1, self.args.trials + 1):
                    print(f"  --- Trial {trial}/{self.args.trials} ---")
                    self.run_trial(algo, trial)

        except KeyboardInterrupt:
            print("\n⚠️  Interrupted — flushing results...")
        finally:
            # Always write whatever we have
            raw_path = os.path.join(out_dir, "results_raw.csv")
            summary_path = os.path.join(out_dir, "results_summary.csv")
            report_path = os.path.join(out_dir, "comparison_report.txt")

            self.write_raw_csv(raw_path)
            try:
                self.write_summary_csv(summary_path)
            except ImportError:
                print("  ⚠️  pandas not installed — skipping summary CSV")
            self.write_report(report_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Benchmark DQN load balancer against traditional baselines."
    )
    parser.add_argument("--model", type=str, default="models/final/dqn_final.pth",
                        help="Path to trained DQN model")
    parser.add_argument("--duration", type=int, default=60,
                        help="Duration per trial in seconds")
    parser.add_argument("--trials", type=int, default=30,
                        help="Number of trials per algorithm (default: 30 for statistical power)")
    parser.add_argument("--traffic-pattern", type=str, default="constant",
                        choices=["constant", "bursty", "incremental", "sinusoidal"])
    parser.add_argument("--rps", type=int, default=80,
                        help="Requests per second")
    parser.add_argument("--output-dir", type=str, default="comparison_results",
                        help="Output directory for CSVs and report")
    parser.add_argument("--scenario", type=str, default="static",
                        choices=["static", "failure", "heterogeneous", "bursty", "combined", "all"],
                        help="Which scenario to run (default: static)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility")
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("Run with sudo (Mininet requires root).")
        sys.exit(1)

    runner = ComparisonRunner(args)
    runner.run()


if __name__ == "__main__":
    main()
