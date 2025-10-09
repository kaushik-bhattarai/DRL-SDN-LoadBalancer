from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.cli import CLI

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

        # Connect core ↔ aggs (simplified: each core connects to one agg per pod)
        for i, c in enumerate(cores):
            for j in range(4):
                self.addLink(c, aggs[j + (i*1) % 4], bw=100, delay='1ms')

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
                host = self.addHost(f'h{host_id}', cpu=.1)
                self.addLink(e, host, bw=100, delay='1ms')
                host_id += 1

def start_network():
    topo = FatTree4()
    net = Mininet(topo=topo, controller=RemoteController, link=TCLink)
    net.start()
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
                        
    print("Mininet started. Use CLI commands below:")
    CLI(net)          # <- This opens the interactive Mininet CLI
    net.stop()        # <- Network stops when you exit the CLI
    

# s_core1 → 0001
# s_core2 → 0002
# s_core3 → 0003
# s_core4 → 0004

# s_agg0_0 → 0064 (100)
# s_agg0_1 → 0065 (101)
# s_agg1_0 → 0066 (102)
# s_agg1_1 → 0067 (103)
# s_agg2_0 → 0068 (104)
# s_agg2_1 → 0069 (105)
# s_agg3_0 → 006a (106)
# s_agg3_1 → 006b (107)

# s_edge0_0 → 00c8 (200)
# s_edge0_1 → 00c9 (201)
# s_edge1_0 → 00ca (202)
# s_edge1_1 → 00cb (203)
# s_edge2_0 → 00cc (204)
# s_edge2_1 → 00cd (205)
# s_edge3_0 → 00ce (206)
# s_edge3_1 → 00cf (207)

# ✅ That matches exactly with our intended numbering scheme (cores 1–4, aggs 100–107, edges 200–207).