# DRL-SDN Load Balancer

A Deep Reinforcement Learning (DRL) based Load Balancer for Software Defined Networks (SDN) using Ryu and Mininet.

## 🚀 Overview
This project implements an intelligent load balancer that uses a Deep Q-Network (DQN) agent to dynamically route traffic across multiple servers in a Fat-Tree topology. The agent learns to minimize latency and server load variance by interacting with the SDN environment in real-time.

## 🛠️ Prerequisites
- **OS**: Linux (Ubuntu 20.04+ recommended)
- **Python**: 3.8+
- **Mininet**: Network emulator
- **Ryu**: SDN Controller framework
- **Open vSwitch**: Virtual switch

## 📦 Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/drl-sdn-load-balancer.git
   cd drl-sdn-load-balancer
   ```

2. **Create a virtual environment :**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

## 🏃‍♂️ How to Run

### 1. Start the Controller
Open a terminal and run the Ryu controller:
```bash
# Ensure you are in the project root and venv is activated 
ryu-manager ryu_controller.py
```

### 2. Start Training
Open a second terminal and run the training script:
```bash
sudo ./venv/bin/python3 train.py
```
This will:
- Initialize the Mininet topology (Fat-Tree).
- Connect to the Ryu controller.
- Start the DRL agent training loop.
- Generate traffic using `ab` (ApacheBench).

## 📂 Project Structure
- `ryu_controller.py`: The SDN controller logic (ARP, VIP, DRL integration).
- `train.py`: Main training script (Environment loop, Traffic generation).
- `drl_agent.py`: Deep Q-Network agent implementation (PyTorch).
- `mininet_topology.py`: Custom Fat-Tree topology definition.
- `setup_network.py`: Helper script to configure static routes and flows.
- `traffic_generator.py`: Wrapper for generating HTTP traffic.
- `real_server_monitor.py`: Monitors server metrics (CPU, Latency).

## 📊 Features
- **Dynamic Routing**: DRL agent selects optimal servers per flow.
- **Real-time Monitoring**: Latency and Load tracking.
- **Training Mode**: Disables session persistence for faster learning.
- **VIP Handling**: Virtual IP (10.0.0.100) for transparent load balancing.

## 📝 Configuration
Edit `config.yaml` to adjust:
- Training parameters (Episodes, Batch size, Learning rate).
- Reward function weights.
- Traffic patterns.

## 🤝 Contributing
Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

## 📄 License
[MIT](https://choosealicense.com/licenses/mit/)
