# 🧠 Deep Reinforcement Learning–Based Server Load Balancing (SDN + Ryu + Mininet)

This project implements a **Server Load Balancing System** using **Software-Defined Networking (SDN)** with the **Ryu controller** and **Mininet** network emulator.

The current phase includes:
- ✅ Working **controller (Ryu)** with REST APIs
- ✅ Working **Fat-Tree network topology** in Mininet
- 🚧 Planned integration of **Deep Reinforcement Learning (DRL)** for dynamic load balancing

---

## ⚙️ 1. Requirements

Make sure the following are installed:

### System packages
```bash
sudo apt update
sudo apt install -y python3 python3-pip mininet openvswitch-switch
```

### Python dependencies
```bash
pip install ryu requests pyyaml numpy
```

### Optional (for DRL phase)
```bash
pip install torch gym
```

✅ **Tested on:** Ubuntu 20.04 / Linux Mint 21 with Python 3.8+

---

## 📁 2. Project Structure

```
.
├── controller.py           # Ryu controller (handles flows, ports, APIs)
├── mininet_topology.py     # Fat-tree topology setup for Mininet
├── trainer.py              # DRL training logic (under development)
├── utils/
│   └── metrics.py          # Host metric utilities (CPU, memory, RTT simulation)
├── config.yaml             # DRL configuration parameters (optional)
└── README.md               # Project documentation
```

---

## 🚀 3. How to Run the Project

### Step 1. Start the Ryu Controller
```bash
ryu-manager ryu_controller.py
```

You should see logs like:
```
Datapath 200 connected
Datapath 201 connected
Datapath 202 connected
```

### Step 2. Run the Fat-Tree Topology
In a new terminal:
```bash
sudo python3 mininet_topology.py
```

### Step 3. Test Connectivity
Once Mininet starts:
```bash
mininet> h1 ping -c1 h2
```

You should see destination host unreachable because it is yet to install the flow rules in the switch (having dpid:200).So for this we first install flow rules via REST API.

Also once you created topology then it persits untill you killed with command :
```bash
sudo mn -c
```
And make sure you restart controller after that and run the topology again !!

---

## 🌐 4. Verify Controller API Endpoints

**Base URL:** `http://127.0.0.1:8080/sdrlb`

### Examples

| Endpoint | Description | Example |
|----------|-------------|---------|
| `/stats/port/<dpid>` | Get port statistics | `curl http://127.0.0.1:8080/sdrlb/stats/port/200` |
| `/stats/flow/<dpid>` | Get flow statistics | `curl http://127.0.0.1:8080/sdrlb/stats/flow/200` |
| `/host_ports/<dpid>` | Get host-port mappings | `curl http://127.0.0.1:8080/sdrlb/host_ports/200` |
| `/stats/flowentry/add` | Add flow manually | see below |

### Example: Add Flow via REST
This code can be seen in `add_flows.sh ` and on its execution it sucessfully installed the flow rule in switch dpid:200 which is also connected with hosts `h1` and `h2 ` amd make it pingable between them.
```bash
#!/bin/bash

RYU_URL="http://127.0.0.1:8080/sdrlb/stats/flowentry/add"
DPID=200

# h1 → h2 (IP)
curl -s -X POST -H "Content-Type: application/json" \
-d '{"dpid":'"$DPID"',"match":{"in_port":3,"eth_type":2048},"actions":[{"type":"OUTPUT","port":4}],"priority":1000,"idle_timeout":60}' \
$RYU_URL

# h2 → h1 (IP)
curl -s -X POST -H "Content-Type: application/json" \
-d '{"dpid":'"$DPID"',"match":{"in_port":4,"eth_type":2048},"actions":[{"type":"OUTPUT","port":3}],"priority":1000,"idle_timeout":60}' \
$RYU_URL

# h1 → h2 (ARP)
curl -s -X POST -H "Content-Type: application/json" \
-d '{"dpid":'"$DPID"',"match":{"in_port":3,"eth_type":2054},"actions":[{"type":"OUTPUT","port":4}],"priority":1000,"idle_timeout":60}' \
$RYU_URL

# h2 → h1 (ARP)
curl -s -X POST -H "Content-Type: application/json" \
-d '{"dpid":'"$DPID"',"match":{"in_port":4,"eth_type":2054},"actions":[{"type":"OUTPUT","port":3}],"priority":1000,"idle_timeout":60}' \
$RYU_URL

echo "✅ All 4 flows (IP+ARP) installed for h1 <-> h2"

```
On successfull flow installation now try again:
```bash
mininet> h1 ping -c1 h2
```
It should works!!
But
#### Why IP + ARP Flows are Necessary??
ARP: needed so hosts can resolve each other’s MAC address. Without it, they never get past “who has 10.0.0.x?” stage.

IP : needed to forward actual ICMP (ping) packets once ARP resolves.

If you only install ARP flows, hosts can learn MACs, but no IP packets will ever be forwarded → host unreachable.
If you only install IP flows, ARP won’t work, so the MAC never resolves → same failure.

That’s why we need both

## 🧠 5. DRL Training (Future Phase)

`trainer.py` will later handle:
- Collecting network and host metrics
- Generating synthetic traffic
- Training a DQN Agent for adaptive load balancing

**Currently:**
- `controller.py` and `mininet_topology.py` are tested and working
- DRL logic (`trainer.py`) is a skeleton ready for integration

---

## ✅ 6. Expected Behavior

- Controller and topology start without errors
- Switches connect successfully to Ryu
- Host-to-host ping succeeds
- REST API endpoints return valid JSON responses
- Manual flow installation via REST API works correctly

---

