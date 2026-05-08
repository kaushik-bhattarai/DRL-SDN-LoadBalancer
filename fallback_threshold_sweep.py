#!/usr/bin/env python3
"""
Fallback Threshold Sweep — Phase 4

Sweeps failure injection velocities (failures per observation window)
to find the exact point where DRL fail rate crosses above the best
heuristic baseline (Least Connections).  Uses the existing scenario
infrastructure to inject controlled failures.

The crossover velocity becomes CONFIG['fallback']['engage_threshold'].
Half that value becomes CONFIG['fallback']['disengage_threshold']
(hysteresis to prevent oscillation).

Usage:
    sudo python3 fallback_threshold_sweep.py --output-dir sweep_results
    sudo python3 fallback_threshold_sweep.py --trials 10 --window 60
"""

import argparse
import csv
import json
import os
import random
import sys
import threading
import time
from datetime import datetime

import numpy as np
import requests
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scenarios.base_scenario import BaseScenario, _api, jains_fairness, HOST_NAMES, SERVER_IPS
from traffic_generator import ConstantTraffic

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RYU_URL = "http://127.0.0.1:8080/sdrlb"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

FAILURE_VELOCITIES = [0, 1, 2, 3, 4, 5, 6, 8, 10, 15]
ALGORITHMS_TO_SWEEP = ["drl", "least_connections"]


# ---------------------------------------------------------------------------
# Failure-Injecting Scenario
# ---------------------------------------------------------------------------

class FailureVelocityScenario(BaseScenario):
    """
    Injects a configurable number of server failures within a fixed
    observation window, each lasting 3-8 seconds.
    """

    def __init__(self, net, traffic_gen, server_monitor, output_dir,
                 rps=80, velocity=0, window=60, seed=42):
        super().__init__(net, traffic_gen, server_monitor, output_dir, rps)
        self.velocity = velocity
        self.window = window
        self.seed = seed

        # Pre-compute failure schedule
        rng = np.random.RandomState(seed)
        if velocity > 0:
            self.failure_times = sorted(rng.uniform(5, window - 10, size=velocity))
            self.failure_durations = rng.uniform(3.0, 8.0, size=velocity)
            self.failure_servers = rng.choice(HOST_NAMES, size=velocity)
        else:
            self.failure_times = []
            self.failure_durations = []
            self.failure_servers = []

        # Track which failures are currently active
        self._active_failures = {}  # host_name -> recovery_time

    def get_name(self):
        return f"sweep_v{self.velocity}"

    def get_duration(self):
        return self.window

    def get_pattern(self):
        return ConstantTraffic(self.rps, self.get_duration() + 10)

    def on_step(self, sec, algorithm):
        now = float(sec)

        # Inject scheduled failures
        for i, t in enumerate(self.failure_times):
            host = self.failure_servers[i]
            dur = self.failure_durations[i]

            if abs(now - t) < 0.6 and host not in self._active_failures:
                self.kill_http_server(host)
                self._active_failures[host] = t + dur

        # Recover servers whose duration has elapsed
        for host, recover_at in list(self._active_failures.items()):
            if now >= recover_at:
                self.revive_http_server(host)
                del self._active_failures[host]

    def compute_metrics(self, raw_rows, lat_us):
        failed_requests = 0
        total_requests = 0

        for idx in range(1, len(raw_rows)):
            row = raw_rows[idx]
            prev = raw_rows[idx - 1]

            h1d = max(0, row["h1_sels"] - prev["h1_sels"])
            h2d = max(0, row["h2_sels"] - prev["h2_sels"])
            h3d = max(0, row["h3_sels"] - prev["h3_sels"])
            step_total = h1d + h2d + h3d
            total_requests += step_total

            if row["h1_alive"] == 0:
                failed_requests += h1d
            if row["h2_alive"] == 0:
                failed_requests += h2d
            if row["h3_alive"] == 0:
                failed_requests += h3d

        fail_rate = failed_requests / total_requests if total_requests > 0 else 0.0
        return {"fail_rate": fail_rate, "total_requests": total_requests}


# ---------------------------------------------------------------------------
# Sweep Runner
# ---------------------------------------------------------------------------

def run_sweep(args):
    from mininet.net import Mininet
    from mininet_topology import FatTree4
    from functools import partial
    from mininet.node import RemoteController, OVSSwitch
    from mininet.link import TCLink
    from evaluate_baseline import InstrumentedTrafficGenerator
    from real_server_monitor import ServerMonitor
    from comparison_runner import push_model

    print("\n[SETUP] Starting Mininet for sweep...")
    topo = FatTree4()
    c0 = RemoteController("c0", ip="127.0.0.1", port=6633)
    net = Mininet(
        topo=topo,
        controller=c0,
        switch=partial(OVSSwitch, protocols="OpenFlow13"),
        link=TCLink,
        autoSetMacs=True,
    )
    net.start()
    for sw in net.switches:
        sw.cmd(f"ovs-vsctl set bridge {sw.name} protocols=OpenFlow13")
        sw.cmd(f"ovs-vsctl set-controller {sw.name} tcp:127.0.0.1:6633")
    time.sleep(5)

    from setup_network import setup_complete_routing
    setup_complete_routing()
    time.sleep(3)

    traffic_gen = InstrumentedTrafficGenerator(net, virtual_ip="10.0.0.100", virtual_port=8000)
    traffic_gen.start_http_servers()

    server_monitor = ServerMonitor(net, server_hosts=HOST_NAMES)
    server_monitor.start_monitoring(interval=1.0)
    time.sleep(2)

    # Push DRL model
    model_path = args.model
    if not os.path.isabs(model_path):
        model_path = os.path.join(SCRIPT_DIR, model_path)
    push_model(model_path)
    _api("POST", "/set_training_mode", {"enabled": False})

    os.makedirs(args.output_dir, exist_ok=True)
    all_results = {}

    try:
        for velocity in FAILURE_VELOCITIES:
            print(f"\n{'=' * 60}")
            print(f"  VELOCITY = {velocity} failures / {args.window}s window")
            print(f"{'=' * 60}")

            all_results[velocity] = {}

            for algo in ALGORITHMS_TO_SWEEP:
                fail_rates = []

                for trial in range(1, args.trials + 1):
                    trial_seed = args.seed + velocity * 1000 + trial
                    scenario = FailureVelocityScenario(
                        net=net,
                        traffic_gen=traffic_gen,
                        server_monitor=server_monitor,
                        output_dir=args.output_dir,
                        rps=args.rps,
                        velocity=velocity,
                        window=args.window,
                        seed=trial_seed,
                    )
                    print(f"  [{algo}] trial {trial}/{args.trials} (seed={trial_seed})")
                    _, summary = scenario.run(algo, trial)
                    fail_rates.append(summary["fail_rate"])

                all_results[velocity][algo] = {
                    "mean": float(np.mean(fail_rates)),
                    "std": float(np.std(fail_rates)),
                    "values": [float(v) for v in fail_rates],
                }
                print(f"    {algo}: fail_rate = {np.mean(fail_rates):.4f} ± {np.std(fail_rates):.4f}")

        # Find crossover
        crossover = None
        for v in FAILURE_VELOCITIES:
            drl_m = all_results[v].get("drl", {}).get("mean", 0)
            lc_m = all_results[v].get("least_connections", {}).get("mean", 0)
            if drl_m > lc_m and v > 0:
                crossover = v
                break

        print(f"\n{'=' * 60}")
        print(f"  SWEEP RESULTS")
        print(f"{'=' * 60}")
        print(f"  {'Velocity':>10s} {'DRL fail':>12s} {'LC fail':>12s} {'DRL > LC?':>12s}")
        for v in FAILURE_VELOCITIES:
            drl_m = all_results[v].get("drl", {}).get("mean", 0)
            lc_m = all_results[v].get("least_connections", {}).get("mean", 0)
            flag = "  ← CROSS" if (crossover and v == crossover) else ""
            print(f"  {v:>10d} {drl_m:>12.4f} {lc_m:>12.4f} {'YES' if drl_m > lc_m else 'no':>12s}{flag}")

        if crossover is not None:
            engage = crossover
            disengage = max(1, crossover // 2)
            print(f"\n  RECOMMENDED THRESHOLDS:")
            print(f"    engage_threshold:    {engage}  (failures per {args.window}s)")
            print(f"    disengage_threshold: {disengage}  (hysteresis lower bound)")
        else:
            print(f"\n  DRL never exceeded LC — no fallback needed!")
            engage = None
            disengage = None

        # Save results
        out = {
            "timestamp": datetime.now().isoformat(),
            "velocities": FAILURE_VELOCITIES,
            "window_sec": args.window,
            "trials": args.trials,
            "results": {str(k): v for k, v in all_results.items()},
            "crossover_velocity": crossover,
            "recommended_engage": engage,
            "recommended_disengage": disengage,
        }
        out_path = os.path.join(args.output_dir, "sweep_results.json")
        with open(out_path, "w") as f:
            json.dump(out, f, indent=2)
        print(f"\n  Results saved: {out_path}")

    finally:
        server_monitor.stop_monitoring()
        traffic_gen.stop()
        for h in HOST_NAMES:
            host = net.get(h)
            if host:
                host.cmd('pkill -f "python3 -m http.server"')
        net.stop()


def main():
    parser = argparse.ArgumentParser(
        description="Sweep failure velocities to find DRL-to-LC crossover."
    )
    parser.add_argument("--model", default="models/final/dqn_final.pth")
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--window", type=int, default=60, help="Observation window (seconds)")
    parser.add_argument("--rps", type=int, default=80)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default="sweep_results")
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("Run with sudo (Mininet requires root).")
        sys.exit(1)

    run_sweep(args)


if __name__ == "__main__":
    main()
