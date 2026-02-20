#!/usr/bin/env python3
"""
Quick test to verify traffic_generator.py works correctly
"""

from mininet_topology import start_network
from traffic_generator import TrafficGenerator, ConstantTraffic
import time

print("\n" + "="*60)
print("Testing Traffic Generator Setup")
print("="*60 + "\n")

# Start network
print("[1] Starting network...")
net = start_network()

# Create traffic generator
print("\n[2] Creating traffic generator...")
traffic_gen = TrafficGenerator(
    net,
    virtual_ip="10.0.0.100",
    server_hosts=['h1', 'h2', 'h3']
)

# Verify separation
print("\n[3] Verifying server/client separation:")
print(f"  Servers: {traffic_gen.server_hosts}")
print(f"  Clients: {[h.name for h in traffic_gen.clients]}")

# Check no overlap
server_names = set(traffic_gen.server_hosts)
client_names = set([h.name for h in traffic_gen.clients])
overlap = server_names & client_names

if overlap:
    print(f"   ERROR: Overlap found: {overlap}")
else:
    print(f"   No overlap - servers and clients are separate!")

# Start servers
print("\n[4] Starting HTTP servers...")
traffic_gen.start_http_servers()

# Verify servers are running
print("\n[5] Verifying servers respond:")
for server_name in traffic_gen.server_hosts:
    server = net.get(server_name)
    result = server.cmd('curl -s -m 1 http://127.0.0.1/')
    if server_name in result:
        print(f"   {server_name}: Responding")
    else:
        print(f"   {server_name}: Not responding")

# Test client can reach server (use same-switch pairs)
print("\n[6] Testing client → server connectivity:")
print("  Testing same-switch pairs:")

# h1, h2 are on switch 200
# Test with h2 → h1 (same switch)
client_h2 = net.get('h2')
server_h1 = net.get('h1')
result = client_h2.cmd(f'curl -s -m 2 http://{server_h1.IP()}/')
if 'h1' in result:
    print(f"   h2 → h1 (same switch): OK")
else:
    print(f"   h2 → h1 (same switch): Failed")

# h3, h4 are on switch 201
# Test with h4 → h3 (same switch)
client_h4 = net.get('h4')
server_h3 = net.get('h3')
result = client_h4.cmd(f'curl -s -m 2 http://{server_h3.IP()}/')
if 'h3' in result:
    print(f"   h4 → h3 (same switch): OK")
else:
    print(f"   h4 → h3 (same switch): Failed")

print("\n  Testing cross-switch (will likely fail):")
# h4 (switch 201) → h1 (switch 200)
result = client_h4.cmd(f'curl -s -m 2 http://{server_h1.IP()}/')
if 'h1' in result:
    print(f"   h4 → h1 (cross-switch): OK")
else:
    print(f"   h4 → h1 (cross-switch): Failed (expected)")

# Quick traffic test - use SAME-SWITCH pairs
print("\n[7] Running quick traffic test (5 seconds)...")
print("  Using same-switch client-server pairs...")
traffic_gen.running = True

start = time.time()
test_pairs = [
    (net.get('h2'), net.get('h1')),  # Same switch 200
    (net.get('h4'), net.get('h3')),  # Same switch 201
]

while time.time() - start < 5:
    # Use same-switch pairs
    client, server = random.choice(test_pairs)
    
    # Send request
    result = client.cmd(f'curl -s -m 1 http://{server.IP()}/')
    if len(result) > 10:  # Got actual content
        traffic_gen.stats['successful_requests'] += 1
    else:
        traffic_gen.stats['failed_requests'] += 1
    
    traffic_gen.stats['total_requests'] += 1
    
    time.sleep(0.5)

print(f"\n[8] Traffic test results:")
print(f"  - Requests sent: {traffic_gen.stats['total_requests']}")
print(f"  - Successful: {traffic_gen.stats['successful_requests']}")
print(f"  - Failed: {traffic_gen.stats['failed_requests']}")
success_rate = (traffic_gen.stats['successful_requests'] / 
                max(traffic_gen.stats['total_requests'], 1)) * 100
print(f"  - Success rate: {success_rate:.1f}%")

# Cleanup
print("\n[9] Cleaning up...")
traffic_gen.stop()
net.stop()

print("\n" + "="*60)
print(" Test completed!")
print("="*60 + "\n")