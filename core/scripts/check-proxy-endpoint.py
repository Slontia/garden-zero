"""Read-only TCP diagnosis. Never print proxy URLs, usernames or passwords."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import ipaddress
import os
import socket
from urllib.parse import urlsplit

seen = set()
for key in ('http_proxy', 'https_proxy', 'all_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
    value = os.environ.get(key)
    if not value:
        continue
    try:
        endpoint = urlsplit(value if '://' in value else 'http://' + value)
        scheme = endpoint.scheme.lower()
        host = endpoint.hostname
        port = endpoint.port or {'http': 80, 'https': 443, 'socks5': 1080, 'socks5h': 1080, 'socks4': 1080}.get(scheme)
        if not host or not port:
            raise ValueError()
    except ValueError:
        print(f'{key}: invalid endpoint syntax (value hidden)')
        continue
    print(f'{key}: protocol={scheme}, host={host}, port={port}, credentials={"present (hidden)" if endpoint.username is not None else "absent"}')
    if (host, port) in seen:
        print('  Same endpoint as above; no repeated connection test.')
        continue
    seen.add((host, port))
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == 'localhost'
    if loopback:
        print('  Loopback address: verify whether proxy runs in WSL or Windows and whether WSL uses mirrored networking.')
    try:
        with socket.create_connection((host, port), timeout=5):
            print('  TCP PASS: endpoint accepts a connection; proxy protocol/upstream still needs checking.')
    except socket.gaierror:
        print('  TCP FAIL: proxy hostname could not be resolved.')
    except TimeoutError:
        print('  TCP FAIL: connection timed out.')
    except OSError as exc:
        print(f'  TCP FAIL: {type(exc).__name__}, errno={exc.errno}')
if not seen:
    print('No valid proxy endpoints found in environment.')
