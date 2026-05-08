import numpy as np
from .base_scenario import BaseScenario

class HeterogeneousCapacityScenario(BaseScenario):
    """
    Simulates h1 running at half capacity (512kbit vs full).
    WRR is given optimal weights (1:2:2).
    Constant traffic at 100 rps for 120s.
    """
    
    def get_name(self):
        return "heterogeneous_capacity"

    def get_duration(self):
        return 120

    def get_pattern(self):
        from traffic_generator import ConstantTraffic
        return ConstantTraffic(100, self.get_duration() + 10)

    def setup_trial(self, algorithm):
        print(f"    [SETUP] Applying half-capacity throttle to h1...")
        self.add_tc_throttle('h1', rate='512kbit')
        if algorithm == "weighted_round_robin":
            self.wrr_weights = [1, 2, 2]

    def compute_metrics(self, raw_rows, lat_us):
        total_h1_sels = 0
        total_sels = 0
        
        weighted_rtt_sum = 0
        
        for idx in range(1, len(raw_rows)):
            row = raw_rows[idx]
            prev = raw_rows[idx-1]
            
            h1_sels_diff = max(0, row["h1_sels"] - prev["h1_sels"])
            h2_sels_diff = max(0, row["h2_sels"] - prev["h2_sels"])
            h3_sels_diff = max(0, row["h3_sels"] - prev["h3_sels"])
            
            step_total = h1_sels_diff + h2_sels_diff + h3_sels_diff
            
            total_h1_sels += h1_sels_diff
            total_sels += step_total
            
            if step_total > 0:
                step_weighted_rtt = (
                    h1_sels_diff * row["h1_rtt"] +
                    h2_sels_diff * row["h2_rtt"] +
                    h3_sels_diff * row["h3_rtt"]
                ) / step_total
                weighted_rtt_sum += step_weighted_rtt

        h1_traffic_share = total_h1_sels / total_sels if total_sels > 0 else 0
        avg_rtt_weighted = weighted_rtt_sum / len(raw_rows[1:]) if len(raw_rows) > 1 else 0
        
        # Throughput loss: expected 100 rps * 120s = 12000 total approx. 
        # actual throughput is the total_req in the last row.
        # But wait, the traffic gen sends exactly what we tell it. 
        # The true "ideal throughput" is whatever constant says to send... wait.
        # Throughput is bounded by capacity and ab timeouts. 
        ideal_throughput = max(1, self.rps * self.get_duration())
        actual_throughput = raw_rows[-1]["throughput"] if len(raw_rows) > 0 else 0
        # `throughput` column in raw_rows is per-second throughput! No, wait:
        # `throughput = total_req - last_total`.
        # So sum of all raw_rows 'throughput' column is the total.
        sum_actual_throughput = sum(r["throughput"] for r in raw_rows)
        
        throughput_loss = max(0, (ideal_throughput - sum_actual_throughput) / ideal_throughput)

        return {
            "h1_traffic_share": h1_traffic_share,
            "avg_rtt_weighted": avg_rtt_weighted * 1000.0, # Convert to ms
            "throughput_loss": throughput_loss
        }
