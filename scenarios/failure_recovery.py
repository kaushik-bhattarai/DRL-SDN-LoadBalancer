import numpy as np
from .base_scenario import BaseScenario, HOST_NAMES, SERVER_IPS

class FailureRecoveryScenario(BaseScenario):
    """
    Simulates h1 failing at t=30s, recovering at 90s.
    Simulates h2 failing at t=120s, recovering at 150s.
    Constant traffic at 80 rps for 180s.
    """
    
    def get_name(self):
        return "failure_recovery"

    def get_duration(self):
        return 180

    def get_pattern(self):
        from traffic_generator import ConstantTraffic
        return ConstantTraffic(80, self.get_duration() + 10)

    def on_step(self, sec, algorithm):
        # Precise event timing
        if sec == 30:
            print(f"    [EVENT] Simulating h1 failure...")
            self.kill_http_server('h1')
        elif sec == 90:
            print(f"    [EVENT] Simulating h1 recovery...")
            self.revive_http_server('h1')
        elif sec == 120:
            print(f"    [EVENT] Simulating h2 failure...")
            self.kill_http_server('h2')
        elif sec == 150:
            print(f"    [EVENT] Simulating h2 recovery...")
            self.revive_http_server('h2')

    def compute_metrics(self, raw_rows, lat_us):
        failed_request_count = 0
        h1_zero_streak = 0
        adapt_time_h1 = None
        
        fairness_during_failure = []
        
        for idx in range(1, len(raw_rows)):
            row = raw_rows[idx]
            prev = raw_rows[idx-1]
            sec = row["second"]
            
            # Count selections to dead servers since last second
            h1_sels_diff = max(0, row["h1_sels"] - prev["h1_sels"])
            h2_sels_diff = max(0, row["h2_sels"] - prev["h2_sels"])
            h3_sels_diff = max(0, row["h3_sels"] - prev["h3_sels"])
            
            if row["h1_alive"] == 0:
                failed_request_count += h1_sels_diff
                fairness_during_failure.append(row["fairness_alive"])
                # Only check adapt_time if it hasn't adapted OR if it's the first window
                # To be precise, time to adapt is when algorithm stops routing to h1
                if adapt_time_h1 is None and 30 < sec <= 90:
                    if h1_sels_diff == 0:
                        h1_zero_streak += 1
                        if h1_zero_streak == 5:
                            adapt_time_h1 = (sec - 5) - 30 
                    else:
                        h1_zero_streak = 0
            
            if row["h2_alive"] == 0:
                failed_request_count += h2_sels_diff
                fairness_during_failure.append(row["fairness_alive"])
                
        # If it never adapted (e.g. RR/WRR), set it to max window (60s)
        if adapt_time_h1 is None:
            adapt_time_h1 = 60

        return {
            "time_to_adapt": adapt_time_h1,
            "failed_request_count": failed_request_count,
            "fairness_among_alive": float(np.mean(fairness_during_failure)) if fairness_during_failure else 1.0
        }
