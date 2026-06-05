"""Reachability checks: plain TCP liveness, and the macOS route-bypass canary test.

The route-bypass exists because, inside a restricted network, the operator's Surge
runs in Enhanced Mode (TUN) and captures all traffic. Env-var proxy settings cannot
escape that; only a host route via the real gateway tests the raw ISP path.
"""

from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class ProbeResult:
    target: str
    port: int
    ok: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None


def tcp_check(host: str, port: int, timeout: float = 8.0) -> ProbeResult:
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency = (time.perf_counter() - start) * 1000.0
            return ProbeResult(host, port, True, round(latency, 1))
    except OSError as exc:
        return ProbeResult(host, port, False, None, str(exc))


def resolve_a(host: str, dns: Optional[str] = None, timeout: float = 5.0) -> Optional[str]:
    """Resolve an A record. When `dns` is set, query it directly (bypasses TUN DNS)."""
    if dns:
        try:
            out = subprocess.run(
                ["dig", "+short", "-4", f"@{dns}", host, "A"],
                capture_output=True,
                text=True,
                timeout=timeout,
            ).stdout
            for line in out.splitlines():
                line = line.strip()
                if line and line[0].isdigit():
                    return line
            return None
        except (subprocess.SubprocessError, FileNotFoundError):
            return None
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


# --------------------------------------------------------------------------- macOS route bypass
def _route(action: str, ip: str, gateway: str) -> bool:
    try:
        proc = subprocess.run(
            ["sudo", "route", "-q", "-n", action, "-host", ip, gateway],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def isp_probe(
    target: str,
    port: int,
    gateway: str,
    dns: Optional[str] = None,
    timeout: float = 8.0,
) -> ProbeResult:
    """Test reachability of `target` over the raw ISP path (bypassing a TUN proxy).

    `target` may be a hostname (resolved via `dns`) or a literal IP. A temporary host
    route via `gateway` is installed for the duration of the check, then removed.
    """
    ip = target
    if not target.replace(".", "").isdigit():
        resolved = resolve_a(target, dns=dns)
        if not resolved:
            return ProbeResult(target, port, False, None, "DNS resolution failed")
        ip = resolved

    added = _route("add", ip, gateway)
    try:
        result = tcp_check(ip, port, timeout=timeout)
        result.target = f"{target} ({ip})" if target != ip else ip
        return result
    finally:
        if added:
            _route("delete", ip, gateway)
