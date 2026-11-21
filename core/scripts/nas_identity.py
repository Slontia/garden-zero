"""Strict PROXY v2 parsing for a trusted loopback Serve upstream; no write authorization."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import datetime,ipaddress,struct
SIGNATURE=b'\r\n\r\n\x00\r\nQUIT\n'
def address_header(header,payload):
 if len(header)!=16 or header[:12]!=SIGNATURE or header[12]!=0x21:raise ValueError('Expected PROXY v2 PROXY command')
 size=int.from_bytes(header[14:16],'big')
 if size!=len(payload) or size>512:raise ValueError('Invalid bounded PROXY payload length')
 family=header[13]
 width=4 if family==0x11 else 16 if family==0x21 else 0
 needed=2*width+4
 if not width or len(payload)<needed:raise ValueError('Only TCP over IPv4/IPv6 is accepted')
 source=ipaddress.ip_address(payload[:width]);destination=ipaddress.ip_address(payload[width:2*width]);sport,dport=struct.unpack('!HH',payload[2*width:needed])
 def tailnet(ip):return ip in ipaddress.ip_network('100.64.0.0/10') if ip.version==4 else ip in ipaddress.ip_network('fd7a:115c:a1e0::/48')
 if not tailnet(source) or not tailnet(destination) or not sport or not dport:raise ValueError('Non-tailnet address or invalid port')
 # Reject malformed TLVs rather than allowing a second ambiguous address block.
 cursor=needed
 while cursor<len(payload):
  if len(payload)-cursor<3:raise ValueError('Truncated PROXY TLV')
  length=int.from_bytes(payload[cursor+1:cursor+3],'big');cursor+=3+length
  if cursor>len(payload):raise ValueError('Truncated PROXY TLV value')
 return {'source':str(source),'destination':str(destination),'source_port':sport,'destination_port':dport}

def node_identity(source,whois,peer=None):
 node=whois.get('Node',{});identity=node.get('StableID')
 if not isinstance(identity,str) or not identity:raise ValueError('Node has no stable identity')
 if peer is None:
  if node.get('MachineAuthorized') is not True:raise ValueError('Node is not authorized')
 else:
  # Remote WhoIs omits MachineAuthorized. Require the current local netmap entry,
  # not a caller-supplied node/name or a remembered IP association.
  if peer.get('ID')!=identity or peer.get('InNetworkMap') is not True or peer.get('Online') is not True or peer.get('Expired') or source not in peer.get('TailscaleIPs',[]):raise ValueError('Peer is not live in the current network map')
 for value in (node.get('KeyExpiry'),peer.get('KeyExpiry') if peer else None):
  if value and not value.startswith('0001-'):
   if datetime.datetime.fromisoformat(value.replace('Z','+00:00'))<=datetime.datetime.now(datetime.timezone.utc):raise ValueError('Node key expired')
 addresses=[ipaddress.ip_interface(a).ip for a in node.get('Addresses',[])]
 if ipaddress.ip_address(source) not in addresses:raise ValueError('Source does not match node-owned address')
 return identity
