#!/usr/bin/env python3
"""
Complete DRL Training with REAL Server Load Monitoring

This is the CORRECT way to train for server load balancing:
1. Real HTTP servers running on h1, h2, h3
2. Real traffic from clients creating actual CPU load
3. REAL CPU/memory/latency measurement
4. DRL agent learns to balance REAL load
"""

import requests
import json
import time
import yaml
import numpy as np
import os
import threading
import sys
from drl_agent import DQNAgent
# from trainer import get_port_and_flow_stats, get_host_ports, post_flow_entry, detect_dpids
# build_state is now local or imported from controller logic, let's redefine it here to match controller
from traffic_generator import TrafficGenerator, ConstantTraffic, BurstyTraffic, IncrementalTraffic
from real_server_monitor import ServerMonitor, collect_real_server_metrics, calculate_reward_from_real_load
from setup_network import setup_complete_routing

# Import metrics and set up real monitoring
import utils.metrics as metrics_module

# Base URL for Ryu Controller
RYU_BASE_URL = 'http://127.0.0.1:8080'
RYU_URL = f'{RYU_BASE_URL}/sdrlb'  # Keep for backward compatibility with existing code using RYU_URL

def detect_dpids():
    """Query connected switches from Ryu controller"""
    try:
        resp = requests.get(f'{RYU_BASE_URL}/stats/switches', timeout=2.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[WARN] Failed to detect DPIDs: {e}")
    return []

def build_state(server_metrics):
    """Build enhanced state vector (18 features) for better learning signal"""
    # server_metrics is a dict: {'h1': {...}, 'h2': {...}, 'h3': {...}}
    
    flat_state = []
    
    # Track previous metrics for rate calculations
    if not hasattr(build_state, 'prev_metrics'):
        build_state.prev_metrics = {}
    
    for h in ['h1', 'h2', 'h3']:
        d = server_metrics.get(h, {})
        prev = build_state.prev_metrics.get(h, {})
        
        # Basic metrics (normalized to [0, 1])
        cpu = d.get('cpu', 0.0)
        memory = d.get('memory', 0.0)
        rtt = min(d.get('rtt', 0.0) / 0.1, 1.0)  # Normalize: 100ms = 1.0
        load_score = d.get('load_score', 0.0)
        
        # Connection rate (change from previous)
        curr_conns = d.get('connections', 0)
        prev_conns = prev.get('connections', curr_conns)
        conn_rate = min(abs(curr_conns - prev_conns) / 100.0, 1.0)  # Normalize
        
        # RTT trend (is it getting better or worse?)
        curr_rtt = d.get('rtt', 0.0)
        prev_rtt = prev.get('rtt', curr_rtt)
        rtt_trend = 0.5 + min(max((prev_rtt - curr_rtt) / 0.05, -0.5), 0.5)  # [-0.5, 0.5] → [0, 1]
        
        flat_state.extend([
            cpu,
            memory,
            rtt,
            load_score,
            conn_rate,
            rtt_trend
        ])
        
        # Store for next iteration
        build_state.prev_metrics[h] = d.copy()
        
    return np.array(flat_state, dtype=np.float32)

class RealLoadBalancerTrainer:
    """
    Complete trainer with REAL server load monitoring
    """
    
    def __init__(self, config_path='config.yaml'):
        # Load configuration
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
        
        self.agent = None
        self.net = None
        self.traffic_gen = None
        self.server_monitor = None
        self.training_active = False
        
        # Training statistics
        self.episode_rewards = []
        self.episode_losses = []
        self.episode_metrics = []
        
        # Action logging
        self.action_log_file = 'action_log.csv'
        with open(self.action_log_file, 'w') as f:
            f.write("timestamp,episode,step,state,action,reward,next_state,done\n")
        
        # Weight sync counter (sync every N steps to avoid timeouts)
        self.sync_counter = 0
        self.sync_interval = 10  # Sync every 10 steps instead of every step

    def log_action(self, episode, step, state, action, reward, next_state, done):
        """Log action details to CSV"""
        with open(self.action_log_file, 'a') as f:
            state_str = '|'.join([f'{x:.2f}' for x in state])
            next_state_str = '|'.join([f'{x:.2f}' for x in next_state]) if next_state is not None else ''
            f.write(f"{time.time()},{episode},{step},{state_str},{action},{reward:.4f},{next_state_str},{done}\n")
    
    def sync_weights_to_controller(self):
        """Synchronize trained weights to controller's DRL agent"""
        try:
            import torch
            import base64
            import io
            
            # Serialize Q-network weights
            q_net_buffer = io.BytesIO()
            torch.save(self.agent.q_net.state_dict(), q_net_buffer)
            q_net_bytes = q_net_buffer.getvalue()
            q_net_b64 = base64.b64encode(q_net_bytes).decode('utf-8')
            
            # Serialize target network weights
            target_net_buffer = io.BytesIO()
            torch.save(self.agent.target_net.state_dict(), target_net_buffer)
            target_net_bytes = target_net_buffer.getvalue()
            target_net_b64 = base64.b64encode(target_net_bytes).decode('utf-8')
            
            # Send to controller
            resp = requests.post(
                f'{RYU_URL}/update_weights',
                json={
                    'q_net_weights': q_net_b64,
                    'target_net_weights': target_net_b64
                },
                timeout=5.0  # Increased from 2.0 to handle agent initialization
            )
            
            if resp.status_code == 200:
                # Reset failure counter on success
                if not hasattr(self, '_sync_failures'):
                    self._sync_failures = 0
                self._sync_failures = 0
                return True
            else:
                # Only print every 10th failure to reduce spam
                if not hasattr(self, '_sync_failures'):
                    self._sync_failures = 0
                self._sync_failures += 1
                if self._sync_failures == 1 or self._sync_failures % 10 == 0:
                    print(f"⚠️  Weight sync failed: {resp.status_code} (failure #{self._sync_failures})")
                return False
                
        except Exception as e:
            # Only print first error and every 10th error
            if not hasattr(self, '_sync_failures'):
                self._sync_failures = 0
            self._sync_failures += 1
            if self._sync_failures == 1 or self._sync_failures % 10 == 0:
                print(f"⚠️  Weight sync error: {e} (failure #{self._sync_failures})")
            return False
    
    def setup_network(self):
        """Initialize Mininet network"""
        print("\n[SETUP] Starting Mininet network...")
        from mininet_topology import start_network
        
        self.net = start_network()
        print("[SETUP] Network started successfully")
        
        # Debug: List Mininet switches
        print(f"[DEBUG] Mininet switches: {[s.name for s in self.net.switches]}")
        
        print("[SETUP] Waiting 15s for switches to connect to controller...")
        time.sleep(15)
        
        # Debug: Check Ryu switches (with retry)
        ryu_switches = []
        for attempt in range(3):
            try:
                resp = requests.get(f'{RYU_BASE_URL}/stats/switches', timeout=3.0)
                if resp.status_code == 200:
                    ryu_switches = resp.json()
                    print(f"[DEBUG] Ryu switches ({len(ryu_switches)}): {sorted(ryu_switches)}")
                    break
                else:
                    print(f"[DEBUG] Switch query attempt {attempt+1} failed: HTTP {resp.status_code}")
            except Exception as e:
                print(f"[DEBUG] Switch query attempt {attempt+1} failed: {e}")
                if attempt < 2:
                    time.sleep(2)
        
        if not ryu_switches:
            print("[WARN] ⚠️  Could not query Ryu switches, but continuing anyway...")
        
        # Install routing flows
        print("[SETUP] Installing routing flows...")
        setup_complete_routing()
        time.sleep(5)
        
        # Verify connectivity
        print("[SETUP] Verifying connectivity (h1 -> h3)...")
        h1 = self.net.get('h1')
        h3 = self.net.get('h3')
        result = h1.cmd(f'ping -c 3 -W 1 {h3.IP()}')
        if "0% packet loss" in result:
            print("[SETUP] ✅ Connectivity verified!")
        else:
            print(f"[SETUP] ❌ Connectivity check failed:\n{result}")
            # Don't abort, but warn loudly
            print("⚠️  WARNING: Multi-hop routing might be broken!")
    
    def setup_monitor(self):
        """Start monitoring (Servers are already started by TrafficGenerator)"""
        
        server_hosts = ['h1', 'h2', 'h3']
        
        # Initialize server monitor for REAL metrics
        print("\n[SETUP] Initializing REAL server monitor...")
        self.server_monitor = ServerMonitor(self.net, server_hosts=server_hosts)
        self.server_monitor.start_monitoring(interval=2.0)
        
        # Set the global monitor in metrics module
        metrics_module.set_server_monitor(self.server_monitor)
        
        print("[SETUP] ✅ Real server monitoring active!\n")
    
    def setup_traffic_generator(self):
        """Initialize traffic generator"""
        print("[SETUP] Initializing traffic generator...")
        self.traffic_gen = TrafficGenerator(self.net, virtual_ip="10.0.0.100", virtual_port=8000)
        
        # CRITICAL: Start the HTTP servers!
        self.traffic_gen.start_http_servers()
        
        print("[SETUP] Traffic generator ready\n")
    
    def setup_agent(self):
        """Initialize DRL agent"""
        print("[SETUP] Initializing DRL agent...")
        self.agent = DQNAgent(self.config)
        print(f"[SETUP] Agent created: state_dim={self.config['drl']['state_dim']}, action_dim={self.config['drl']['action_dim']}")
        
        # Enable training mode in controller (disables session persistence)
        try:
            resp = requests.post(
                f'{RYU_URL}/set_training_mode',
                json={'enabled': True},
                timeout=2.0
            )
            if resp.status_code == 200:
                print("[SETUP] ✅ Controller training mode ENABLED (session persistence disabled)")
            else:
                print(f"[SETUP] ⚠️  Failed to enable training mode: {resp.status_code}")
        except Exception as e:
            print(f"[SETUP] ⚠️  Could not enable training mode: {e}")
        
        print("[SETUP] ✅ DRL agent ready\n")
    
    def generate_traffic_thread(self, pattern, duration):
        """Run traffic generation in background"""
        print(f"[TRAFFIC] Starting {pattern.name} pattern for {duration}s")
        
        start_time = time.time()
        request_count = 0
        
        while self.training_active and (time.time() - start_time) < duration:
            elapsed = time.time() - start_time
            current_rate = pattern.get_rate(elapsed)
            
            if current_rate > 0:
                # Calculate batch size for 1 second (or less)
                batch_duration = 1.0 
                batch_size = int(current_rate * batch_duration)
                if batch_size < 1: batch_size = 1
                
                # Send HTTP requests using Apache Bench (ab) for HIGH LOAD
                import random
                client = random.choice(self.traffic_gen.clients)
                
                # Log which client is being used (for debugging)
                if request_count % 500 == 0:  # Log every 500 requests
                    print(f"[DEBUG] Using client {client.name} ({client.IP()}) for traffic")
                
                success, count = self.traffic_gen.send_batch(
                    client,
                    self.traffic_gen.virtual_ip,
                    self.traffic_gen.virtual_port,
                    count=batch_size,
                    concurrency=min(10, batch_size)
                )
                
                self.traffic_gen.stats['total_requests'] += batch_size
                request_count += batch_size
                
                # Sleep for the batch duration
                time.sleep(batch_duration)
        
        print(f"[TRAFFIC] Pattern completed: {request_count} requests sent")
    
    def train_episode(self, episode_num, episode_duration, traffic_pattern):
        """
        Train one episode with REAL traffic and metrics
        """
        print(f"\n{'='*70}")
        print(f"Episode {episode_num+1}")
        print(f"{'='*70}")
        print(f"Traffic Pattern: {traffic_pattern.name}")
        print(f"Duration: {episode_duration}s\n")
        
        # Show initial server status
        print("[BEFORE] Initial server status:")
        self.server_monitor.print_status()
        
        # Start traffic generation
        traffic_thread = threading.Thread(
            target=self.generate_traffic_thread,
            args=(traffic_pattern, episode_duration)
        )
        traffic_thread.daemon = True
        traffic_thread.start()
        
        # Detect active switches
        all_dpid = detect_dpids()
        if not all_dpid:
            all_dpid = [200]
        
        print(f"[TRAIN] Active switches: {all_dpid}")
        
        # Clear controller sessions for fresh episode start
        try:
            resp = requests.post(
                f'{RYU_URL}/set_training_mode',
                json={'enabled': True},  # This also clears sessions
                timeout=2.0
            )
        except Exception:
            pass  # Ignore errors, sessions will clear eventually
        
        # Initial weight sync to bootstrap controller's agent
        print("[TRAIN] Syncing initial weights to controller...")
        if self.sync_weights_to_controller():
            print("[TRAIN] ✅ Controller initialized with trainer's weights")
        else:
            print("[TRAIN] ⚠️  Initial weight sync failed, controller will initialize on first sync")
        
        start_time = time.time()
        total_reward = 0.0
        total_loss = 0.0
        step_count = 0
        action_counts = {}
        
        # Training loop
        while time.time() - start_time < episode_duration:
            # 1. Get current state (12-dim vector)
            # We use the monitor directly now
            current_metrics = self.server_monitor.get_metrics()
            state = build_state(current_metrics)
            
            # 2. Agent selects action (0, 1, 2)
            action = self.agent.act(state)
            action_counts[action] = action_counts.get(action, 0) + 1
            
            # 3. Sync weights to controller (every 10 steps to avoid timeouts)
            self.sync_counter += 1
            if self.sync_counter % self.sync_interval == 0:
                self.sync_weights_to_controller()
            
            # 4. Wait for action to have effect (1 second)
            time.sleep(1.0)
            
            # 5. Get reward based on NEW state
            next_metrics = self.server_monitor.get_metrics()
            next_state = build_state(next_metrics)
            
            reward = calculate_reward_from_real_load(
                self.server_monitor,
                [], # host_metrics not needed for new reward function
                self.config.get('training_reward_weights', {})
            )
            
            total_reward += reward
            
            # 6. Store transition and Train
            done = (time.time() - start_time >= episode_duration)
            self.agent.remember(state, action, reward, next_state, done)
            
            loss = self.agent.train()
            if loss is not None:
                total_loss += loss
            
            # 7. Log action
            self.log_action(episode_num, step_count, state, action, reward, next_state, done)
            
            step_count += 1
            
            # Print progress
            if step_count % 5 == 0:
                elapsed = time.time() - start_time
                print(f"[{elapsed:.0f}s] Steps: {step_count}, Avg Reward: {total_reward/max(step_count,1):.3f}")
        
        # Wait for traffic thread
        traffic_thread.join(timeout=2)
        
        # Show final server status
        print("\n[AFTER] Final server status:")
        self.server_monitor.print_status()
        
        # Update target network
        self.agent.update_target()
        
        # Calculate averages
        avg_reward = total_reward / max(step_count, 1)
        avg_loss = total_loss / max(step_count, 1)
        
        # Get final server metrics
        final_server_metrics = self.server_monitor.get_metrics()
        server_loads = [m['load_score'] for m in final_server_metrics.values()]
        load_variance = float(np.var(server_loads))
        
        # Store statistics
        self.episode_rewards.append(avg_reward)
        self.episode_losses.append(avg_loss)
        self.episode_metrics.append({
            'total_requests': self.traffic_gen.stats['total_requests'],
            'successful_requests': self.traffic_gen.stats['successful_requests'],
            'load_variance': load_variance,
            'action_distribution': action_counts,
            'server_metrics': {k: v.copy() for k, v in final_server_metrics.items()}
        })
        
        # Print summary
        print(f"\n{'='*70}")
        print(f"EPISODE {episode_num+1} SUMMARY")
        print(f"{'='*70}")
        print(f"Training Metrics:")
        print(f"  - Total Reward: {total_reward:.3f}")
        print(f"  - Avg Reward: {avg_reward:.3f}")
        print(f"  - Avg Loss: {avg_loss:.4f}")
        print(f"  - Epsilon: {self.agent.epsilon:.3f}")
        print(f"  - Steps: {step_count}")
        print(f"\nTraffic Metrics:")
        print(f"  - Total Requests: {self.traffic_gen.stats['total_requests']}")
        print(f"  - Successful: {self.traffic_gen.stats['successful_requests']}")
        success_rate = (self.traffic_gen.stats['successful_requests'] / 
                       max(self.traffic_gen.stats['total_requests'], 1)) * 100
        print(f"  - Success Rate: {success_rate:.1f}%")
        print(f"\nLoad Balancing Metrics:")
        print(f"  - Load Variance: {load_variance:.6f}")
        print(f"  - Action Distribution: {action_counts}")
        print(f"{'='*70}\n")
    
    def train(self):
        """Main training loop"""
        print("\n" + "="*70)
        print("DRL Training with REAL Server Load Monitoring")
        print("="*70 + "\n")
        
        try:
            # Setup
            self.setup_network()
            
            # 1. Start Traffic Generator (Starts HTTP Servers)
            self.setup_traffic_generator()
            
            # 2. Start Monitor (After servers are ready)
            self.setup_monitor()
            
            self.setup_agent()
            
            # Training configuration
            num_episodes = self.config['training']['episodes']
            episode_duration = self.config['training']['episode_duration']
            
            # Traffic patterns
            patterns = [
                ConstantTraffic(rate=100, duration=episode_duration),
                BurstyTraffic(base_rate=50, burst_rate=400, duration=episode_duration),
                IncrementalTraffic(start_rate=50, end_rate=300, duration=episode_duration)
            ]
            
            self.training_active = True
            
            print(f"Starting training for {num_episodes} episodes...")
            print(f"Each episode: {episode_duration}s")
            print(f"Total training time: ~{(num_episodes * episode_duration) / 60:.0f} minutes\n")
            
            # Train episodes
            for ep in range(num_episodes):
                # Cycle through traffic patterns
                pattern = patterns[ep % len(patterns)]
                
                self.train_episode(ep, episode_duration, pattern)
                
                # Save checkpoint every 10 episodes
                if (ep + 1) % 10 == 0:
                    self.save_checkpoint(ep + 1)
                    print(f"✅ Checkpoint saved: episode {ep+1}\n")
            
        except KeyboardInterrupt:
            print('\n⚠️  Training interrupted by user')
        
        finally:
            self.training_active = False
            self.cleanup()
    
    def save_checkpoint(self, episode):
        """Save model checkpoint"""
        os.makedirs('models/checkpoints', exist_ok=True)
        self.agent.save_model(f'models/checkpoints/dqn_ep{episode}.pth')
    
    def save_final_model(self):
        """Save final trained model and statistics"""
        os.makedirs('models/final', exist_ok=True)
        self.agent.save_model('models/final/dqn_final.pth')
        
        # Save comprehensive training statistics
        stats = {
            'episode_rewards': self.episode_rewards,
            'episode_losses': self.episode_losses,
            'episode_metrics': self.episode_metrics,
            'final_epsilon': self.agent.epsilon,
            'total_episodes': len(self.episode_rewards),
            'config': self.config
        }
        
        os.makedirs('logs', exist_ok=True)
        with open('logs/training_with_real_load.json', 'w') as f:
            json.dump(stats, f, indent=2)
        
        print(f"\n{'='*70}")
        print("✅ TRAINING COMPLETED!")
        print(f"{'='*70}")
        print(f"Final model: models/final/dqn_final.pth")
        print(f"Statistics: logs/training_with_real_load.json")
        print(f"Total episodes: {len(self.episode_rewards)}")
        print(f"Final epsilon: {self.agent.epsilon:.3f}")
        
        # Show performance improvement
        if len(self.episode_rewards) >= 20:
            first_10_avg = np.mean(self.episode_rewards[:10])
            last_10_avg = np.mean(self.episode_rewards[-10:])
            improvement = last_10_avg - first_10_avg
            print(f"\nPerformance Improvement:")
            print(f"  First 10 episodes: {first_10_avg:.3f}")
            print(f"  Last 10 episodes: {last_10_avg:.3f}")
            print(f"  Improvement: {improvement:.3f} ({improvement/abs(first_10_avg)*100:.1f}%)")
        
        print(f"{'='*70}\n")
    
    def cleanup(self):
        """Cleanup resources"""
        print("\n[CLEANUP] Stopping services...")
        
        if self.server_monitor:
            print("  - Stopping server monitor...")
            self.server_monitor.stop_monitoring()
        
        if self.traffic_gen:
            print("  - Stopping traffic generator...")
            self.traffic_gen.stop()
        
        # Stop HTTP servers
        if self.net:
            print("  - Stopping HTTP servers...")
            for host_name in ['h1', 'h2', 'h3']:
                host = self.net.get(host_name)
                if host:
                    host.cmd('pkill -f "python3 -m http.server"')
        
        if self.net:
            print("  - Stopping network...")
            self.net.stop()
        
        if self.agent:
            print("  - Saving final model...")
            self.save_final_model()
        
        print("[CLEANUP] Done!\n")


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Train DRL agent with REAL server load monitoring'
    )
    parser.add_argument('--config', type=str, default='config.yaml',
                        help='Path to config file')
    
    args = parser.parse_args()
    
    # Check if Ryu controller is running
    print("\n[CHECK] Verifying Ryu controller...")
    try:
        response = requests.get(f'{RYU_URL}/ports/200', timeout=2)
        print("✅ Ryu controller is running\n")
    except:
        print("\n" + "="*70)
        print("❌ ERROR: Ryu controller not detected!")
        print("="*70)
        print("\nPlease start the Ryu controller first:")
        print("  Terminal 1: ryu-manager ryu_controller.py")
        print("\nThen run this script:")
        print("  Terminal 2: sudo python3 trainer_with_real_monitoring.py")
        print("\n" + "="*70 + "\n")
        sys.exit(1)
    
    # Check if running as root (needed for Mininet)
    if os.geteuid() != 0:
        print("\n" + "="*70)
        print("❌ ERROR: This script requires root privileges (for Mininet)")
        print("="*70)
        print("\nPlease run with sudo:")
        print("  sudo python3 trainer_with_real_monitoring.py")
        print("\n" + "="*70 + "\n")
        sys.exit(1)
    
    print("="*70)
    print("🚀 REAL SERVER LOAD BALANCING TRAINING")
    print("="*70)
    print("\nThis training uses:")
    print("  ✅ Real HTTP servers (h1, h2, h3)")
    print("  ✅ Real traffic generation")
    print("  ✅ Real CPU/memory measurement")
    print("  ✅ Real latency measurement")
    print("  ✅ DRL agent learns from ACTUAL load!")
    print("\n" + "="*70 + "\n")
    
    # Run training
    trainer = RealLoadBalancerTrainer(config_path=args.config)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='DRL Trainer with Real Monitoring')
    parser.add_argument('--config', type=str, default='config.yaml', help='Path to config file')
    args = parser.parse_args()
    
    # Run training
    trainer = RealLoadBalancerTrainer(config_path=args.config)
    trainer.train()

if __name__ == '__main__':
    main()