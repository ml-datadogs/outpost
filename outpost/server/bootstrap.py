"""SSH bootstrap: install sing-box and apply a node's rendered config.

Auth order:
  1. root password returned by the provider order (Aeza/Zomro): freshly ordered
     nodes only accept password auth, so this takes precedence on first boot. We
     then append our public key to authorized_keys for subsequent key-based runs.
  2. SSH private key from settings (re-runs after the key is installed, and the
     `adopt` flow where the key is already on the box).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import paramiko

from ..config import Settings
from ..config import settings as default_settings
from ..models import Node
from ..secrets_gen import generate_password
from .singbox import DEFAULT_REALITY_DEST, render_singbox_config
from .xray import render_xray_config

REMOTE_CONFIG = "/tmp/outpost-singbox.json"
REMOTE_XRAY_CONFIG = "/tmp/outpost-xray.json"
REMOTE_SCRIPT = "/tmp/outpost-bootstrap.sh"


class BootstrapError(RuntimeError):
    pass


def wait_for_ssh(ip: str, port: int = 22, timeout: int = 300, interval: int = 6) -> None:
    import socket

    deadline = time.time() + timeout
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        try:
            with socket.create_connection((ip, port), timeout=8):
                return
        except OSError as exc:  # noqa: PERF203
            last_err = exc
            time.sleep(interval)
    raise BootstrapError(f"SSH on {ip}:{port} not reachable within {timeout}s: {last_err}")


def _connect(
    node: Node,
    settings: Settings,
    root_password: Optional[str],
) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {"hostname": node.ip, "port": node.ssh_port, "username": node.ssh_user, "timeout": 20}

    if root_password:
        # Freshly provisioned nodes only have password auth until we install the key.
        client.connect(password=root_password, look_for_keys=False, allow_agent=False, **kwargs)
    elif settings.ssh_private_key_file and Path(settings.ssh_private_key_file).exists():
        client.connect(key_filename=str(settings.ssh_private_key_file), **kwargs)
    else:
        client.connect(**kwargs)  # rely on agent / default keys
    return client


def _run(client: paramiko.SSHClient, command: str) -> str:
    stdin, stdout, stderr = client.exec_command(command, timeout=600)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    if exit_code != 0:
        raise BootstrapError(f"remote command failed ({exit_code}): {command}\n{err or out}")
    return out


def _put(client: paramiko.SSHClient, content: str, remote_path: str, mode: int = 0o600) -> None:
    sftp = client.open_sftp()
    try:
        with sftp.open(remote_path, "w") as fh:
            fh.write(content)
        sftp.chmod(remote_path, mode)
    finally:
        sftp.close()


def bootstrap_node(
    node: Node,
    settings: Settings = default_settings,
    root_password: Optional[str] = None,
    reality_dest: str = DEFAULT_REALITY_DEST,
) -> None:
    if not node.ip:
        raise BootstrapError(f"node {node.id} has no IP")

    tls_domain = node.tls_domain or settings.tls_domain
    if tls_domain:
        node.tls_domain = tls_domain
        node.sni = tls_domain
        node.insecure = False

    config_json = render_singbox_config(node, settings=settings, reality_dest=reality_dest)
    xray_json = render_xray_config(node, settings=settings, reality_dest=reality_dest)
    script = (settings.server_dir / "bootstrap.sh").read_text()
    if node.secrets and not node.secrets.hysteria2_obfs_password:
        node.secrets.hysteria2_obfs_password = generate_password()

    wait_for_ssh(node.ip, node.ssh_port)
    client = _connect(node, settings, root_password)
    try:
        # If we logged in with a password, persist our public key for future runs.
        if root_password and settings.ssh_public_key_file and Path(settings.ssh_public_key_file).exists():
            pub = Path(settings.ssh_public_key_file).read_text().strip()
            qpub = _shell_quote(pub)
            _run(
                client,
                "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && "
                f"grep -qxF {qpub} ~/.ssh/authorized_keys || "
                f"printf '%s\\n' {qpub} >> ~/.ssh/authorized_keys; "
                "chmod 600 ~/.ssh/authorized_keys",
            )

        _put(client, config_json, REMOTE_CONFIG, mode=0o600)
        _put(client, xray_json, REMOTE_XRAY_CONFIG, mode=0o600)
        _put(client, script, REMOTE_SCRIPT, mode=0o700)

        ports = f"{node.ports.hysteria2},{node.ports.trojan},{node.ports.vless_reality}"
        sni = node.tls_domain or node.sni
        env = (
            f"OUTPOST_SNI={_shell_quote(sni)} "
            f"OUTPOST_PORTS={_shell_quote(ports)} "
            f"OUTPOST_XRAY_CONFIG={_shell_quote(REMOTE_XRAY_CONFIG)}"
        )
        if node.tls_domain:
            env += f" OUTPOST_TLS_DOMAIN={_shell_quote(node.tls_domain)}"
        _run(client, f"{env} bash {REMOTE_SCRIPT} {REMOTE_CONFIG}")
        pin = _run(
            client,
            "tr -d '[:space:]' < /etc/sing-box/cert.sha256 2>/dev/null || "
            "openssl x509 -in /etc/sing-box/cert.pem -noout -fingerprint -sha256 "
            "| sed 's/sha256 Fingerprint=//I; s/://g' | tr '[:upper:]' '[:lower:]'",
        ).strip()
        if len(pin) == 64 and all(c in "0123456789abcdef" for c in pin):
            node.tls_cert_sha256 = pin
    finally:
        client.close()


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"
