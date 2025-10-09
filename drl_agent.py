import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import random
from collections import deque
import os

class DQNAgent:
    def __init__(self, config):
        self.q_net = nn.Sequential(
            nn.Linear(config['drl']['state_dim'], config['drl']['hidden_dim']),
            nn.ReLU(),
            nn.Linear(config['drl']['hidden_dim'], config['drl']['action_dim'])
        )
        self.target_net = nn.Sequential(
            nn.Linear(config['drl']['state_dim'], config['drl']['hidden_dim']),
            nn.ReLU(),
            nn.Linear(config['drl']['hidden_dim'], config['drl']['action_dim'])
        )
        self.target_net.load_state_dict(self.q_net.state_dict())
        self.optimizer = optim.Adam(self.q_net.parameters(), lr=config['drl']['lr'])
        self.memory = deque(maxlen=10000)
        self.config = config

    def act(self, state, epsilon):
        if np.random.random() < epsilon:
            return np.random.randint(self.config['drl']['action_dim'])
        state = torch.FloatTensor(state).unsqueeze(0)  # Add batch dimension
        with torch.no_grad():
            return self.q_net(state).argmax(dim=1).item()

    def remember(self, state, action, reward, next_state):
        self.memory.append((state, action, reward, next_state))

    def train(self):
        if len(self.memory) < self.config['training']['batch_size']:
            return
        
        batch = random.sample(self.memory, self.config['training']['batch_size'])
        states, actions, rewards, next_states = zip(*batch)
        
        states = torch.FloatTensor(states)
        actions = torch.LongTensor(actions).unsqueeze(1)
        rewards = torch.FloatTensor(rewards)
        next_states = torch.FloatTensor(next_states)
        
        current_q = self.q_net(states).gather(1, actions).squeeze()
        next_q = self.target_net(next_states).max(1)[0].detach()
        targets = rewards + self.config['training']['gamma'] * next_q
        
        loss = nn.MSELoss()(current_q, targets)
        
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

    def update_target(self):
        self.target_net.load_state_dict(self.q_net.state_dict())

    def save_model(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        torch.save(self.q_net.state_dict(), path)
        print(f"[INFO] Model saved to {path}")

    def load_model(self, path):
        if os.path.isfile(path):
            self.q_net.load_state_dict(torch.load(path))
            self.update_target()
            print(f"[INFO] Model loaded from {path}")
        else:
            print(f"[WARN] Model file {path} not found")
