import numpy as np
from .base_scenario import BaseScenario, HOST_NAMES

class BurstySaturationScenario(BaseScenario):
    """
    Simulates bursty traffic: 15s at 30rps, 15s at 350rps.
    Repeats 4 times (120s total).
    """

    def get_name(self):
        return "bursty_saturation"

    def get_duration(self):
        return 120

    def get_pattern(self):
        from traffic_generator import BurstyTraffic
        return BurstyTraffic(
            base_rate=30, 
            burst_rate=350, 
            burst_duration=15, 
            burst_interval=30, 
            duration=self.get_duration() + 10
        )

    def compute_metrics(self, raw_rows, lat_us):
        burst_rtts = []
        queue_saturation_events = 0
        
        # We need to identify burst periods. 
        # By the pattern: [0, 15) is burst, [15, 30) is base... wait, 
        # `burst_duration=15, burst_interval=30` means:
        # 0-14s: burst
        # 15-29s: base
        # 30-44s: burst
        # It's burst when (sec % 30) < 15.
        
        recovery_start = None
        recovery_times = []
        
        for r in raw_rows:
            sec = r["second"]
            is_burst = (sec % 30) < 15
            
            # 1. P95 RTT during burst windows
            if is_burst:
                burst_rtts.append(r["avg_rtt"])
                # Reset recovery tracking if we re-enter burst
                recovery_start = None 
            else:
                # 3. Recovery time tracking
                # We want to measure how fast fairness returns to >= 0.90 after burst ends
                # Once it returns, we record the time taken.
                if recovery_start is None:
                    # Burst just ended
                    recovery_start = sec
                
                if recovery_start is not None and r["fairness_index"] >= 0.90:
                    time_to_recover = sec - (sec - (sec % 30) + 15) # Wait, better: sec - start_of_this_base_period
                    # Start of this base period = sec - (sec % 30) + 15
                    # e.g. sec=18. start = 18 - 18 + 15 = 15. time = 18 - 15 = 3
                    base_start = sec - (sec % 30)
                    time_to_recover = sec - base_start
                    recovery_times.append(time_to_recover)
                    recovery_start = None # Don't record multiple times per window
            
            # 2. Queue saturation events
            conns = [r["h1_conns"], r["h2_conns"], r["h3_conns"]]
            mean_conns = sum(conns) / 3.0
            if mean_conns > 0:
                if any(c > 2 * mean_conns for c in conns):
                    queue_saturation_events += 1

        p95_rtt_burst = float(np.percentile(burst_rtts, 95)) if burst_rtts else 0
        
        # Average recovery time cross the 4 windows
        avg_recovery_time = float(np.mean(recovery_times)) if recovery_times else 15.0 # Max is 15s window

        return {
            "p95_rtt_burst": p95_rtt_burst,
            "queue_saturation_events": queue_saturation_events,
            "recovery_time": avg_recovery_time
        }
