import numpy as np
import re
import requests
import random

RYU_URL = 'http://127.0.0.1:8080/sdrlb'

def _safe_float(s, default=0.0):
    try:
        return float(s)
    except Exception:
        return default

def collect_host_metrics(dpid=1, use_real_hosts=False):
    """
    Collect host metrics (CPU, mem, RTT) for the given switch DPID.

    - If `use_real_hosts=True`, attempts to query the controller and simulate metrics.
    - Otherwise, returns simulated metrics (for offline testing).

    Returns a list of dicts:
        [{'name':'h1', 'cpu':0.1, 'mem':0.2, 'rtt':0.002}, ...]
    """
    metrics = []

    if use_real_hosts:
        try:
            host_ports = requests.get(f'{RYU_URL}/host_ports/{dpid}', timeout=3).json()
            host_names = list(host_ports.keys())
        except Exception:
            host_names = [f'h{i+1}' for i in range(16)]  # fallback simulated hosts

        for h in host_names:
            # Here we simulate CPU, mem, RTT because we cannot directly query Mininet hosts from trainer
            cpu = np.random.uniform(0.05, 0.5)      # simulate CPU load 5%-50%
            mem = np.random.uniform(0.1, 0.8)       # simulate mem usage 10%-80%
            rtt = np.random.uniform(0.001, 0.02)    # RTT in seconds
            metrics.append({'name': h, 'cpu': cpu, 'mem': mem, 'rtt': rtt})
    else:
        # Simulated offline host metrics
        for i in range(16):
            metrics.append({
                'name': f'h{i+1}',
                'cpu': float(np.random.uniform(0.05, 0.5)),
                'mem': float(np.random.uniform(0.1, 0.8)),
                'rtt': float(np.random.uniform(0.001, 0.02))
            })

    return metrics


def calculate_reward(port_stats, host_metrics, weights):
    """
    Compute reward: encourage low latency and balanced server CPU utilization.

    reward = - (alpha * avg_latency + beta * std(cpu_utils))

    - port_stats: dict {port: tx_bytes} (currently not used directly but passed)
    - host_metrics: list of dicts with keys 'cpu','mem','rtt' (order preserved)
    - weights: dict with 'alpha' and 'beta'
    """
    alpha = weights.get('alpha', 1.0)
    beta = weights.get('beta', 1.0)

    if not host_metrics:
        return 0.0

    latencies = [h.get('rtt', 0.0) for h in host_metrics]
    cpus = [h.get('cpu', 0.0) for h in host_metrics]

    avg_latency = float(np.mean(latencies)) if latencies else 0.0
    cpu_std = float(np.std(cpus)) if cpus else 0.0

    reward = - (alpha * avg_latency + beta * cpu_std)
    return reward
