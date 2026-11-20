"""On-demand authenticated checks. Each Xray process has one outbound bound to the ISP interface."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from .server.pilot import GECKO_PACKET_SIZE, hy2_mask

TRACE = "https://1.1.1.1/cdn-cgi/trace"
TRANSFER = "https://speed.cloudflare.com/__down?bytes=1048576"
SIZE = 1024 * 1024


@dataclass(frozen=True)
class Observation:
    ok: bool
    stage: str
    bytes_received: int = 0
    transfer_ms: int = 0
    handshake_ms: int = 0


def client_outbound(link: str, ip: str, interface: str) -> dict:
    """Interpret the share-link subset used by the pilot, preserving auth and certificate checks."""
    url = urlsplit(link)
    q = dict(parse_qsl(url.query))
    allowed = {
        "security",
        "encryption",
        "type",
        "sni",
        "fp",
        "pbk",
        "sid",
        "spx",
        "flow",
        "pcs",
        "path",
        "mode",
        "alpn",
        "obfs",
        "obfs-password",
        "obfs-packet-size",
    }
    if q.keys() - allowed or q.get("encryption", "none") != "none":
        raise ValueError("Unsupported pilot share-link parameters")
    port = url.port
    if not port or not url.username:
        raise ValueError("Pilot profile needs an endpoint and credentials")
    auth = unquote(url.username)
    endpoint = {"address": str(ip_address(ip)), "port": port}
    stream: dict = {"sockopt": {"interface": interface}}
    security = q.get("security", "tls")
    if security == "reality":
        stream["realitySettings"] = {
            "publicKey": q["pbk"],
            "shortId": q["sid"],
            "serverName": q["sni"],
            "fingerprint": q.get("fp", "firefox"),
            "spiderX": q.get("spx", "/"),
        }
    elif security == "tls":
        tls: dict = {"serverName": q.get("sni", url.hostname)}
        if q.get("pcs"):
            tls["pinnedPeerCertSha256"] = q["pcs"]
        if q.get("alpn"):
            tls["alpn"] = q["alpn"].split(",")
        stream["tlsSettings"] = tls
    else:
        raise ValueError("Pilot requires TLS or REALITY")
    stream["security"] = security
    stream["network"] = q.get("type", "tcp")
    if stream["network"] == "xhttp":
        stream["xhttpSettings"] = {"path": q["path"], "mode": q.get("mode", "auto")}
    elif stream["network"] not in ("tcp", "raw"):
        raise ValueError("Unsupported pilot transport")
    if url.scheme == "vless":
        user = {"id": auth, "encryption": "none", "flow": q.get("flow", "")}
        settings = {"vnext": [dict(endpoint, users=[user])]}
        protocol = "vless"
    elif url.scheme == "trojan":
        settings = {"servers": [dict(endpoint, password=auth)]}
        protocol = "trojan"
    elif url.scheme in {"hy2", "hysteria2"}:
        protocol = "hysteria"
        settings = dict(endpoint, version=2)
        stream.update(network="hysteria", hysteriaSettings={"version": 2, "auth": auth})
        if q.get("obfs"):
            if q["obfs"] not in {"salamander", "gecko"}:
                raise ValueError("Unsupported Hysteria obfuscation")
            size = q.get("obfs-packet-size", GECKO_PACKET_SIZE) if q["obfs"] == "gecko" else ""
            stream["finalmask"] = hy2_mask(q["obfs-password"], size)
    else:
        raise ValueError("Unsupported pilot protocol")
    return {"protocol": protocol, "settings": settings, "streamSettings": stream}


@contextmanager
def tunnel(outbound: dict, core: str):
    with tempfile.TemporaryDirectory(prefix="outpost-probe-") as directory:
        path = Path(directory) / "client.json"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        # SOCKS auth prevents another process on a reused port from producing a successful sample.
        token = os.urandom(16).hex()
        inbound = {
            "listen": "127.0.0.1",
            "port": port,
            "protocol": "socks",
            "settings": {"auth": "password", "accounts": [{"user": "probe", "pass": token}]},
        }
        path.write_text(
            json.dumps({"log": {"loglevel": "none"}, "inbounds": [inbound], "outbounds": [outbound]})
        )
        with subprocess.Popen(
            [core, "run", "-c", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ) as process:
            try:
                deadline = time.monotonic() + 5
                while process.poll() is None and time.monotonic() < deadline:
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.05)
                else:
                    raise RuntimeError("Xray did not start; verify core version and profile")
                yield f"socks5h://probe:{token}@127.0.0.1:{port}"
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()


def curl(url: str, args: list, max_bytes: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "curl",
            "-q",
            "-4fsS",
            "--connect-timeout",
            "6",
            "--max-time",
            "25",
            "--max-filesize",
            str(max_bytes),
            *args,
            url,
        ],
        capture_output=True,
        timeout=30,
    )


def trace(body: bytes) -> dict:
    return dict(line.split("=", 1) for line in body.decode("ascii", "replace").splitlines() if "=" in line)


def check_origin(interface: str) -> None:
    if not re.fullmatch(r"(en\d+|eth\d+|enp[\w]+|wlan\d+|wlp[\w]+)", interface):
        raise ValueError("Choose a physical Wi-Fi or Ethernet interface")
    socket.if_nametoindex(interface)
    result = curl("https://1.1.1.1/cdn-cgi/trace", ["--interface", f"if!{interface}", "--noproxy", "*"], 4096)
    if result.returncode or trace(result.stdout).get("loc") != "RU":
        raise RuntimeError("Physical RU origin unconfirmed; no sample recorded")


def measure(link: str, ip: str, country: str, interface: str) -> Observation:
    core = os.getenv("OUTPOST_XRAY") or shutil.which("xray")
    if not core:
        raise RuntimeError("Set OUTPOST_XRAY to an Xray executable")
    with tunnel(client_outbound(link, ip, interface), core) as proxy:
        args = ["--proxy", proxy, "--noproxy", ""]
        start = time.monotonic()
        r = curl(TRACE, args, 4096)
        elapsed = round((time.monotonic() - start) * 1000)
        if r.returncode:
            return Observation(False, "tunnel/trace", handshake_ms=elapsed)
        exit_info = trace(r.stdout)
        if exit_info.get("ip") != ip or exit_info.get("loc") != country:
            return Observation(False, "unexpected-exit", handshake_ms=elapsed)
        start = time.monotonic()
        r = curl(TRANSFER, args, SIZE)
        ms = round((time.monotonic() - start) * 1000)
        passed = not r.returncode and len(r.stdout) == SIZE
        return Observation(passed, "ok" if passed else "transfer", len(r.stdout), ms, elapsed)
