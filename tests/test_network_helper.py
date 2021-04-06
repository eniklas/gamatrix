from ..helpers.network_helper import _ip_allowed
from ipaddress import IPv4Network


def test_ip_allowed():
    # Test no networks allows access
    networks = []
    ip = "1.2.3.4"

    assert _ip_allowed(ip, networks)

    # Test IP in allowed CIDRs
    networks = [
        IPv4Network("192.168.0.0/24"),
        IPv4Network("192.168.1.0/24"),
    ]
    ip = "192.168.1.100"

    assert _ip_allowed(ip, networks)

    # Test IP not in allowed CIDRs
    ip = "127.0.0.1"
    assert not _ip_allowed(ip, networks)
