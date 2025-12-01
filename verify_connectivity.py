import sys
import time
from mininet_topology import start_network
from setup_basic_routing import setup_complete_routing

def verify_connectivity():
    print("🚀 Starting Network Verification...")
    net = start_network()
    
    try:
        print("\n⏳ Waiting for controller connection (10s)...")
        time.sleep(10)
        
        # Install routes
        print("\n🛠️ Installing Routes...")
        setup_complete_routing()
        time.sleep(2)
        
        print("\n📡 Running Ping Test...")
        h4 = net.get('h4')
        
        # 1. Physical Connectivity
        print("\n[1] Check Physical Connectivity (h4 -> h1, h2, h3)")
        servers = ['h1', 'h2', 'h3']
        for s_name in servers:
            server = net.get(s_name)
            print(f"--- Pinging {s_name} ({server.IP()}) ---")
            result = h4.cmd(f'ping -c 2 -W 1 {server.IP()}')
            if "0 received" in result:
                print(f"❌ {s_name} UNREACHABLE")
            else:
                print(f"✅ {s_name} REACHABLE")

        # 2. VIP Connectivity
        print("\n[2] Check VIP Connectivity (h4 -> 10.0.0.100)")
        print("--- Pinging VIP (10.0.0.100) ---")
        # Send 5 pings to give ARP time
        result = h4.cmd('ping -c 5 -W 1 10.0.0.100')
        print(result)
        
        if "0 received" in result:
            print("❌ VIP UNREACHABLE! (Controller not replying to ARP?)")
            print("DEBUG: ARP Cache of h4:")
            print(h4.cmd('arp -n'))
        else:
            print("✅ VIP REACHABLE! (Controller is working)")
            
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        print("\n🛑 Stopping Network...")
        net.stop()

if __name__ == "__main__":
    verify_connectivity()
