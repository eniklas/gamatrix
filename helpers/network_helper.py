import logging

from flask import abort
from ipaddress import IPv4Address


log = logging.getLogger(__name__)


def _ip_allowed(ip, networks):
    """Returns True if ip is in any of the networks, False otherwise"""
    # If no CIDRs are defined, all IPs are allowed
    ip_allowed = not networks

    ip = IPv4Address(ip)

    for network in networks:
        if ip in network:
            ip_allowed = True
            break

    return ip_allowed


def check_ip_is_authorized(ip, networks):
    if not _ip_allowed(ip, networks):
        log.info(f"Rejecting request from unauthorized IP {ip}")
        abort(401)

    log.info(f"Accepted request from {ip}")
