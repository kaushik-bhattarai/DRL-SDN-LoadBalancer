import json
import argparse
import matplotlib.pyplot as plt
import os

def visualize_metrics(json_path, output_path=None):
    if not os.path.exists(json_path):
        print(f"Error: File '{json_path}' not found.")
        return

    with open(json_path, 'r') as f:
        data = json.load(f)
    
    history = data.get('history', [])
    if not history:
        print("No history data found in the JSON file.")
        return

    t_sec = [entry['t_sec'] for entry in history]
    
    # 1. Total Requests
    total_requests = [entry['total_requests'] for entry in history]
    
    # 2. Server Selections (Cumulative)
    servers = set()
    for entry in history:
        servers.update(entry.get('server_selections', {}).keys())
    servers = sorted(list(servers))
    
    server_selections = {srv: [] for srv in servers}
    for entry in history:
        for srv in servers:
            server_selections[srv].append(entry.get('server_selections', {}).get(srv, 0))
            
    # 3. Latency
    latency_mean = [entry.get('latency_mean_ms', 0) for entry in history]
    latency_p95 = [entry.get('latency_p95_ms', 0) for entry in history]
    
    # 4. Fairness
    fairness = [entry.get('fairness_connections', 1.0) for entry in history]
    
    # 5. Active Connections
    conn_keys = set()
    for entry in history:
        for k in entry.keys():
            if k.startswith('conns_'):
                conn_keys.add(k)
    conn_keys = sorted(list(conn_keys))
    
    conns_data = {k: [] for k in conn_keys}
    for entry in history:
        for k in conn_keys:
            conns_data[k].append(entry.get(k, 0))

    # Plotting
    fig, axs = plt.subplots(3, 2, figsize=(15, 12))
    fig.suptitle(f"Inference Metrics Visualization\n{os.path.basename(json_path)}", fontsize=16)

    # Plot Total Requests
    axs[0, 0].plot(t_sec, total_requests, label='Total Requests', color='black')
    axs[0, 0].set_title('Total Requests Over Time')
    axs[0, 0].set_xlabel('Time (sec)')
    axs[0, 0].set_ylabel('Requests')
    axs[0, 0].grid(True)
    axs[0, 0].legend()

    # Plot Server Selections
    for srv in servers:
        axs[0, 1].plot(t_sec, server_selections[srv], label=f'Server {srv}')
    axs[0, 1].set_title('Cumulative Server Selections')
    axs[0, 1].set_xlabel('Time (sec)')
    axs[0, 1].set_ylabel('Requests routed')
    axs[0, 1].grid(True)
    axs[0, 1].legend()

    # Plot Latency
    axs[1, 0].plot(t_sec, latency_mean, label='Mean Latency', color='blue')
    axs[1, 0].plot(t_sec, latency_p95, label='P95 Latency', color='red', linestyle='dashed')
    axs[1, 0].set_title('Latency Over Time')
    axs[1, 0].set_xlabel('Time (sec)')
    axs[1, 0].set_ylabel('Latency (ms)')
    axs[1, 0].grid(True)
    axs[1, 0].legend()

    # Plot Fairness
    axs[1, 1].plot(t_sec, fairness, label='Connections Fairness', color='green')
    axs[1, 1].set_title('Jain\'s Fairness Index Over Time')
    axs[1, 1].set_xlabel('Time (sec)')
    axs[1, 1].set_ylabel('Fairness (0 to 1)')
    axs[1, 1].set_ylim(0, 1.05)
    axs[1, 1].grid(True)
    axs[1, 1].legend()

    # Plot Active Connections
    for k in conn_keys:
        axs[2, 0].plot(t_sec, conns_data[k], label=k.replace('conns_', ''))
    axs[2, 0].set_title('Active Connections per Host')
    axs[2, 0].set_xlabel('Time (sec)')
    axs[2, 0].set_ylabel('Connections')
    axs[2, 0].grid(True)
    axs[2, 0].legend()

    # Summary text
    axs[2, 1].axis('off')
    summary_text = (
        f"Final Metrics Summary:\n\n"
        f"Total Requests: {data.get('total_requests', 0)}\n"
        f"Avg Latency: {data.get('latency_avg_ms', 0):.2f} ms\n"
        f"P95 Latency: {data.get('latency_p95_ms', 0):.2f} ms\n"
        f"Stabilization time: {data.get('stabilization_sec', 0)} sec\n"
        f"Final Fairness: {data.get('fairness_final', 0):.4f}\n"
        f"Duration: {data.get('duration_sec', 0)} sec\n\n"
        f"Server Selections Final:\n"
    )
    for srv, count in data.get('server_selections_final', {}).items():
        summary_text += f"  - {srv}: {count}\n"
        
    axs[2, 1].text(0.1, 0.5, summary_text, fontsize=12, verticalalignment='center',
                   bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    
    if output_path:
        plt.savefig(output_path)
        print(f"Plot saved to '{output_path}'")
    else:
        print("Displaying plot window...")
        plt.show()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Visualize Inference Metrics")
    parser.add_argument("json_path", help="Path to the inference evaluation JSON file")
    parser.add_argument("--save", "-s", help="Path to save the generated plot image (e.g., plot.png)", default=None)
    args = parser.parse_args()

    visualize_metrics(args.json_path, args.save)
