"""Network utility helpers independent of messaging gateway adapters."""

from __future__ import annotations

import ipaddress
import socket


def is_network_accessible(host: str) -> bool:
    """Return True when *host* appears reachable from outside localhost."""
    raw = str(host or "").strip()
    if not raw:
        return False
    if raw in {"0.0.0.0", "::"}:
        return True
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        try:
            infos = socket.getaddrinfo(raw, None)
        except OSError:
            return False
        for info in infos:
            try:
                ip = ipaddress.ip_address(info[4][0])
            except ValueError:
                continue
            if not (ip.is_loopback or ip.is_link_local):
                return True
        return False
    return not (ip.is_loopback or ip.is_link_local)
