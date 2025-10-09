# trainer.py
import requests
import json
import time
import yaml
import numpy as np
import os
from drl_agent import DQNAgent
from utils.metrics import collect_host_metrics, calculate_reward

RYU_URL = 'http://127.0.0.1:8080/sdrlb'

def save_checkpoint(agent, episode):
    os.makedirs('models/checkpoints', exist_ok=True)
    agent.save_model(f'models/checkpoints/dqn_ep{episode}.pth')

def save_final(agent):
    os.makedirs('models/final', exist_ok=True)
    agent.save_model('models/final/dqn_final.pth')

def build_state(port_stats, flow_stats, host_metrics, config):
    cpus = [h.get('cpu', 0.0) for h in host_metrics]
    mems = [h.get('mem', 0.0) for h in host_metrics]
    rtts = [h.get('rtt', 0.0) for h in host_metrics]

    port_values = sorted(port_stats.items(), key=lambda x: x[0])
    port_bytes = [p[1] for p in port_values]
    port_norm = [b / (10**7 + 1) for b in port_bytes]

    state = cpus + mems + rtts + port_norm
    sd = config['drl']['state_dim']
    state = np.array(state, dtype=float)
    if len(state) >= sd:
        state = state[:sd]
    else:
        state = np.pad(state, (0, sd - len(state)))
    return state

def get_port_and_flow_stats(dpid):
    try:
        p = requests.get(f'{RYU_URL}/stats/port/{dpid}', timeout=5).json()
        f = requests.get(f'{RYU_URL}/stats/flow/{dpid}', timeout=5).json()
        return p, f
    except Exception:
        return {}, {}

def get_host_ports(dpid):
    try:
        return requests.get(f'{RYU_URL}/host_ports/{dpid}', timeout=5).json()
    except Exception:
        return {}

def post_flow_entry(entry):
    try:
        r = requests.post(f'{RYU_URL}/stats/flowentry/add', json=entry, timeout=5)
        return r.status_code, r.text
    except Exception as e:
        return 500, str(e)

def train(net=None):
    # Load config
    with open('config.yaml') as f:
        config = yaml.safe_load(f)

    agent = DQNAgent(config)
    episodes = config['training']['episodes']
    episode_duration = config['training'].get('episode_duration', 60)

    # Detect all DPIDs dynamically from Ryu
    all_dpid = []
    try:
        for i in range(1, 20):  # assume max 20 switches
            r = requests.get(f'{RYU_URL}/ports/{i}', timeout=1)
            if r.status_code == 200:
                all_dpid.append(i)
    except Exception:
        all_dpid = [1]  # fallback

    print(f"[TRAIN] Detected DPIDs: {all_dpid}")

    try:
        for ep in range(episodes):
            print(f'[TRAIN] Start episode {ep}')
            start = time.time()
            total_reward = 0.0

            while time.time() - start < episode_duration:
                # collect metrics for all hosts
                host_metrics = collect_host_metrics(net=net, use_real_hosts=False)

                for dpid in all_dpid:
                    port_stats, flow_stats = get_port_and_flow_stats(dpid)
                    host_ports = get_host_ports(dpid)
                    if not host_ports:
                        continue
                    host_names = list(host_ports.keys())

                    # Build state
                    s = build_state(port_stats, flow_stats, host_metrics, config)

                    # Select action
                    a = agent.act(s)

                    server_host = host_names[a % len(host_names)]
                    server_port = host_ports[server_host]

                    # Post flow entry to controller
                    flow_entry = {
                        "dpid": dpid,
                        "match": {"in_port": 1, "eth_type": 0x0800},
                        "actions": [{"type": "OUTPUT", "port": server_port}],
                        "priority": 1000,
                        "idle_timeout": 30
                    }
                    post_flow_entry(flow_entry)

                    # Calculate reward for this action
                    reward = calculate_reward(port_stats, host_metrics,
                                              config.get('training_reward_weights', {}))
                    total_reward += reward

                    # Build next state
                    s_next = build_state(port_stats, flow_stats, host_metrics, config)
                    agent.remember(s, a, reward, s_next)
                    agent.train()

                time.sleep(1.0)

            agent.update_target()
            print(f'[TRAIN] Episode {ep} total reward: {total_reward:.3f}')
            if ep % 10 == 0:
                save_checkpoint(agent, ep)

    except KeyboardInterrupt:
        print('[TRAIN] Interrupted by user')

    finally:
        save_final(agent)
        print('[TRAIN] Finished')

if __name__ == '__main__':
    from mininet_topology import start_network
    net = start_network()
    train(net)
