#!/usr/bin/env python3
"""
Complete BIDIRECTIONAL routing setup for DRL Load Balancer with CORE LAYER
Installs flows on ALL switches including CORE so clients can reach servers across pods
"""

import requests
import time
import sys

RYU_URL = 'http://127.0.0.1:8080/sdrlb/stats/flowentry/add'

# Server locations
SERVERS = {
    '10.0.0.1': {'switch': 200, 'port': 3},  # h1
    '10.0.0.2': {'switch': 200, 'port': 4},  # h2
    '10.0.0.3': {'switch': 201, 'port': 3},  # h3
}

# Edge switches (200-207) - each has 2 uplinks to aggregation (ports 1, 2)
EDGE_SWITCHES = [200, 201, 202, 203, 204, 205, 206, 207]

# Aggregation switches (100-107)
AGG_SWITCHES = [100, 101, 102, 103, 104, 105, 106, 107]

# Core switches (1-4)
CORE_SWITCHES = [1, 2, 3, 4]

# Topology mapping: which agg switches connect to which edge switches
# Topology mapping: which agg switches connect to which edge switches
# From verify_topology_ports.py:
# Port 3 -> Edge*_0 (e.g. 200, 202...)
# Port 4 -> Edge*_1 (e.g. 201, 203...)
AGG_TO_EDGE = {
    100: {200: 3, 201: 4},
    101: {200: 3, 201: 4},
    102: {202: 3, 203: 4},
    103: {202: 3, 203: 4},
    104: {204: 3, 205: 4},
    105: {204: 3, 205: 4},
    106: {206: 3, 207: 4},
    107: {206: 3, 207: 4},
}

# Agg to Core uplink mapping (port 1 on each agg goes to core)
AGG_TO_CORE_PORT = 1

# Core to Agg mapping (Standard Fat-Tree k=4)
# Core 1, 2 (indices 0, 1) -> Agg 0 in all pods (Ports 1, 2, 3, 4)
# Core 3, 4 (indices 2, 3) -> Agg 1 in all pods (Ports 1, 2, 3, 4)
CORE_TO_AGG = {
    1: {100: 1, 102: 2, 104: 3, 106: 4},  # Core 1 -> Agg0s
    2: {100: 1, 102: 2, 104: 3, 106: 4},  # Core 2 -> Agg0s
    3: {101: 1, 103: 2, 105: 3, 107: 4},  # Core 3 -> Agg1s
    4: {101: 1, 103: 2, 105: 3, 107: 4},  # Core 4 -> Agg1s
}

# Host to port mapping on each edge switch (for return traffic)
# Based on topology code: edge_base=200, switches = s_edge{pod}_{e}, dpid=200+pod*2+e
# From links: s_edge0_0=h1,h2  s_edge0_1=h3,h4  s_edge1_0=h5,h6  s_edge1_1=h7,h8
#             s_edge2_0=h9,h10  s_edge2_1=h11,h12  s_edge3_0=h13,h14  s_edge3_1=h15,h16
HOST_PORTS = {
    200: [(3, '10.0.0.1'), (4, '10.0.0.2')],    # s_edge0_0 (pod 0, e=0): h1, h2
    201: [(3, '10.0.0.3'), (4, '10.0.0.4')],    # s_edge0_1 (pod 0, e=1): h3, h4
    202: [(3, '10.0.0.5'), (4, '10.0.0.6')],    # s_edge1_0 (pod 1, e=0): h5, h6
    203: [(3, '10.0.0.7'), (4, '10.0.0.8')],    # s_edge1_1 (pod 1, e=1): h7, h8
    204: [(3, '10.0.0.9'), (4, '10.0.0.10')],   # s_edge2_0 (pod 2, e=0): h9, h10
    205: [(3, '10.0.0.11'), (4, '10.0.0.12')],  # s_edge2_1 (pod 2, e=1): h11, h12
    206: [(3, '10.0.0.13'), (4, '10.0.0.14')],  # s_edge3_0 (pod 3, e=0): h13, h14
    207: [(3, '10.0.0.15'), (4, '10.0.0.16')],  # s_edge3_1 (pod 3, e=1): h15, h16
}

def install_flow(dpid, match, actions, priority=100):
    """Install a flow via REST API"""
    payload = {
        "dpid": dpid,
        "match": match,
        "actions": actions,
        "priority": priority,
        "idle_timeout": 0,
        "hard_timeout": 0
    }
    
    try:
        response = requests.post(RYU_URL, json=payload, timeout=5)
        if response.status_code == 200:
            return True
        else:
            print(f"    ⚠️  Failed on switch {dpid}: {response.text[:50]}")
            return False
    except Exception as e:
        print(f"    ❌ Error on switch {dpid}: {str(e)[:50]}")
        return False

def setup_complete_routing():
    """Install complete BIDIRECTIONAL routing including CORE layer"""
    
    print("\n" + "="*70)
    print("Installing COMPLETE Fat-Tree Routing (Edge+Agg+Core)")
    print("="*70 + "\n")
    
    total_flows = 0
    
    # Server IPs that should be handled by DRL agent via DNAT
    # DO NOT install static flows for these!
    DRL_SERVER_IPS = ['10.0.0.1', '10.0.0.2', '10.0.0.3']
    
    for server_ip, server_info in SERVERS.items():
        server_switch = server_info['switch']
        server_port = server_info['port']
        
        print(f"📍 Setting up routes to {server_ip} (Server on switch {server_switch})")
        
        # ========================================
        # STEP 1: Server's own switch - Direct delivery (IP + ARP)
        # ========================================
        
        # FORWARD: To server
        match = {"eth_type": 2048, "ipv4_dst": server_ip}
        actions = [{"type": "OUTPUT", "port": server_port}]
        
        if install_flow(server_switch, match, actions, priority=2000):
            print(f"  ✅ Switch {server_switch}: Direct to server IP (port {server_port})")
            total_flows += 1
        
        # ARP to server
        match_arp = {"eth_type": 2054, "arp_tpa": server_ip}
        actions = [{"type": "OUTPUT", "port": server_port}]
        
        if install_flow(server_switch, match_arp, actions, priority=2000):
            print(f"  ✅ Switch {server_switch}: Direct to server ARP (port {server_port})")
            total_flows += 1
        
        # REVERSE: From server back to clients on same switch
        hosts = HOST_PORTS.get(server_switch, [])
        for host_port, host_ip in hosts:
            if host_ip == server_ip:
                continue  # Skip server itself
            
            # Return IP traffic
            match = {"eth_type": 2048, "ipv4_src": server_ip, "ipv4_dst": host_ip}
            actions = [{"type": "OUTPUT", "port": host_port}]
            if install_flow(server_switch, match, actions, priority=1900):
                total_flows += 1
            
            # Return ARP
            match_arp = {"eth_type": 2054, "arp_tpa": host_ip}
            actions = [{"type": "OUTPUT", "port": host_port}]
            if install_flow(server_switch, match_arp, actions, priority=1900):
                total_flows += 1
        
        # REVERSE: From server to other switches
        for edge_switch in EDGE_SWITCHES:
            if edge_switch == server_switch:
                continue  # Skip same switch (already handled above)
            
            for host_port, host_ip in HOST_PORTS.get(edge_switch, []):
                # Return IP traffic - send to uplink
                match = {"eth_type": 2048, "ipv4_dst": host_ip}
                actions = [{"type": "OUTPUT", "port": 1}]  # Uplink to agg
                if install_flow(server_switch, match, actions, priority=1800):
                    total_flows += 1
                
                # Return ARP - send to uplink
                match_arp = {"eth_type": 2054, "arp_tpa": host_ip}
                actions = [{"type": "OUTPUT", "port": 1}]  # Uplink to agg
                if install_flow(server_switch, match_arp, actions, priority=1800):
                    total_flows += 1
        
        print(f"  ✅ Switch {server_switch}: Return paths to all hosts configured")
        
        # Return traffic to uplink (for clients on other switches) - GENERIC FALLBACK
        match = {"eth_type": 2048, "in_port": server_port}
        actions = [{"type": "OUTPUT", "port": 1}]  # Send to uplink
        if install_flow(server_switch, match, actions, priority=1500):
            print(f"  ✅ Switch {server_switch}: Return traffic from server to uplink")
            total_flows += 1
        
        # ========================================
        # STEP 2: ALL other edge switches - Send UP to aggregation (IP + ARP)
        # ========================================
        for edge_switch in EDGE_SWITCHES:
            if edge_switch == server_switch:
                continue  # Skip server's own switch
            
            # FORWARD: To server
            match = {"eth_type": 2048, "ipv4_dst": server_ip}
            actions = [{"type": "OUTPUT", "port": 1}]
            
            if install_flow(edge_switch, match, actions, priority=500):
                print(f"  ✅ Edge {edge_switch}: Send IP to agg (port 1)")
                total_flows += 1
            
            match_arp = {"eth_type": 2054, "arp_tpa": server_ip}
            actions = [{"type": "OUTPUT", "port": 1}]
            
            if install_flow(edge_switch, match_arp, actions, priority=500):
                print(f"  ✅ Edge {edge_switch}: Send ARP to agg (port 1)")
                total_flows += 1
            
            # REVERSE: From uplink to hosts on this switch
            for host_port, host_ip in HOST_PORTS.get(edge_switch, []):
                # Return IP traffic
                match = {"eth_type": 2048, "ipv4_dst": host_ip}
                actions = [{"type": "OUTPUT", "port": host_port}]
                if install_flow(edge_switch, match, actions, priority=500):
                    total_flows += 1
                
                # Return ARP
                match_arp = {"eth_type": 2054, "arp_tpa": host_ip}
                actions = [{"type": "OUTPUT", "port": host_port}]
                if install_flow(edge_switch, match_arp, actions, priority=500):
                    total_flows += 1
        
        # ========================================
        # STEP 3: Aggregation switches (IP + ARP)
        # ========================================
        for agg_switch, edge_ports in AGG_TO_EDGE.items():
            if server_switch in edge_ports:
                # This agg connects directly to server's edge - route DOWN
                out_port = edge_ports[server_switch]
                
                # FORWARD: To server
                match = {"eth_type": 2048, "ipv4_dst": server_ip}
                actions = [{"type": "OUTPUT", "port": out_port}]
                
                if install_flow(agg_switch, match, actions, priority=1500):
                    print(f"  ✅ Agg {agg_switch}: Route IP to edge {server_switch} (port {out_port})")
                    total_flows += 1
                
                match_arp = {"eth_type": 2054, "arp_tpa": server_ip}
                actions = [{"type": "OUTPUT", "port": out_port}]
                
                if install_flow(agg_switch, match_arp, actions, priority=1500):
                    print(f"  ✅ Agg {agg_switch}: Route ARP to edge {server_switch} (port {out_port})")
                    total_flows += 1
                
                # Return path for hosts in THIS agg's edges (same pod)
                for dest_edge, dest_port in edge_ports.items():
                    if dest_edge == server_switch:
                        continue
                    
                    for host_port, host_ip in HOST_PORTS.get(dest_edge, []):
                        # Return IP traffic
                        match = {"eth_type": 2048, "ipv4_dst": host_ip}
                        actions = [{"type": "OUTPUT", "port": dest_port}]
                        if install_flow(agg_switch, match, actions, priority=1500):
                            total_flows += 1
                        
                        # Return ARP traffic
                        match_arp = {"eth_type": 2054, "arp_tpa": host_ip}
                        actions = [{"type": "OUTPUT", "port": dest_port}]
                        if install_flow(agg_switch, match_arp, actions, priority=1500):
                            total_flows += 1
                
                # CRITICAL FIX: Return path for hosts in OTHER pods (send to CORE!)
                for other_switch, hosts in HOST_PORTS.items():
                    # Skip edges connected to THIS agg
                    if other_switch in edge_ports:
                        continue
                    
                    # Determine which core ports to use
                    # Agg 0 (even) -> Core 1, 2 (ports 1, 2)
                    # Agg 1 (odd) -> Core 3, 4 (ports 1, 2)
                    # We can just ECMP or pick one. Let's pick port 1 for simplicity.
                    # Wait, we need to be careful.
                    # In mininet_topology:
                    # self.addLink(agg0, cores[0]) -> Port 1 on Agg0
                    # self.addLink(agg0, cores[1]) -> Port 2 on Agg0
                    # self.addLink(agg1, cores[2]) -> Port 1 on Agg1
                    # self.addLink(agg1, cores[3]) -> Port 2 on Agg1
                    
                    uplink_ports = [1, 2]
                    
                    for host_port, host_ip in hosts:
                        # Return IP traffic - send to core (ECMP-like: use both ports)
                        match = {"eth_type": 2048, "ipv4_dst": host_ip}
                        actions = [{"type": "OUTPUT", "port": 1}] # Default to port 1
                        if install_flow(agg_switch, match, actions, priority=1400):
                            total_flows += 1
                        
                        # Return ARP traffic - send to core
                        match_arp = {"eth_type": 2054, "arp_tpa": host_ip}
                        actions = [{"type": "OUTPUT", "port": 1}]
                        if install_flow(agg_switch, match_arp, actions, priority=1400):
                            total_flows += 1
            else:
                # This agg is in different pod - send UP to core
                # Same logic: Agg0 -> Core 1/2 (Port 1/2), Agg1 -> Core 3/4 (Port 1/2)
                
                match = {"eth_type": 2048, "ipv4_dst": server_ip}
                actions = [{"type": "OUTPUT", "port": 1}] # Default to port 1
                
                if install_flow(agg_switch, match, actions, priority=1500):
                    print(f"  ✅ Agg {agg_switch}: Send IP to core (port 1)")
                    total_flows += 1
                
                match_arp = {"eth_type": 2054, "arp_tpa": server_ip}
                actions = [{"type": "OUTPUT", "port": 1}]
                
                if install_flow(agg_switch, match_arp, actions, priority=1500):
                    print(f"  ✅ Agg {agg_switch}: Send ARP to core (port 1)")
                    total_flows += 1
                
                # Return path for hosts connected to THIS agg (send down to edges)
                for dest_edge, dest_port in edge_ports.items():
                    for host_port, host_ip in HOST_PORTS.get(dest_edge, []):
                        # Return IP traffic
                        match = {"eth_type": 2048, "ipv4_dst": host_ip}
                        actions = [{"type": "OUTPUT", "port": dest_port}]
                        if install_flow(agg_switch, match, actions, priority=1400):
                            total_flows += 1
                        
                        # Return ARP traffic
                        match_arp = {"eth_type": 2054, "arp_tpa": host_ip}
                        actions = [{"type": "OUTPUT", "port": dest_port}]
                        if install_flow(agg_switch, match_arp, actions, priority=1400):
                            total_flows += 1
        
        # ========================================
        # STEP 4: Core switches (IP + ARP) - NEW!
        # ========================================
        # Find which agg switches connect to server's edge
        server_aggs = [agg for agg, edges in AGG_TO_EDGE.items() if server_switch in edges]
        
        for core_switch, agg_ports in CORE_TO_AGG.items():
            for server_agg in server_aggs:
                if server_agg in agg_ports:
                    out_port = agg_ports[server_agg]
                    
                    # FORWARD: To server's agg
                    match = {"eth_type": 2048, "ipv4_dst": server_ip}
                    actions = [{"type": "OUTPUT", "port": out_port}]
                    
                    if install_flow(core_switch, match, actions, priority=500):
                        print(f"  ✅ Core {core_switch}: Route IP to agg {server_agg} (port {out_port})")
                        total_flows += 1
                    
                    match_arp = {"eth_type": 2054, "arp_tpa": server_ip}
                    actions = [{"type": "OUTPUT", "port": out_port}]
                    
                    if install_flow(core_switch, match_arp, actions, priority=500):
                        print(f"  ✅ Core {core_switch}: Route ARP to agg {server_agg} (port {out_port})")
                        total_flows += 1
            
            # REVERSE: Route return traffic from server's agg to other aggs
            for dest_agg, out_port in agg_ports.items():
                if dest_agg in server_aggs:
                    continue
                
                # Route return traffic to all hosts connected to this agg
                for dest_edge in AGG_TO_EDGE.get(dest_agg, {}).keys():
                    for host_port, host_ip in HOST_PORTS.get(dest_edge, []):
                        match = {"eth_type": 2048, "ipv4_dst": host_ip}
                        actions = [{"type": "OUTPUT", "port": out_port}]
                        if install_flow(core_switch, match, actions, priority=500):
                            total_flows += 1
                        
                        match_arp = {"eth_type": 2054, "arp_tpa": host_ip}
                        actions = [{"type": "OUTPUT", "port": out_port}]
                        if install_flow(core_switch, match_arp, actions, priority=500):
                            total_flows += 1
        
        print()
        print()
        
        # ========================================
        # STEP 5: VIP ARP Handling (Force to Controller)
        # ========================================
        print("📍 Setting up VIP ARP rules (Force to Controller)")
        for edge_switch in EDGE_SWITCHES:
            # Match ARP for VIP (10.0.0.100)
            match_vip_arp = {"eth_type": 2054, "arp_tpa": "10.0.0.100"}
            # Output to CONTROLLER (OFPP_CONTROLLER = 0xfffffffd = 4294967293)
            actions = [{"type": "OUTPUT", "port": 4294967293}]
            
            if install_flow(edge_switch, match_vip_arp, actions, priority=5000):
                print(f"  ✅ Edge {edge_switch}: Force VIP ARP to Controller")
                total_flows += 1
    
    print("="*70)
    print(f"✅ Routing setup complete! Installed {total_flows} flows")
    print("="*70)
    
    return total_flows > 0

def test_connectivity(net=None):
    """Test routing by pinging servers from various clients"""
    if net is None:
        from mininet_topology import start_network
        net = start_network()
        time.sleep(3)
        should_stop = True
    else:
        should_stop = False
    
    print("\n" + "="*70)
    print("Testing Client → Server Connectivity")
    print("="*70 + "\n")
    
    # Test from clients on different switches
    # Test from ALL clients to ALL servers
    print("Testing connectivity from EVERY host to ALL servers...")
    
    servers = ['10.0.0.1', '10.0.0.2', '10.0.0.3']
    success_count = 0
    total_tests = 0
    
    # We have 16 hosts: h1...h16
    for i in range(1, 17):
        client_name = f'h{i}'
        client = net.get(client_name)
        client_ip = f'10.0.0.{i}'
        
        for server_ip in servers:
            # Skip pinging self
            if client_ip == server_ip:
                continue
                
            total_tests += 1
            result = client.cmd(f'ping -c 1 -W 1 {server_ip}')
            
            if '1 received' in result:
                # print(f"  ✅ {client_name} → {server_ip}")
                success_count += 1
                sys.stdout.write('.')
            else:
                print(f"\n  ❌ FAIL: {client_name} → {server_ip}")
                sys.stdout.write('X')
            sys.stdout.flush()
            
    print(f"\n\n{'='*70}")
    print(f"📊 Results: {success_count}/{total_tests} tests passed ({success_count/total_tests*100:.0f}%)")
    print(f"{'='*70}\n")
    
    if should_stop:
        net.stop()
    
    return success_count == total_tests

def main():
    print("\n" + "="*70)
    print("DRL Load Balancer - Complete Fat-Tree Routing Setup")
    print("="*70)
    
    # Check if we should run in test mode
    if len(sys.argv) > 1 and sys.argv[1] == '--test':
        print("\n🧪 TEST MODE: Will start network, install flows, and test\n")
        
        from mininet_topology import start_network
        
        print("[1] Starting Mininet network...")
        net = start_network()
        time.sleep(3)
        
        print("\n[2] Installing routing flows...")
        if not setup_complete_routing():
            print("\n❌ Failed to install flows!")
            net.stop()
            return
        
        time.sleep(2)
        
        print("\n[3] Testing connectivity...")
        success = test_connectivity(net)
        
        if success:
            print("✅✅✅ SUCCESS! All routing tests passed! ✅✅✅")
            print("\nYour DRL load balancer is ready to train!")
            print("Next: sudo python3 trainer_with_real_monitoring.py\n")
        else:
            print("⚠️  Some tests failed. Check controller logs.\n")
        
        net.stop()
    
    else:
        # Normal mode: just install flows (network must already be running)
        print("\n⚠️  Make sure controller and Mininet are running:")
        print("   Terminal 1: ryu-manager ryu_controller.py")
        print("   Terminal 2: sudo python3 mininet_topology.py")
        print("\nInstalling flows...\n")
        
        if setup_complete_routing():
            print("\n✅ Flows installed! Test with:")
            print("   python3 setup_routing_complete.py --test")
        else:
            print("\n❌ Failed to install flows!")

if __name__ == '__main__':
    main()