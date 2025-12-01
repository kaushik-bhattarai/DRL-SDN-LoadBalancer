#!/usr/bin/env python3
"""
Baseline Evaluator for Load Balancing
Compares Round-Robin, Least-Connections, and Random strategies.
"""

import time
import requests
import random
import numpy as np
import threading
from traffic_generator import TrafficGenerator, ConstantTraffic, BurstyTraffic
from real_server_monitor import ServerMonitor
from mininet_topology import start_network
from setup_basic_routing import setup_complete_routing

RYU_URL = 'http://127.0.0.1:8080/sdrlb'

class BaselineEvaluator:
    def __init__(self, strategy='round_robin', duration=30):
        self.strategy = strategy
        self.duration = duration
        self.net = None
        self.traffic_gen = None
        self.server_monitor = None
        self.running = False
        self.metrics = []
        
    def setup(self):
        print(f"\n[SETUP] Starting Baseline Evaluation: {self.strategy}")
        self.net = start_network()
        time.sleep(5)
        setup_complete_routing()
        time.sleep(2)
        
        self.traffic_gen = TrafficGenerator(self.net, virtual_ip="10.0.0.100", virtual_port=8000)
        self.traffic_gen.start_http_servers()
        
        self.server_monitor = ServerMonitor(self.net, server_hosts=['h1', 'h2', 'h3'])
        self.server_monitor.start_monitoring(interval=1.0)
        
    def run(self):
        self.running = True
        
        # Start traffic
        pattern = BurstyTraffic(duration=self.duration)
        traffic_thread = threading.Thread(
            target=self.traffic_gen.start,
            args=([pattern],),
            kwargs={'use_background': False}
        )
        traffic_thread.daemon = True
        traffic_thread.start()
        
        start_time = time.time()
        step = 0
        server_hosts = ['h1', 'h2', 'h3']
        
        while time.time() - start_time < self.duration:
            # Select server based on strategy
            selected_host = None
            
            if self.strategy == 'round_robin':
                selected_host = server_hosts[step % len(server_hosts)]
                
            elif self.strategy == 'random':
                selected_host = random.choice(server_hosts)
                
            elif self.strategy == 'least_connections':
                # Get metrics
                metrics = self.server_monitor.get_metrics()
                best_host = None
                min_conns = float('inf')
                
                for h in server_hosts:
                    conns = metrics.get(h, {}).get('connections', 0)
                    if conns < min_conns:
                        min_conns = conns
                        best_host = h
                selected_host = best_host or server_hosts[0]
            
            # Apply decision
            if selected_host:
                host_obj = self.net.get(selected_host)
                if host_obj:
                    try:
                        requests.post(
                            f'{RYU_URL}/set_action', 
                            json={'server_ip': host_obj.IP()},
                            timeout=0.5
                        )
                    except:
                        pass
            
            # Log metrics
            current_metrics = self.server_monitor.get_metrics()
            loads = [m.get('load_score', 0) for m in current_metrics.values()]
            rtts = [m.get('rtt', 0) for m in current_metrics.values()]
            
            self.metrics.append({
                'time': time.time(),
                'strategy': self.strategy,
                'avg_rtt': np.mean(rtts),
                'load_variance': np.var(loads),
                'total_requests': self.traffic_gen.stats['total_requests']
            })
            
            step += 1
            time.sleep(1.0)
            
        print(f"[DONE] {self.strategy} completed.")
        
    def cleanup(self):
        if self.traffic_gen: self.traffic_gen.stop()
        if self.server_monitor: self.server_monitor.stop_monitoring()
        if self.net: self.net.stop()
        
    def save_results(self):
        import csv
        filename = f'baseline_{self.strategy}.csv'
        with open(filename, 'w') as f:
            writer = csv.DictWriter(f, fieldnames=['time', 'strategy', 'avg_rtt', 'load_variance', 'total_requests'])
            writer.writeheader()
            writer.writerows(self.metrics)
        print(f"Results saved to {filename}")

if __name__ == '__main__':
    import sys
    strategy = sys.argv[1] if len(sys.argv) > 1 else 'round_robin'
    evaluator = BaselineEvaluator(strategy)
    try:
        evaluator.setup()
        evaluator.run()
        evaluator.save_results()
    finally:
        evaluator.cleanup()
