"""Render the inventory into Surge and Happ subscriptions.

One source of truth (the inventory) -> two client-specific renderings:
  * Surge: native proxy lines, Hysteria2 + Trojan only (Surge can't read Reality).
  * Happ:  base64 of share links, Hysteria2 + Trojan + VLESS/Reality.

Happ 4.11+ / Xray-core (2026-06+) reject allowInsecure/insecure/vcn.
Self-signed: pin leaf cert with pcs=<hex SHA-256 fingerprint> only.
Real cert (OUTPOST_TLS_DOMAIN): omit pcs/insecure entirely.
"""

from __future__ import annotations

import base64
from typing import List, Optional
from urllib.parse import quote

from .models import HAPP_PROTOCOLS, SURGE_PROTOCOLS, Inventory, Node, Protocol
from .server.singbox import DEFAULT_REALITY_DEST


def _renderable(inv: Inventory) -> List[Node]:
    return [n for n in inv.active() if n.ip and n.secrets and n.is_exit_eligible()]


def _secrets(node: Node):
    """Return node secrets, asserting presence (callers come from _renderable)."""
    assert node.secrets is not None, f"node {node.id} has no secrets"
    return node.secrets


def _frag(name: str) -> str:
    return quote(name, safe="")


def _cert_pin_b64(hex_pin: str) -> str:
    """Hysteria2 pinSHA256: base64-encoded raw SHA-256 digest (Surge/other clients)."""
    return base64.b64encode(bytes.fromhex(hex_pin)).decode("ascii")


def _happ_cert_pin(params: dict[str, str], node: Node) -> None:
    """Happ 4.11+: hex pcs only; never insecure/allowInsecure/vcn/pinSHA256."""
    if node.insecure and node.tls_cert_sha256:
        params["pcs"] = node.tls_cert_sha256


def _link_query(**params: str) -> str:
    return "&".join(f"{key}={quote(value, safe='')}" for key, value in params.items() if value != "")


def _link_host(node: Node) -> str:
    """Use TLS domain as host when we have a real cert (Happ validates hostname + SNI)."""
    return node.tls_domain or node.ip or ""


# --------------------------------------------------------------------------- Surge
def _surge_hysteria2(node: Node) -> str:
    s = _secrets(node)
    sni = node.tls_domain or node.sni
    return (
        f"{node.name}-hy2 = hysteria2, {node.ip}, {node.ports.hysteria2}, "
        f"password={s.hysteria2_password}, sni={sni}, skip-cert-verify={'true' if node.insecure else 'false'}"
    )


def _surge_trojan(node: Node) -> str:
    s = _secrets(node)
    sni = node.tls_domain or node.sni
    return (
        f"{node.name}-trojan = trojan, {node.ip}, {node.ports.trojan}, "
        f"password={s.trojan_password}, sni={sni}, skip-cert-verify={'true' if node.insecure else 'false'}"
    )


def render_surge(inv: Inventory) -> str:
    """Surge-format proxy lines (for a Surge subscription / proxy provider URL)."""
    lines = ["# Outpost managed proxies (Surge). Auto-generated; do not edit."]
    names: List[str] = []
    for node in _renderable(inv):
        if Protocol.HYSTERIA2 in node.protocols and Protocol.HYSTERIA2 in SURGE_PROTOCOLS:
            lines.append(_surge_hysteria2(node))
            names.append(f"{node.name}-hy2")
        if Protocol.TROJAN in node.protocols and Protocol.TROJAN in SURGE_PROTOCOLS:
            lines.append(_surge_trojan(node))
            names.append(f"{node.name}-trojan")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- Happ (share links)
def _link_hysteria2(node: Node) -> str:
    s = _secrets(node)
    sni = node.tls_domain or node.sni
    params: dict[str, str] = {"sni": sni}
    if s.hysteria2_obfs_password:
        params["obfs"] = "salamander"
        params["obfs-password"] = s.hysteria2_obfs_password
    _happ_cert_pin(params, node)
    query = _link_query(**params)
    auth = quote(s.hysteria2_password, safe="")
    suffix = f"/?{query}" if query else "/"
    return f"hysteria2://{auth}@{_link_host(node)}:{node.ports.hysteria2}{suffix}#{_frag(node.name + '-hy2')}"


def _link_trojan(node: Node) -> str:
    s = _secrets(node)
    sni = node.tls_domain or node.sni
    params: dict[str, str] = {
        "security": "tls",
        "type": "tcp",
        "sni": sni,
    }
    _happ_cert_pin(params, node)
    query = _link_query(**params)
    return (
        f"trojan://{quote(s.trojan_password, safe='')}@{_link_host(node)}:{node.ports.trojan}"
        f"?{query}#{_frag(node.name + '-trojan')}"
    )


def _link_reality(node: Node, reality_dest: str = DEFAULT_REALITY_DEST, port: Optional[int] = None) -> str:
    s = _secrets(node)
    listen_port = port if port is not None else node.ports.vless_reality
    # v2rayN / Happ compatible parameter set (no allowInsecure; Reality pins via pbk/sid).
    query = _link_query(
        encryption="none",
        security="reality",
        type="tcp",
        sni=reality_dest,
        fp="firefox",
        pbk=s.reality_public_key,
        sid=s.reality_short_id,
        spx="/",
    )
    label = node.name + "-reality" + (f"-{listen_port}" if listen_port != node.ports.vless_reality else "")
    return f"vless://{s.reality_uuid}@{node.ip}:{listen_port}?{query}#{_frag(label)}"


def render_happ_links(inv: Inventory, reality_dest: str = DEFAULT_REALITY_DEST) -> List[str]:
    links: List[str] = []
    for node in _renderable(inv):
        if Protocol.HYSTERIA2 in node.protocols:
            links.append(_link_hysteria2(node))
        if Protocol.TROJAN in node.protocols:
            links.append(_link_trojan(node))
        if Protocol.VLESS_REALITY in node.protocols and Protocol.VLESS_REALITY in HAPP_PROTOCOLS:
            links.append(_link_reality(node, reality_dest))
            if node.ports.vless_reality != 443:
                links.append(_link_reality(node, reality_dest, port=443))
    return links


def render_happ(inv: Inventory, reality_dest: str = DEFAULT_REALITY_DEST) -> str:
    """Happ universal subscription: base64 of newline-joined share links."""
    body = "\n".join(render_happ_links(inv, reality_dest))
    return base64.b64encode(body.encode("utf-8")).decode("ascii")
