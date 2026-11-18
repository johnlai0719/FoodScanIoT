"""Explicit local clients, including an opt-in Docker Desktop host gateway."""
import os
from pathlib import Path
import socket
import struct


def is_local_client(request):
    allowed = {'127.0.0.1', '::1', 'testclient'}
    allowed.update(value.strip() for value in os.getenv('DASHBOARD_TRUSTED_CLIENTS', '').split(',') if value.strip())
    if os.getenv('DASHBOARD_TRUST_DOCKER_GATEWAY') == '1':
        for line in Path('/proc/net/route').read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) > 3 and fields[1] == '00000000' and int(fields[3], 16) & 2:
                allowed.add(socket.inet_ntoa(struct.pack('<I', int(fields[2], 16))))
    return request.client is None or request.client.host in allowed
