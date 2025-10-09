# controller.py
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.app.wsgi import WSGIApplication, ControllerBase, route

import json
from webob import Response
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet, arp


BASE_URL = '/sdrlb'


class SDNRest(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        wsgi = kwargs['wsgi']

        self._datapaths = {}      # dpid -> datapath
        self.port_stats = {}      # (dpid, port_no) -> tx_bytes
        self.flow_stats = {}      # (dpid, match_json) -> stats
        self.port_desc = {}       # dpid -> [port_numbers]
        self.host_ports = {}      # dpid -> {host_mac_or_ip: port_no}
        # self.host_ports = {

        #     200: {'10.0.0.1': 3, '10.0.0.2': 4},
        #     201: {'10.0.0.3': 3, '10.0.0.4': 4},
        #     202: {'10.0.0.5': 3, '10.0.0.6': 4},
        #     203: {'10.0.0.7': 3, '10.0.0.8': 4},
        #     204: {'10.0.0.9': 3, '10.0.0.10': 4},
        #     205: {'10.0.0.11': 3, '10.0.0.12': 4},
        #     206: {'10.0.0.13': 3, '10.0.0.14': 4},
        #     207: {'10.0.0.15': 3, '10.0.0.16': 4},
        # }


        self.monitor_thread = hub.spawn(self._monitor)
        wsgi.register(RestController, {'sdn_app': self})

    # ------------------ Monitoring ------------------
    def _monitor(self):
        while True:
            for dp in list(self._datapaths.values()):
                self._request_stats(dp)
            hub.sleep(5)

    def _request_stats(self, datapath):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        datapath.send_msg(parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY))
        datapath.send_msg(parser.OFPFlowStatsRequest(datapath))
        try:
            datapath.send_msg(parser.OFPPortDescStatsRequest(datapath, 0))
        except Exception:
            pass

    # ------------------ Events ------------------
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, CONFIG_DISPATCHER])
    def _state_change_handler(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self._datapaths[dp.id] = dp
            self.logger.info(f"Datapath {dp.id} connected")
        else:
            self._datapaths.pop(dp.id, None)
            self.logger.info(f"Datapath {dp.id} disconnected")

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_handler(self, ev):
        dpid = ev.msg.datapath.id
        for stat in ev.msg.body:
            self.port_stats[(dpid, stat.port_no)] = stat.tx_bytes

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_handler(self, ev):
        dpid = ev.msg.datapath.id
        for stat in ev.msg.body:
            match = {}
            if hasattr(stat, 'match'):
                try:
                    for k, v in stat.match.items():
                        match[str(k)] = str(v)
                except Exception:
                    match = str(stat.match)
            self.flow_stats[(dpid, json.dumps(match))] = {
                'packet_count': stat.packet_count,
                'byte_count': stat.byte_count
            }

    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
    def port_desc_handler(self, ev):
        dpid = ev.msg.datapath.id
        ports = []
        for p in ev.msg.body:
            if p.port_no != ev.msg.datapath.ofproto.OFPP_LOCAL:
                ports.append(p.port_no)
        self.port_desc[dpid] = sorted(ports)

    # ------------------ Host learning ------------------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        dpid = datapath.id
        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        self.logger.info(msg)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return

        src_mac = eth.src

        if dpid not in self.host_ports:
            self.host_ports[dpid] = {}

        # Learn host by MAC
        self.host_ports[dpid][src_mac] = in_port

        # If ARP packet, also learn host by IP
        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt:
            self.host_ports[dpid][arp_pkt.src_ip] = in_port


# ------------------ REST Controller ------------------
class RestController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data)
        self.sdn_app = data['sdn_app']

    @route('sdn', BASE_URL + '/stats/port/{dpid}', methods=['GET'])
    def get_port_stats(self, req, **kwargs):
        dpid = int(kwargs['dpid'])
        result = {port: tx for (dp, port), tx in self.sdn_app.port_stats.items() if dp == dpid}
        return Response(status=200, content_type='application/json', body=json.dumps(result))

    @route('sdn', BASE_URL + '/stats/flow/{dpid}', methods=['GET'])
    def get_flow_stats(self, req, **kwargs):
        dpid = int(kwargs['dpid'])
        result = {match_json: stats for (dp, match_json), stats in self.sdn_app.flow_stats.items() if dp == dpid}
        return Response(status=200, content_type='application/json', body=json.dumps(result))

    @route('sdn', BASE_URL + '/ports/{dpid}', methods=['GET'])
    def get_ports(self, req, **kwargs):
        dpid = int(kwargs['dpid'])
        ports = self.sdn_app.port_desc.get(dpid, [])
        return Response(status=200, content_type='application/json', body=json.dumps({'ports': ports}))

    @route('sdn', BASE_URL + '/host_ports/{dpid}', methods=['GET'])
    def get_host_ports(self, req, **kwargs):
        dpid = int(kwargs['dpid'])
        host_ports = self.sdn_app.host_ports.get(dpid, {})
        return Response(status=200, content_type='application/json', body=json.dumps(host_ports).encode('utf-8'))

    @route('sdn', BASE_URL + '/stats/flowentry/add', methods=['POST'])
    def add_flow(self, req, **kwargs):
        try:
            data = json.loads(req.body)
            dpid = int(data['dpid'])
            datapath = self.sdn_app._datapaths.get(dpid)
            if datapath is None:
                return Response(status=404, body=json.dumps({'error': 'datapath not found'}))

            parser = datapath.ofproto_parser
            ofproto = datapath.ofproto
            match = parser.OFPMatch(**data.get('match', {}))
            actions = [parser.OFPActionOutput(int(a['port'])) for a in data.get('actions', []) if a.get('type') == 'OUTPUT']
            inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
            mod = parser.OFPFlowMod(
                datapath=datapath,
                match=match,
                instructions=inst,
                priority=int(data.get('priority', 1000)),
                idle_timeout=int(data.get('idle_timeout', 0)),
                hard_timeout=int(data.get('hard_timeout', 0))
            )
            datapath.send_msg(mod)
            return Response(status=200, body=json.dumps({'result': 'flow added'}))
        except Exception as e:
            return Response(status=500, body=json.dumps({'error': str(e)}))

    @route('sdn', BASE_URL + '/stats/flowentry/clear', methods=['POST'])
    def clear_flows(self, req, **kwargs):
        try:
            data = json.loads(req.body)
            dpid = int(data['dpid'])
            datapath = self.sdn_app._datapaths.get(dpid)
            if datapath is None:
                return Response(status=404, body=json.dumps({'error': 'datapath not found'}))
            parser = datapath.ofproto_parser
            ofproto = datapath.ofproto
            mod = parser.OFPFlowMod(datapath=datapath, command=ofproto.OFPFC_DELETE)
            datapath.send_msg(mod)
            return Response(status=200, body=json.dumps({'result': 'flows cleared'}))
        except Exception as e:
            return Response(status=500, body=json.dumps({'error': str(e)}))
