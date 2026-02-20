#!/usr/bin/env python3
"""
Traffic Generator for DRL-SDN Load Balancer Training

This generates realistic traffic patterns to train the DRL agent:
- Multiple traffic patterns (constant, bursty, incremental)
- HTTP requests to virtual IP (load balancer)
- Background traffic between hosts
- Configurable traffic intensity
"""

import time
import threading
import random
import subprocess
from mininet.net import Mininet
from mininet.node import Host
import numpy as np
import yaml
import os
import signal
import sys

class TrafficPattern:
    """Base class for traffic patterns"""
    
    def __init__(self, name, duration=60):
        self.name = name
        self.duration = duration
        self.start_time = None
    
    def get_rate(self, elapsed_time):
        """Return requests per second at given elapsed time"""
        raise NotImplementedError
    
    def is_finished(self, elapsed_time):
        """Check if pattern is finished"""
        return elapsed_time >= self.duration


class ConstantTraffic(TrafficPattern):
    """Constant traffic rate"""
    
    def __init__(self, rate=100, duration=60):
        super().__init__("Constant", duration)
        self.rate = rate
    
    def get_rate(self, elapsed_time):
        return self.rate


class BurstyTraffic(TrafficPattern):
    """Bursty traffic with periodic spikes"""
    
    def __init__(self, base_rate=50, burst_rate=500, burst_duration=5, burst_interval=15, duration=60):
        super().__init__("Bursty", duration)
        self.base_rate = base_rate
        self.burst_rate = burst_rate
        self.burst_duration = burst_duration
        self.burst_interval = burst_interval
    
    def get_rate(self, elapsed_time):
        """Returns burst_rate during bursts, base_rate otherwise"""
        cycle_time = elapsed_time % self.burst_interval
        if cycle_time < self.burst_duration:
            return self.burst_rate
        return self.base_rate


class IncrementalTraffic(TrafficPattern):
    """Gradually increasing traffic"""
    
    def __init__(self, start_rate=50, end_rate=500, duration=60):
        super().__init__("Incremental", duration)
        self.start_rate = start_rate
        self.end_rate = end_rate
    
    def get_rate(self, elapsed_time):
        """Linear interpolation from start_rate to end_rate"""
        if self.duration == 0:
            return self.end_rate
        progress = elapsed_time / self.duration
        return self.start_rate + (self.end_rate - self.start_rate) * progress


class SinusoidalTraffic(TrafficPattern):
    """Sinusoidal traffic pattern (simulates day/night cycles)"""
    
    def __init__(self, base_rate=100, amplitude=200, period=60, duration=120):
        super().__init__("Sinusoidal", duration)
        self.base_rate = base_rate
        self.amplitude = amplitude
        self.period = period
    
    def get_rate(self, elapsed_time):
        """Sinusoidal wave pattern"""
        return self.base_rate + self.amplitude * np.sin(2 * np.pi * elapsed_time / self.period)


class TrafficGenerator:
    """Main traffic generator for Mininet network"""
    
    def __init__(self, net, virtual_ip="10.0.0.100", virtual_port=8000, server_hosts=['h1', 'h2', 'h3']):
        self.net = net
        self.virtual_ip = virtual_ip
        self.virtual_port = virtual_port
        self.server_hosts = server_hosts  # Servers that will handle requests
        self.running = False
        self.threads = []
        self.stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'total_bytes_sent': 0
        }
        
        # Get ALL hosts
        all_hosts = [h for h in net.hosts]
        
        # CLIENT HOSTS = All hosts EXCEPT servers
        # This is CRITICAL: servers don't send traffic, only receive it!
        self.clients = [h for h in all_hosts if h.name not in server_hosts]
        
        print(f"[TrafficGen] Initialized")
        print(f"  Servers: {server_hosts} (will handle requests)")
        print(f"  Clients: {[h.name for h in self.clients]} (will generate traffic)")
    
    def start_http_servers(self, server_hosts=None):
        """Start simple HTTP servers on specified hosts"""
        if server_hosts is None:
            server_hosts = self.server_hosts  # Use initialized servers (h1, h2, h3)
        
        print(f"\n[TrafficGen] Starting HTTP servers on {len(server_hosts)} hosts...")
        
        for host_name in server_hosts:
            host = self.net.get(host_name)
            if host is None:
                print(f"   {host_name}: Host not found!")
                continue
            
            # Start a simple Python HTTP server on port 8000
            host.cmd(f'mkdir -p /tmp/{host_name}')
            host.cmd(f'echo "Server: {host_name} | IP: {host.IP()}" > /tmp/{host_name}/index.html')
            host.cmd(f'cd /tmp/{host_name} && python3 -m http.server 8000 > /tmp/{host_name}.log 2>&1 &')
            print(f"  ✅ {host_name} ({host.IP()}): HTTP server started")
        
        time.sleep(2)  # Wait for servers to start
        print("[TrafficGen] HTTP servers ready!\n")
        
        # Verify connectivity
        print("[TrafficGen] Verifying connectivity...")
        client = self.clients[0]
        for host_name in server_hosts:
            host = self.net.get(host_name)
            # Ping with short timeout
            result = client.cmd(f'ping -c 1 -W 1 {host.IP()}')
            if "1 received" in result:
                print(f"   Ping {client.name} -> {host_name} ({host.IP()}): OK")
            else:
                print(f"   Ping {client.name} -> {host_name} ({host.IP()}): FAILED")
                print(f"     Output: {result}")
        print("\n")
    
    def stop_http_servers(self):
        """Stop all HTTP servers"""
        print("\n[TrafficGen] Stopping HTTP servers...")
        for host in self.clients:
            host.cmd('pkill -f "python3 -m http.server"')
        print("[TrafficGen] HTTP servers stopped\n")
    
    def send_request(self, client, target_ip, port):
        """Send a single HTTP request from client to target"""
        try:
            # Use wget with timeout
            result = client.cmd(f'wget -q -O - --timeout=2 http://{target_ip}:{port}/ 2>&1')
            
            if "200 OK" in result or len(result) > 0:
                self.stats['successful_requests'] += 1
                self.stats['total_bytes_sent'] += len(result)
                return True
            else:
                self.stats['failed_requests'] += 1
                return False
        except Exception as e:
            self.stats['failed_requests'] += 1
            return False
    
    def send_batch(self, client, target_ip, port, count, concurrency=10):
        """Send a batch of requests using Apache Bench (ab)"""
        try:
            # Use ab for high load (Timeout 5s)
            cmd = f"ab -n {count} -c {concurrency} -s 5 http://{target_ip}:{port}/ 2>&1"
            result = client.cmd(cmd)
            
            if "Failed requests:        0" in result:
                self.stats["successful_requests"] += count
                self.stats["total_bytes_sent"] += count * 100 # Approx
                return True, count
            else:
                # Parse failed requests if possible, or assume all failed if ab failed
                # ab output format: "Failed requests:        X"
                import re
                match = re.search(r"Failed requests:\s+(\d+)", result)
                if match:
                    failed = int(match.group(1))
                    success = count - failed
                    self.stats["successful_requests"] += success
                    self.stats["failed_requests"] += failed
                    return True, success
                else:
                    self.stats["failed_requests"] += count
                    print(f"[DEBUG] ab failed: {result}")
                    
                    # Fallback debug: Try wget to see if it's reachable at all
                    print(f"[DEBUG] Probing with wget to check connectivity...")
                    probe = client.cmd(f'wget -q -O - --timeout=2 http://{target_ip}:{port}/ 2>&1')
                    if "200 OK" in probe or len(probe) > 0:
                        print(f"[DEBUG] Wget SUCCEEDED! It's an ab specific issue.")
                    else:
                        print(f"[DEBUG] Wget FAILED too. Network/Server is down.")
                        
                    return False, 0
        except Exception as e:
            self.stats["failed_requests"] += count
            return False, 0
    def generate_pattern_traffic(self, pattern, clients=None):
        """
        Generate traffic according to specified pattern
        
        Args:
            pattern: TrafficPattern object
            clients: List of client hosts (if None, use all)
        """
        if clients is None:
            clients = self.clients
        
        print(f"\n[TrafficGen] Starting pattern: {pattern.name}")
        print(f"  Duration: {pattern.duration}s")
        
        start_time = time.time()
        request_count = 0
        
        while self.running:
            elapsed = time.time() - start_time
            
            if pattern.is_finished(elapsed):
                print(f"[TrafficGen] Pattern '{pattern.name}' completed")
                break
            
            # Get current rate (requests per second)
            current_rate = pattern.get_rate(elapsed)
            
            # Calculate delay between requests
            if current_rate > 0:
                delay = 1.0 / current_rate
            else:
                delay = 1.0
            
            # Select random client
            client = random.choice(clients)
            
            # Send request to virtual IP (load balancer)
            self.send_request(client, self.virtual_ip, self.virtual_port)
            request_count += 1
            self.stats['total_requests'] += 1
            
            # Print progress every 100 requests
            if request_count % 100 == 0:
                success_rate = (self.stats['successful_requests'] / self.stats['total_requests']) * 100
                print(f"  [{elapsed:.1f}s] Rate: {current_rate:.1f} req/s | "
                      f"Total: {self.stats['total_requests']} | "
                      f"Success: {success_rate:.1f}%")
            
            time.sleep(delay)
    
    def generate_background_traffic(self):
        """Generate random background traffic between hosts"""
        print("[TrafficGen] Starting background traffic...")
        
        while self.running:
            # Random ping between two hosts
            h1 = random.choice(self.clients)
            h2 = random.choice(self.clients)
            
            if h1 != h2:
                h1.cmd(f'ping -c 1 -W 1 {h2.IP()} > /dev/null 2>&1 &')
            
            time.sleep(random.uniform(1, 5))
    
    def start(self, patterns, use_background=True):
        """
        Start traffic generation
        
        Args:
            patterns: List of TrafficPattern objects to execute sequentially
            use_background: Whether to generate background traffic
        """
        self.running = True
        
        # Start HTTP servers on first 3 hosts
        self.start_http_servers()
        
        # Start background traffic thread
        if use_background:
            bg_thread = threading.Thread(target=self.generate_background_traffic)
            bg_thread.daemon = True
            bg_thread.start()
            self.threads.append(bg_thread)
        
        # Execute each traffic pattern sequentially
        for pattern in patterns:
            if not self.running:
                break
            self.generate_pattern_traffic(pattern)
        
        print("\n[TrafficGen] All patterns completed")
        self.print_stats()
    
    def stop(self):
        """Stop traffic generation"""
        print("\n[TrafficGen] Stopping traffic generation...")
        self.running = False
        
        # Wait for threads to finish
        for thread in self.threads:
            thread.join(timeout=2)
        
        # Stop HTTP servers
        self.stop_http_servers()
        
        print("[TrafficGen] Traffic generation stopped")
    
    def print_stats(self):
        """Print traffic statistics"""
        print("\n" + "="*60)
        print("Traffic Generation Statistics")
        print("="*60)
        print(f"Total Requests:      {self.stats['total_requests']}")
        print(f"Successful:          {self.stats['successful_requests']}")
        print(f"Failed:              {self.stats['failed_requests']}")
        if self.stats['total_requests'] > 0:
            success_rate = (self.stats['successful_requests'] / self.stats['total_requests']) * 100
            print(f"Success Rate:        {success_rate:.2f}%")
        print(f"Total Bytes Sent:    {self.stats['total_bytes_sent']}")
        print("="*60 + "\n")


def load_traffic_config(config_file='config.yaml'):
    """Load traffic patterns from config file"""
    
    if not os.path.exists(config_file):
        print(f"[WARN] Config file not found: {config_file}, using defaults")
        return [
            ConstantTraffic(rate=100, duration=30),
            BurstyTraffic(base_rate=50, burst_rate=500, duration=30),
            IncrementalTraffic(start_rate=50, end_rate=300, duration=30)
        ]
    
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)
    
    traffic_config = config.get('traffic', {})
    
    if not traffic_config.get('enabled', True):
        print("[INFO] Traffic generation disabled in config")
        return []
    
    patterns = []
    for pattern_config in traffic_config.get('patterns', []):
        pattern_type = pattern_config.get('type', 'constant')
        
        if pattern_type == 'constant':
            patterns.append(ConstantTraffic(
                rate=pattern_config.get('rate', 100),
                duration=pattern_config.get('duration', 30)
            ))
        elif pattern_type == 'bursty':
            patterns.append(BurstyTraffic(
                base_rate=pattern_config.get('base_rate', 50),
                burst_rate=pattern_config.get('burst_rate', 500),
                burst_duration=pattern_config.get('burst_duration', 5),
                burst_interval=pattern_config.get('burst_interval', 15),
                duration=pattern_config.get('duration', 60)
            ))
        elif pattern_type == 'incremental':
            patterns.append(IncrementalTraffic(
                start_rate=pattern_config.get('start_rate', 50),
                end_rate=pattern_config.get('end_rate', 500),
                duration=pattern_config.get('duration', 60)
            ))
        elif pattern_type == 'sinusoidal':
            patterns.append(SinusoidalTraffic(
                base_rate=pattern_config.get('base_rate', 100),
                amplitude=pattern_config.get('amplitude', 200),
                period=pattern_config.get('period', 60),
                duration=pattern_config.get('duration', 120)
            ))
    
    return patterns


def main():
    """Main function to run standalone traffic generation"""
    print("\n" + "="*60)
    print("DRL-SDN Load Balancer - Traffic Generator")
    print("="*60 + "\n")
    
    # Import mininet topology
    from mininet_topology import start_network
    
    # Start network
    print("[INFO] Starting Mininet network...")
    net = start_network()
    print("[INFO] Network started successfully\n")
    
    # Load traffic patterns from config
    patterns = load_traffic_config()
    
    if not patterns:
        print("[INFO] No traffic patterns configured, using default")
        patterns = [
            ConstantTraffic(rate=100, duration=20),
            BurstyTraffic(duration=20),
            IncrementalTraffic(duration=20)
        ]
    
    # Create traffic generator
    # IMPORTANT: Specify which hosts are servers
    traffic_gen = TrafficGenerator(
        net, 
        virtual_ip="10.0.0.100",
        server_hosts=['h1', 'h2', 'h3']  # These are servers
    )
    
    print(f"\n[INFO] Setup:")
    print(f"  - Servers (backends): h1, h2, h3")
    print(f"  - Clients (traffic generators): {[h.name for h in traffic_gen.clients]}")
    print(f"  - Virtual IP: 10.0.0.100")
    print(f"  - Total clients: {len(traffic_gen.clients)}\n")
    
    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        print('\n[INFO] Interrupt received, stopping...')
        traffic_gen.stop()
        net.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # Start traffic generation
    try:
        traffic_gen.start(patterns, use_background=True)
    except KeyboardInterrupt:
        pass
    finally:
        traffic_gen.stop()
        net.stop()
    
    print("\n[INFO] Traffic generation completed. Network stopped.")


if __name__ == '__main__':
    main()