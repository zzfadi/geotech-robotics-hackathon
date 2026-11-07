"""Choose the local IPv4 route without transmitting a packet to the host."""
import ipaddress
import os
import socket
import sys


def local_address(host, override=None):
    if override:
        return str(ipaddress.IPv4Address(override))
    # UDP connect selects the route but sends nothing. Unlike ipconfig,
    # getsockname works for macOS utun interfaces used by Tailscale.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((host, 9030))
        address = probe.getsockname()[0]
    if address == '0.0.0.0':
        raise RuntimeError('No local IPv4 route to the Multi-User host')
    return str(ipaddress.IPv4Address(address))


if __name__ == '__main__':
    print(local_address(sys.argv[1], os.environ.get('COPRESENCE_LOCAL_ADDRESS')))
