from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.cli import CLI
from functools import partial

class FatTree4(Topo):
    def build(self, k=4):
        # Core switches: k^2/4 = 4
        cores = []
        for i in range(4):
            dpid = f"{i+1:016x}"  # 0000...0001, 0000...0002, etc.
            cores.append(self.addSwitch(f's_core{i+1}', dpid=dpid))

        # Pods: k = 4 → 4 pods, each pod has 2 agg, 2 edge
        aggs, edges = [], []
        agg_base = 100  # start DPID numbering for agg
        edge_base = 200  # start DPID numbering for edge

        for pod in range(4):
            for a in range(2):
                dpid = f"{agg_base + pod*2 + a:016x}"
                aggs.append(self.addSwitch(f's_agg{pod}_{a}', dpid=dpid))

            for e in range(2):
                dpid = f"{edge_base + pod*2 + e:016x}"
                edges.append(self.addSwitch(f's_edge{pod}_{e}', dpid=dpid))

        # Connect core ↔ aggs (Standard Fat-Tree k=4)
        # Cores 0,1 connect to Agg 0 in all pods
        # Cores 2,3 connect to Agg 1 in all pods
        for pod in range(4):
            agg0 = aggs[pod * 2]      # First agg in pod
            agg1 = aggs[pod * 2 + 1]  # Second agg in pod
            
            # Connect Agg0 to Core 0 and Core 1
            self.addLink(agg0, cores[0], bw=100, delay='1ms')
            self.addLink(agg0, cores[1], bw=100, delay='1ms')
            
            # Connect Agg1 to Core 2 and Core 3
            self.addLink(agg1, cores[2], bw=100, delay='1ms')
            self.addLink(agg1, cores[3], bw=100, delay='1ms')

        # Connect agg ↔ edge in same pod
        for pod in range(4):
            pod_aggs = [s for s in aggs if s.startswith(f's_agg{pod}_')]
            pod_edges = [s for s in edges if s.startswith(f's_edge{pod}_')]
            for a in pod_aggs:
                for e in pod_edges:
                    self.addLink(a, e, bw=100, delay='1ms')

        # Attach 2 hosts per edge switch → total 16 hosts
        host_id = 1
        for e in edges:
            for h in range(2):
                # Assign IP explicitly
                ip_addr = f'10.0.0.{host_id}/24'
                mac_addr = f'00:00:00:00:00:{host_id:02x}'
                
                host = self.addHost(
                    f'h{host_id}', 
                    cpu=.1,
                    ip=ip_addr,
                    mac=mac_addr
                    # NO defaultRoute! Hosts are on same subnet
                )
                self.addLink(e, host, bw=100, delay='1ms')
                host_id += 1

def start_network():
    """Start Mininet network with remote Ryu controller"""
    topo = FatTree4()
    
    # Create remote controller
    c0 = RemoteController('c0', ip='127.0.0.1', port=6633)
    
    net = Mininet(
        topo=topo, 
        controller=c0,
        switch=partial(OVSSwitch, protocols='OpenFlow13'),
        link=TCLink,
        autoSetMacs=True
    )
    
    net.start()
    
    # FORCE OpenFlow 1.3 on all switches
    print("⚡ Forcing OpenFlow 1.3 on all switches...")
    for sw in net.switches:
        sw.cmd(f'ovs-vsctl set bridge {sw.name} protocols=OpenFlow13')
        # Set controller explicitly for each switch to be safe
        sw.cmd(f'ovs-vsctl set-controller {sw.name} tcp:127.0.0.1:6633')
    
    print("\n" + "="*60)
    print("✅ Mininet network started with Ryu controller")
    print(f"   Switches: {len(net.switches)}")
    print("   Controller: 127.0.0.1:6633")
    print("="*60 + "\n")
    
    return net

if __name__ == '__main__':
    setLogLevel('info')
    net = start_network()
    
    # Print all edge switches and connected host ports
    for sw in net.switches:
        if 'edge' in sw.name:
            print(f"\n{sw.name} connections:")
            for h in net.hosts:
                conns = h.connectionsTo(sw)
                if conns:  # host connected to this switch
                    for (host_intf, sw_intf) in conns:
                        print(f"{h.name} -> {sw_intf.name}")
                        
    print("\nMininet started. Use CLI commands below:")
    CLI(net)          # <- This opens the interactive Mininet CLI
    net.stop()        # <- Network stops when you exit the CLI