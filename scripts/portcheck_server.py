#!/usr/bin/env python3
"""Open temporary TCP listeners on candidate ports so a client can probe which
egress ports the restricted network allows. Auto-stops after a timeout.
Skips ports already in use (e.g. 22, 2053, 8443)."""

import socket
import sys
import threading
import time

PORTS = [80, 443, 53, 8080, 8880, 2052, 2082, 2086, 2095, 2096, 465, 587, 993, 995, 990, 990]
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 300

socks = []


def serve(port: int):
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("::", port))
        s.listen(16)
        s.settimeout(1.0)
    except OSError as e:
        print(f"port {port}: SKIP ({e})")
        return
    socks.append((port, s))
    print(f"port {port}: listening")
    end = time.time() + DURATION
    while time.time() < end:
        try:
            conn, _ = s.accept()
            conn.sendall(b"ok\n")
            conn.close()
        except OSError:
            pass
    s.close()


threads = [threading.Thread(target=serve, args=(p,), daemon=True) for p in set(PORTS)]
for t in threads:
    t.start()
print(f"listeners up for {DURATION}s")
time.sleep(DURATION)
print("done")
