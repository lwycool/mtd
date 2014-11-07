import random
from bitarray import bitarray

from pox.core import core
from pox.lib.revent import *
import pox.openflow.libopenflow_01 as of

from pox.lib.addresses import IPAddr, EthAddr

log = core.getLogger()

class MTDIPPrefixes(object):
    def __init__(self, prefixes):
        self.prefixes = map(MTDIPPrefix, prefixes)
        self.total_masklen = reduce(
                lambda acc, x: acc+x.masklen, self.prefixes, 0)

    def rand_ip_addr(self):
        prefix = self.rand_ip_prefix()
        return prefix.rand_ip_addr()

    def rand_ip_prefix(self):
        draw = random.randint(1, self.total_masklen)
        for p in self.prefixes:
            draw -= p.masklen
            if draw <= 0:
                return p

class MTDIPPrefix(object):
    def __init__(self, pattern):
        parts = pattern.split("/")
        if len(parts) != 2:
            raise TypeError
        
        self.masklen = int(parts[1])
        self.pattern = IPAddr(parts[0])

        bits = bitarray()
        bits.frombytes(self.pattern.toRaw())
        self.prefix = bits[:self.masklen]

    def rand_ip_addr(self):
        rbitslen = 32 - self.masklen
        rbits = bin(random.getrandbits(rbitslen))[2:]
        return IPAddr((self.prefix + rbits.zfill(rbitslen)).tobytes())

    def __repr__(self):
        return "%s/%d" % (repr(self.pattern), self.masklen)

class MTDController(EventMixin):
    """TODO:
    *) A timeout mechanism(Threading.Timer?) to trigger flushing
    *) When we trigger a reassignment, maybe we can use 
       ofp_stats_request to find out a suspect and drop him/her out

    *) a test script (similar as submit.py) to automatic testing
    """
    def __init__(self, fixed, hosts, networks):
        super(MTDController, self).__init__()

        self.mapping = {}
        self.fixed = fixed
        self.hosts = hosts
        self.prefixes = MTDIPPrefixes(networks)
        
        self.flush_assignments()

        self.listenTo(core.openflow)
        log.info("Enabling MTD Module...")
    
    def flush_assignments(self):
        used_ipaddrs = set(self.mapping.keys())

        def next_ip_addr():
            ip_addr = self.prefixes.rand_ip_addr()
            while ip_addr in used_ipaddrs:
               ip_addr = self.prefixed.rand_ip_addr()
            return ip_addr

        next_mapping = {}
        for host in self.hosts:
            new_ip_addr = next_ip_addr()
            next_mapping[new_ip_addr] = host
            used_ipaddrs.add(new_ip_addr)

        self.mapping = next_mapping

        print "Current mapping: ", self.mapping

    def _handle_PacketIn(self, event):
        packet = event.parsed
        ip = packet.find('ipv4')

        def flood():
            msg = of.ofp_packet_out()
            msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
            msg.data = event.ofp
            msg.in_port = event.port
            event.connection.send(msg)

        def drop(duration=(10, 10)):
            if not isinstance(duration, tuple):
                duration = (duration, duration)
            msg = of.ofp_flow_mod()
            msg.match = of.ofp_match.from_packet(packet)
            msg.idle_timeout = duration[0]
            msg.hard_timeout = duration[1]
            msg.buffer_id = event.ofp.buffer_id
            event.connection.send(msg)

        def fwd(target, duration=(10, 10)):
            if not isinstance(duration, tuple):
                duration = (duration, duration)
            # srcip -> dstip
            msg = of.ofp_flow_mod()
            msg.match = of.ofp_match.from_packet(packet)
            msg.idle_timeout = duration[0]
            msg.hard_timeout = duration[1]
            msg.actions.append(of.ofp_action_nw_addr.set_dst(target))
            msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
            event.connection.send(msg)

            # target -> srcip
            msg = of.ofp_flow_mod()
            msg.match = of.ofp_match(dl_type=0x800, nw_src=target, nw_dst=ip.srcip)
            msg.idle_timeout = duration[0]
            msg.hard_timeout = duration[1]
            msg.actions.append(of.ofp_action_nw_addr.set_src(ip.dstip))
            msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
            event.connection.send(msg)

            # send this pkt
            msg = of.ofp_packet_out()
            msg.actions.append(of.ofp_action_nw_addr.set_dst(target))
            msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
            msg.data = event.ofp
            msg.in_port = event.port
            event.connection.send(msg)

        if ip is None or ip.dstip in self.fixed:
            return flood()
        
        if ip.dstip in self.mapping:
            target = self.mapping[ip.dstip]
            print "Making a connection between %s and %s(%s)" \
                    % (ip.srcip, ip.dstip, target)
            fwd(target)
        else:
            drop()

def launch():
    fixed = [IPAddr('172.0.0.1'), IPAddr('172.0.0.11')]
    hosts = [IPAddr('100.0.0.7')]
    networks = ['140.0.0.0/16',
                '150.0.0.0/8',
                '160.0.0.0/8',
                '170.0.0.0/16']

    core.registerNew(MTDController, fixed, hosts, networks)
