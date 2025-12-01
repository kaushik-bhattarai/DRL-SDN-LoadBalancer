#!/usr/bin/env python3
"""
Debug script to trace exactly where cross-pod packets are failing
"""

import time
import requests

def check_flows(switch_id, desc):
    """Check flows on a switch"""
    url = f'http://127.0.0.1:8080/sdrlb/stats/flow/{switch_id}'
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            flows = response.json()
            print(f"\n{'='*70}")
            print(f"Switch {switch_id} ({desc}) - Flows:")
            print(f"{'='*70}")
            
            # Check for server flows
            for match_str, stats in flows.items():
                if '10.0.0.1' in match_str or '10.0.0.2' in match_str or '10.0.0.3' in match_str:
                    print(f"  Match: {match_str}")
                    print(f"    Packets: {stats['packet_count']}, Bytes: {stats['byte_count']}")
            
            # Check default flow
            if '{}' in flows:
                print(f"  DEFAULT FLOW (table-miss):")
                print(f"    Packets: {flows['{}']['packet_count']}, Bytes: {flows['{}']['byte_count']}")
        else:
            print(f"❌ Failed to get flows from switch {switch_id}")
    except Exception as e:
        print(f"❌ Error checking switch {switch_id}: {e}")

def main():
    print("\n" + "="*70)
    print("Cross-Pod Routing Debug Tool")
    print("="*70)
    print("\nThis will trace the path: h9 (switch 202) → h1 (switch 200)")
    print("\nMake sure:")
    print("  1. Controller is running")
    print("  2. Network is running (mininet)")
    print("  3. Routing flows are installed")
    print("  4. You've tried: h9 ping -c 1 10.0.0.1")
    print("\n" + "="*70)
    
    input("\nPress Enter to check flow statistics...")
    
    # Trace the path h12 (switch 205) → h2 (switch 200)
    # h12 on edge 205 → agg 104 → core 1 → agg 101 → edge 200 → h2
    
    print("\n🔍 Tracing path: h12 → edge 205 → agg 104 → core 1 → agg 101 → edge 200 → h2")
    
    # Check each hop
    check_flows(205, "Edge switch where h12 is")
    check_flows(104, "Aggregation switch (Pod 2)")
    check_flows(1, "Core switch 1")
    check_flows(101, "Aggregation switch (Pod 0)")
    check_flows(200, "Edge switch where h2 is")
    
    print("\n" + "="*70)
    print("Analysis:")
    print("="*70)
    print("""
Look at packet counts:
- If edge 202 has packets for 10.0.0.1 → h9 sent packet ✅
- If agg 102 has 0 packets → packet didn't reach agg ❌
- If core 1 has 0 packets → packet didn't reach core ❌
- If agg 100 has 0 packets → packet didn't route from core ❌
- If edge 200 has 0 packets → packet didn't route to server ❌

High packet count in DEFAULT FLOW means packets are hitting 
table-miss instead of matching our flows!
""")

if __name__ == '__main__':
    main()