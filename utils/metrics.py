import numpy as np
import requests
import time

RYU_URL = 'http://127.0.0.1:8080/sdrlb'

# Global server monitor instance
_server_monitor = None

def set_server_monitor(monitor):
    """
    Set the global server monitor instance
    
    Args:
        monitor: ServerMonitor instance from real_server_monitor.py
    """
    global _server_monitor
    _server_monitor = monitor

def collect_host_metrics(dpid=1, use_real_hosts=False, net=None):
    """
    Collect host metrics (CPU, mem, RTT) for training
    
    Args:
        dpid: Datapath ID
        use_real_hosts: If True, use real metrics from ServerMonitor
        net: Mininet network object (not used if ServerMonitor is set)
    
    Returns:
        List of dicts: [{'name':'h1', 'cpu':0.1, 'mem':0.2, 'rtt':0.002}, ...]
    """
    global _server_monitor
    
    # If ServerMonitor is available, use REAL metrics
    if use_real_hosts and _server_monitor is not None:
        return _collect_real_metrics_from_monitor()
    
    # Otherwise, use simulation
    return _collect_simulated_metrics()

def _collect_real_metrics_from_monitor():
    """
    Collect REAL metrics from ServerMonitor
    
    Returns:
        List of metric dicts
    """
    global _server_monitor
    
    metrics = []
    server_metrics = _server_monitor.get_metrics()
    
    # Get all 16 hosts
    for i in range(1, 17):
        host_name = f'h{i}'
        
        if host_name in server_metrics:
            # Real metrics from monitored servers (h1, h2, h3)
            m = server_metrics[host_name]
            metrics.append({
                'name': host_name,
                'cpu': float(m['cpu']),
                'mem': float(m['memory']),
                'rtt': float(m['rtt'])
            })
        else:
            # Client hosts - low baseline load
            metrics.append({
                'name': host_name,
                'cpu': 0.05,    # Minimal CPU (just idle)
                'mem': 0.15,    # Minimal memory
                'rtt': 0.001    # Local network latency
            })
    
    return metrics

def _collect_simulated_metrics():
    """
    Generate simulated metrics (for testing without real network)
    
    Returns:
        List of metric dicts
    """
    metrics = []
    
    # Use time-varying simulation for more realistic patterns
    current_time = time.time()
    
    for i in range(16):
        # Simulate load variations with sine waves
        # Different phase for each host
        base_cpu = 0.3 + 0.2 * np.sin(current_time / 10 + i * 0.5)
        cpu = np.clip(base_cpu + np.random.normal(0, 0.05), 0.05, 0.95)
        
        base_mem = 0.4 + 0.15 * np.sin(current_time / 20 + i * 0.3)
        mem = np.clip(base_mem + np.random.normal(0, 0.03), 0.1, 0.9)
        
        rtt = 0.005 + 0.005 * np.random.random()
        
        metrics.append({
            'name': f'h{i+1}',
            'cpu': float(cpu),
            'mem': float(mem),
            'rtt': float(rtt)
        })
    
    return metrics

def calculate_reward(port_stats, host_metrics, weights):
    """
    Calculate reward for load balancing
    
    If ServerMonitor is available, use real load-based reward.
    Otherwise, use standard metrics-based reward.
    
    Args:
        port_stats: Port statistics dict
        host_metrics: Host metrics list
        weights: Reward weights dict
    
    Returns:
        float: Reward value
    """
    global _server_monitor
    
    # If we have real server monitoring, use load-aware reward
    if _server_monitor is not None:
        return _calculate_real_load_reward(host_metrics, weights)
    
    # Otherwise, use standard reward
    return _calculate_standard_reward(host_metrics, weights)

def _calculate_real_load_reward(host_metrics, weights):
    """
    Calculate reward based on REAL server load
    
    This is the CORRECT way for load balancing!
    """
    global _server_monitor
    
    alpha = weights.get('alpha', 10.0)
    beta = weights.get('beta', 1.0)
    
    # Get real server metrics
    server_metrics = _server_monitor.get_metrics()
    
    if not server_metrics:
        return -1.0
    
    # Extract server loads and latencies
    server_loads = [m['load_score'] for m in server_metrics.values()]
    server_cpus = [m['cpu'] for m in server_metrics.values()]
    server_rtts = [m['rtt'] for m in server_metrics.values()]
    
    # 1. Load variance (lower = better balanced)
    load_variance = float(np.var(server_loads))
    cpu_variance = float(np.var(server_cpus))
    
    # 2. Average latency
    avg_rtt = float(np.mean(server_rtts))
    
    # 3. Overload penalty
    max_cpu = max(server_cpus) if server_cpus else 0.0
    overload_penalty = max(0, (max_cpu - 0.8) * 20.0)
    
    # 4. Underutilization penalty
    avg_cpu = np.mean(server_cpus) if server_cpus else 0.0
    underutil_penalty = max(0, (0.15 - avg_cpu) * 5.0) if avg_cpu < 0.15 else 0.0
    
    # Combined reward
    reward = -(
        alpha * avg_rtt +
        beta * (load_variance + cpu_variance) / 2.0 +
        overload_penalty +
        underutil_penalty
    )
    
    return float(reward)

def _calculate_standard_reward(host_metrics, weights):
    """
    Standard reward based on metrics (for simulation mode)
    """
    alpha = weights.get('alpha', 10.0)
    beta = weights.get('beta', 1.0)
    
    if not host_metrics:
        return 0.0
    
    latencies = [h.get('rtt', 0.0) for h in host_metrics]
    cpus = [h.get('cpu', 0.0) for h in host_metrics]
    
    avg_latency = float(np.mean(latencies)) if latencies else 0.0
    cpu_std = float(np.std(cpus)) if cpus else 0.0
    max_cpu = float(np.max(cpus)) if cpus else 0.0
    
    overload_penalty = max(0, (max_cpu - 0.8) * 10.0)
    
    reward = -(alpha * avg_latency + beta * cpu_std + overload_penalty)
    
    return float(reward)

def get_server_status_summary():
    """
    Get a summary of current server status
    
    Returns:
        dict: Server status information
    """
    global _server_monitor
    
    if _server_monitor is None:
        return {'status': 'No real monitoring', 'servers': []}
    
    server_metrics = _server_monitor.get_metrics()
    
    summary = {
        'status': 'active',
        'num_servers': len(server_metrics),
        'servers': []
    }
    
    for host_name, metrics in server_metrics.items():
        summary['servers'].append({
            'name': host_name,
            'cpu': metrics['cpu'],
            'memory': metrics['memory'],
            'rtt_ms': metrics['rtt'] * 1000,
            'connections': metrics['connections'],
            'load_score': metrics['load_score']
        })
    
    return summary