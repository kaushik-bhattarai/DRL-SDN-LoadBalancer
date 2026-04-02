import numpy as np
from .base_scenario import BaseScenario

class CombinedStressScenario(BaseScenario):
    """
    Primary showcase scenario.
    - Setup: Throttle h1 to half capacity.
    - Traffic: Sinusoidal (base=80, amplitude=60, period=30s) over 180s.
    - Events:
      - 60s: h2 fail
      - 90s: h2 revive
      - 120s: h1 fail
      - 150s: h1 revive
    """
    
    def get_name(self):
        return "combined_stress"

    def get_duration(self):
        return 180

    def get_pattern(self):
        from traffic_generator import SinusoidalTraffic
        return SinusoidalTraffic(
            base_rate=80,
            amplitude=60,
            period=30,
            duration=self.get_duration() + 10
        )

    def setup_trial(self, algorithm):
        print(f"    [SETUP] Applying half-capacity throttle to h1...")
        self.add_tc_throttle('h1', rate='512kbit')

    def on_step(self, sec, algorithm):
        # Precise event timing
        if sec == 60:
            print(f"    [EVENT] Simulating h2 failure...")
            self.kill_http_server('h2')
        elif sec == 90:
            print(f"    [EVENT] Simulating h2 recovery...")
            self.revive_http_server('h2')
        elif sec == 120:
            print(f"    [EVENT] Simulating h1 failure...")
            self.kill_http_server('h1')
        elif sec == 150:
            print(f"    [EVENT] Simulating h1 recovery...")
            self.revive_http_server('h1')

    def compute_metrics(self, raw_rows, lat_us):
        # We will compute fairness, latency, and failed rate across the whole run.
        # The true composite score normalization requires results from ALL algorithms,
        # which isn't available at the `compute_metrics` stage for a single trial.
        # So we just compute the raw components here and store them.
        # The plotting script `plot_dynamic.py` will normalize and plot the composite score!
        
        failed_requests = 0
        total_requests = 0

        rtts = []
        fairnesses = []

        for idx in range(1, len(raw_rows)):
            row = raw_rows[idx]
            prev = raw_rows[idx-1]
            
            # Count selections to dead servers
            h1_sels_diff = max(0, row["h1_sels"] - prev["h1_sels"])
            h2_sels_diff = max(0, row["h2_sels"] - prev["h2_sels"])
            h3_sels_diff = max(0, row["h3_sels"] - prev["h3_sels"])
            
            step_total = h1_sels_diff + h2_sels_diff + h3_sels_diff
            total_requests += step_total
            
            if row["h1_alive"] == 0:
                failed_requests += h1_sels_diff
            if row["h2_alive"] == 0:
                failed_requests += h2_sels_diff
            if row["h3_alive"] == 0:
                failed_requests += h3_sels_diff
                
            rtts.append(row["avg_rtt"])
            fairnesses.append(row["fairness_alive"])

        failed_rate = failed_requests / total_requests if total_requests > 0 else 0
        avg_rtt = float(np.mean(rtts)) if rtts else 0
        avg_fairness = float(np.mean(fairnesses)) if fairnesses else 1.0

        return {
            "stress_fairness_alive": avg_fairness,
            "stress_avg_rtt_ms": avg_rtt * 1000.0,
            "stress_failed_rate": failed_rate
        }
