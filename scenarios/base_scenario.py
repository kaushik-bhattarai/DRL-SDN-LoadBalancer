import csv
import json
import os
import random
import threading
import time
import requests
import numpy as np

RYU_BASE_URL = "http://127.0.0.1:8080"
RYU_URL = f"{RYU_BASE_URL}/sdrlb"
SERVER_IPS = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
HOST_NAMES = ["h1", "h2", "h3"]

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
    s = sum(values)
    s2 = sum(v * v for v in values)
    if s2 == 0:
        return 1.0
    return (s * s) / (len(values) * s2)

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

class BaseScenario:
    def __init__(self, net, traffic_gen, server_monitor, output_dir, rps=80):
        self.net = net
        self.traffic_gen = traffic_gen
        self.server_monitor = server_monitor
        self.output_dir = output_dir
        self.rps = rps
        self._active = False
        
        # Scenarios track their own alive state, push it to Ryu if they kill/revive
        self.server_alive = [1, 1, 1]
    
    # ---------------- Hooks for subclasses ----------------
    
    def get_duration(self):
        return 60
        
    def get_pattern(self):
        from traffic_generator import ConstantTraffic
        return ConstantTraffic(self.rps, self.get_duration() + 10)
        
    def get_name(self):
        return "base"
        
    def setup_trial(self, algorithm):
        pass
        
    def teardown_trial(self, algorithm):
        # Default teardown removes any tc added by mistake
        self.remove_tc_throttle('h1')
        self.remove_tc_throttle('h2')
        self.remove_tc_throttle('h3')
        # And revives any killed HTTP servers
        self.revive_http_server('h1')
        self.revive_http_server('h2')
        self.revive_http_server('h3')
        self.server_alive = [1, 1, 1]
        
    def on_step(self, sec, algorithm):
        pass
        
    def compute_metrics(self, raw_rows, lat_us):
        """Should return a dict of scenario-specific summary metrics."""
        return {}

    # ---------------- Utilities ----------------

    def kill_http_server(self, host_name):
        if not self.net: return
        h = self.net.get(host_name)
        h.cmd('pkill -f SimpleHTTPServer; pkill -f http.server')
        print(f"      [!] Killed HTTP server on {host_name}")
        idx = HOST_NAMES.index(host_name)
        self.server_alive[idx] = 0
        try:
            _api("POST", "/update_metrics", {"server_alive": self.server_alive})
        except:
            pass

    def revive_http_server(self, host_name):
        if not self.net: return
        h = self.net.get(host_name)
        h.cmd('pkill -f SimpleHTTPServer; pkill -f http.server')
        h.cmd(f'cd /tmp/{host_name} && python3 -m http.server 8000 > /tmp/{host_name}.log 2>&1 &')
        print(f"      [+] Revived HTTP server on {host_name}")
        idx = HOST_NAMES.index(host_name)
        self.server_alive[idx] = 1
        try:
            _api("POST", "/update_metrics", {"server_alive": self.server_alive})
        except:
            pass

    def add_tc_throttle(self, host_name, rate='512kbit', burst='32kbit', latency='400ms'):
        if not self.net: return
        h = self.net.get(host_name)
        cmd_show = f"tc qdisc show dev {host_name}-eth0"
        if "tbf" in h.cmd(cmd_show):
            h.cmd(f"tc qdisc del dev {host_name}-eth0 root")
        h.cmd(f"tc qdisc add dev {host_name}-eth0 root tbf rate {rate} burst {burst} latency {latency}")
        print(f"      [!] Throttled {host_name} (rate={rate})")

    def remove_tc_throttle(self, host_name):
        if not self.net: return
        h = self.net.get(host_name)
        cmd_show = f"tc qdisc show dev {host_name}-eth0"
        if "tbf" in h.cmd(cmd_show):
            h.cmd(f"tc qdisc del dev {host_name}-eth0 root")
            print(f"      [+] Removed throttle on {host_name}")

    # ---------------- Trial Loop ----------------

    def run(self, algorithm, trial_num):
        duration = self.get_duration()
        pattern = self.get_pattern()
        
        self.setup_trial(algorithm)

        _api("POST", "/reset_episode")
        if self.server_monitor:
            self.server_monitor.reset_connections()
        time.sleep(0.5)

        payload = {"algorithm": algorithm}
        # Give WRR correctly proportional weights depending on the scenario config.
        # But for WRR, sub-classes can modify Ryu state or we can just pass [1,1,1] by default 
        # unless it's the heterogeneous scenario. We'll handle that inside `setup_trial` or override here.
        # Check if algorithm is WRR and the scenario set custom weights
        if algorithm == "weighted_round_robin":
            if hasattr(self, 'wrr_weights'):
                payload["weights"] = self.wrr_weights
            else:
                payload["weights"] = [1, 1, 1]
        
        _api("POST", "/set_algorithm", payload)
        _api("POST", "/set_training_mode", {"enabled": True})

        time.sleep(2)
        _api("POST", "/reset_episode")
        if self.server_monitor:
            self.server_monitor.reset_connections()

        self._active = True
        
        # Track raw rows for this run
        raw_rows = []

        def traffic_loop():
            start = time.time()
            # use duration + 1 to make sure traffic runs closely to the end
            while self._active and (time.time() - start) < (duration + 1):
                elapsed = time.time() - start
                rate = pattern.get_rate(elapsed)
                if rate > 0 and self.traffic_gen:
                    client = random.choice(self.traffic_gen.clients)
                    self.traffic_gen.send_batch(
                        client,
                        self.traffic_gen.virtual_ip,
                        self.traffic_gen.virtual_port,
                        count=max(1, int(rate)),
                        concurrency=min(10, max(1, int(rate))),
                    )
                time.sleep(1.0)

        if self.traffic_gen:
            t = threading.Thread(target=traffic_loop, daemon=True)
            t.start()

        start_time = time.time()
        last_total = 0
        step = 0

        # Also push server_alive repeatedly in case Ryu controller misses it via update_metrics
        # or we just let it be. We push alive along with real load metrics.
        
        while time.time() - start_time < duration:
            loop_t0 = time.time()
            sec = time.time() - start_time
            
            # Subclass hook (can inject failure, etc)
            self.on_step(int(sec), algorithm)

            try:
                r = _api("GET", "/stats")
                ctrl = r.json()
                total_req = ctrl.get("total_requests", 0)
                sel = ctrl.get("server_selections", {})
            except Exception:
                total_req, sel = last_total, {}
                
            throughput = total_req - last_total
            last_total = total_req

            sm = self.server_monitor.get_metrics() if self.server_monitor else {}
            conns = [sm.get(h, {}).get("connections", 0) for h in HOST_NAMES]
            rtts = [sm.get(h, {}).get("rtt", 0) for h in HOST_NAMES]
            
            # Compute FAIRNESS using Jain's Index but only among ALIVE servers.
            sel_counts = [sel.get(ip, 0) for ip in SERVER_IPS]
            
            alive_sels = [sel_counts[i] for i in range(3) if self.server_alive[i] == 1]
            if not alive_sels or sum(alive_sels) == 0:
                fairness_alive = 1.0  # Or 0 if absolutely no traffic, but 1.0 matches jains default
            else:
                fairness_alive = jains_fairness(alive_sels)
            
            # Simple fairness across all 3 for compatibility
            fairness_all = jains_fairness(sel_counts)
            
            avg_rtt = float(np.mean(rtts)) if rtts else 0
            p95_rtt = float(np.percentile(rtts, 95)) if rtts else 0
            max_imbalance = max(conns) - min(conns) if conns else 0

            # Push real metrics back to Ryu
            try:
                ip_metrics = {
                    f"10.0.0.{i+1}": dict(sm.get(HOST_NAMES[i], {}))
                    for i in range(3)
                    if HOST_NAMES[i] in sm
                }
                # Always augment with our track of server_alive
                ip_metrics["server_alive"] = self.server_alive
                _api("POST", "/update_metrics", ip_metrics)
            except Exception:
                pass

            row = {
                "algorithm": algorithm,
                "trial": trial_num,
                "second": int(sec),
                "throughput": throughput,
                "avg_rtt": round(avg_rtt * 1000, 3),   # ms
                "p95_rtt": round(p95_rtt * 1000, 3),   # ms
                "fairness_index": round(fairness_all, 4),
                "fairness_alive": round(fairness_alive, 4),
                "max_imbalance": max_imbalance,
                "h1_conns": conns[0],
                "h2_conns": conns[1],
                "h3_conns": conns[2],
                "h1_sels": sel_counts[0],
                "h2_sels": sel_counts[1],
                "h3_sels": sel_counts[2],
                "h1_rtt": rtts[0] if len(rtts) > 0 else 0,
                "h2_rtt": rtts[1] if len(rtts) > 1 else 0,
                "h3_rtt": rtts[2] if len(rtts) > 2 else 0,
                "h1_alive": self.server_alive[0],
                "h2_alive": self.server_alive[1],
                "h3_alive": self.server_alive[2],
                "decision_latency_us": 0,  
            }
            raw_rows.append(row)

            if step % 10 == 0:
                print(
                    f"    [{algorithm}|t{trial_num}|{step:3d}s] "
                    f"tput={throughput:4d} fair={fairness_alive:.3f} "
                    f"rtt={avg_rtt*1000:.1f}ms conns={conns} alv={self.server_alive}"
                )
            step += 1
            time.sleep(max(0, 1.0 - (time.time() - loop_t0)))

        self._active = False
        if self.traffic_gen:
            t.join(timeout=5)

        print(f"    Measuring decision latency (1000 calls)...")
        lat_us = measure_decision_latency(n=1000)
        
        for r in raw_rows:
            r["decision_latency_us"] = round(lat_us, 1)

        summary_metrics = self.compute_metrics(raw_rows, lat_us)
        summary_metrics["algorithm"] = algorithm
        summary_metrics["trial"] = trial_num
        summary_metrics["avg_decision_latency_us"] = round(lat_us, 1)
        
        self.teardown_trial(algorithm)
        print(f"    ✅ {algorithm} trial {trial_num} done\n")
        
        return raw_rows, summary_metrics
