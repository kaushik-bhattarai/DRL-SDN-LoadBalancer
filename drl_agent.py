import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import random
from collections import deque
import os


# ========================================================================
# Phase 5: Prioritized Experience Replay (conditional — gate check in plan)
# ========================================================================

class SumTree:
    """Binary sum tree for O(log n) prioritized sampling."""

    def __init__(self, capacity):
        self.capacity = capacity
        self.tree = np.zeros(2 * capacity - 1)
        self.data = [None] * capacity
        self.write = 0
        self.n_entries = 0

    def _propagate(self, idx, change):
        parent = (idx - 1) // 2
        self.tree[parent] += change
        if parent != 0:
            self._propagate(parent, change)

    def _retrieve(self, idx, s):
        left = 2 * idx + 1
        right = left + 1
        if left >= len(self.tree):
            return idx
        if s <= self.tree[left]:
            return self._retrieve(left, s)
        else:
            return self._retrieve(right, s - self.tree[left])

    def total(self):
        return self.tree[0]

    def add(self, priority, data):
        idx = self.write + self.capacity - 1
        self.data[self.write] = data
        self.update(idx, priority)
        self.write = (self.write + 1) % self.capacity
        self.n_entries = min(self.n_entries + 1, self.capacity)

    def update(self, idx, priority):
        change = priority - self.tree[idx]
        self.tree[idx] = priority
        self._propagate(idx, change)

    def get(self, s):
        idx = self._retrieve(0, s)
        data_idx = idx - self.capacity + 1
        return idx, self.tree[idx], self.data[data_idx]


class PrioritizedReplayBuffer:
    """PER with TD-error prioritization.

    Directly addresses the alive-signal underweighting: failure transitions
    produce large TD errors (reward = -1.0 is unexpected for a policy that
    hasn't learned failures) and so are automatically oversampled.
    """

    def __init__(self, capacity, alpha=0.6, beta_start=0.4,
                 beta_increment=0.001, epsilon=0.01):
        self.tree = SumTree(capacity)
        self.capacity = capacity
        self.alpha = alpha          # Priority exponent
        self.beta = beta_start      # IS weight exponent (annealed to 1.0)
        self.beta_increment = beta_increment
        self.epsilon = epsilon      # Small constant to avoid zero priority

    def add(self, transition, td_error=None):
        if td_error is None:
            # New transitions get max priority so they are sampled at least once
            max_p = self.tree.total() / max(1, self.tree.n_entries)
            priority = max(max_p, 1.0)
        else:
            priority = (abs(td_error) + self.epsilon) ** self.alpha
        self.tree.add(priority, transition)

    def sample(self, batch_size):
        batch = []
        idxs = []
        priorities = []
        segment = self.tree.total() / batch_size

        self.beta = min(1.0, self.beta + self.beta_increment)

        for i in range(batch_size):
            lo = segment * i
            hi = segment * (i + 1)
            s = np.random.uniform(lo, hi)
            idx, priority, data = self.tree.get(s)
            if data is None:
                # Fallback: re-sample from the first segment
                s = np.random.uniform(0, segment)
                idx, priority, data = self.tree.get(s)
            batch.append(data)
            idxs.append(idx)
            priorities.append(priority)

        # Importance sampling weights
        probs = np.array(priorities) / max(self.tree.total(), 1e-8)
        weights = (self.tree.n_entries * probs + 1e-8) ** (-self.beta)
        weights /= weights.max()

        return batch, idxs, torch.FloatTensor(weights)

    def update_priorities(self, idxs, td_errors):
        for idx, td_error in zip(idxs, td_errors):
            priority = (abs(td_error) + self.epsilon) ** self.alpha
            self.tree.update(idx, priority)

    def __len__(self):
        return self.tree.n_entries

    def clear(self):
        """Wipe the buffer (e.g. on catastrophic topology reset)."""
        self.tree = SumTree(self.capacity)


# ========================================================================
# DQN Agent
# ========================================================================

class DQNAgent:
    def __init__(self, config):
        self.config = config
        self.state_dim = config['drl']['state_dim']
        self.action_dim = config['drl']['action_dim']
        self.hidden_dim = config['drl']['hidden_dim']
        
        # Q-Network
        self.q_net = nn.Sequential(
            nn.Linear(self.state_dim, self.hidden_dim),
            nn.ReLU(),
            nn.Linear(self.hidden_dim, self.action_dim)
        )
        
        # Target Network
        self.target_net = nn.Sequential(
            nn.Linear(self.state_dim, self.hidden_dim),
            nn.ReLU(),
            nn.Linear(self.hidden_dim, self.action_dim)
        )
        self.target_net.load_state_dict(self.q_net.state_dict())
        
        # Optimizer - FIXED: use 'learning_rate' from config
        lr = config['drl'].get('learning_rate', 0.001)
        self.optimizer = optim.Adam(self.q_net.parameters(), lr=lr)
        
        # Replay Memory — uniform deque or PER depending on config
        memory_size = config['training'].get('memory_size', 10000)
        per_cfg = config.get('training', {}).get('per', {})
        self.use_per = per_cfg.get('enabled', False)

        if self.use_per:
            self.memory = PrioritizedReplayBuffer(
                capacity=memory_size,
                alpha=per_cfg.get('alpha', 0.6),
                beta_start=per_cfg.get('beta_start', 0.4),
                beta_increment=per_cfg.get('beta_increment', 0.001),
            )
            print("[DQNAgent] Using Prioritized Experience Replay (PER)")
        else:
            self.memory = deque(maxlen=memory_size)
            print("[DQNAgent] Using uniform replay buffer")
        
        # Epsilon for exploration
        self.epsilon = config['drl']['epsilon_start']
        self.epsilon_min = config['drl']['epsilon_min']
        self.epsilon_decay = config['drl']['epsilon_decay']

    def act(self, state, epsilon=None):
        """
        Select action using epsilon-greedy policy
        
        Args:
            state: numpy array of shape (state_dim,)
            epsilon: exploration rate (if None, use self.epsilon)
        
        Returns:
            action: integer action index
        """
        if epsilon is None:
            epsilon = self.epsilon
            
        if np.random.random() < epsilon:
            return np.random.randint(self.action_dim), None
        
        # Convert state to tensor
        state = torch.FloatTensor(state).unsqueeze(0)  # Add batch dimension
        
        with torch.no_grad():
            q_values = self.q_net(state)
            action = q_values.argmax(dim=1).item()
            return action, q_values.squeeze().cpu().numpy()

    def remember(self, state, action, reward, next_state, done):
        """Store transition in replay memory."""
        transition = (state, action, reward, next_state, done)
        if self.use_per:
            self.memory.add(transition)
        else:
            self.memory.append(transition)

    def train(self):
        """Train the Q-network using a batch from replay memory."""
        batch_size = self.config['training']['batch_size']
        
        if len(self.memory) < batch_size:
            return None  # Not enough samples yet
        
        # Sample batch — uniform or prioritized
        if self.use_per:
            batch, tree_idxs, is_weights = self.memory.sample(batch_size)
        else:
            batch = random.sample(self.memory, batch_size)
            tree_idxs = None
            is_weights = torch.ones(batch_size)

        states, actions, rewards, next_states, dones = zip(*batch)
        
        # Convert to tensors
        states = torch.FloatTensor(np.array(states))
        actions = torch.LongTensor(actions).unsqueeze(1)
        rewards = torch.FloatTensor(rewards)
        next_states = torch.FloatTensor(np.array(next_states))
        dones = torch.FloatTensor(dones)
        
        # Current Q values
        current_q = self.q_net(states).gather(1, actions).squeeze()
        
        # Target Q values using target network
        with torch.no_grad():
            next_q = self.target_net(next_states).max(1)[0]
            # If done, target is just reward. Otherwise reward + gamma * next_q
            targets = rewards + (1 - dones) * self.config['training']['gamma'] * next_q
        
        # Compute element-wise TD errors (for PER priority update)
        td_errors = (current_q - targets).detach()

        # Compute loss (Huber loss for stability), weighted by IS weights for PER
        element_loss = nn.SmoothL1Loss(reduction='none')(current_q, targets)
        loss = (is_weights * element_loss).mean()
        
        # Optimize
        self.optimizer.zero_grad()
        loss.backward()
        
        # Debug: Gradient norm
        total_norm = 0.0
        for p in self.q_net.parameters():
            if p.grad is not None:
                param_norm = p.grad.data.norm(2)
                total_norm += param_norm.item() ** 2
        total_norm = total_norm ** 0.5
        
        # Clip gradients (increased to 10.0)
        torch.nn.utils.clip_grad_norm_(self.q_net.parameters(), 10.0)
        self.optimizer.step()

        # PER: update priorities with new TD errors
        if self.use_per and tree_idxs is not None:
            self.memory.update_priorities(
                tree_idxs, td_errors.abs().cpu().numpy()
            )
        
        # Decay epsilon
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)
        
        # Store for debug
        self.last_q_values = current_q.detach().mean().item()
        self.last_grad_norm = total_norm
        
        return loss.item()

    def update_target(self):
        """Update target network with Q-network weights"""
        self.target_net.load_state_dict(self.q_net.state_dict())

    def save_model(self, path):
        """Save model checkpoint"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        torch.save({
            'q_net': self.q_net.state_dict(),
            'target_net': self.target_net.state_dict(),
            'optimizer': self.optimizer.state_dict(),
            'epsilon': self.epsilon,
            'memory_size': len(self.memory)
        }, path)
        print(f"[INFO] Model saved to {path}")

    def load_model(self, path):
        """Load model checkpoint"""
        if os.path.isfile(path):
            checkpoint = torch.load(path)
            self.q_net.load_state_dict(checkpoint['q_net'])
            self.target_net.load_state_dict(checkpoint['target_net'])
            self.optimizer.load_state_dict(checkpoint['optimizer'])
            self.epsilon = checkpoint.get('epsilon', self.epsilon_min)
            print(f"[INFO] Model loaded from {path}")
            return True
        else:
            print(f"[WARN] Model file {path} not found")
            return False