"""Render the inventory into Surge and Happ subscriptions.

One source of truth (the inventory) -> two client-specific renderings:
  * Surge: native proxy lines, Hysteria2 + Trojan only (Surge can't read Reality).
  * Happ:  base64 of share links, Hysteria2 + Trojan + VLESS/Reality.
"""

from __future__ import annotations

import base64
from typing import List
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
    return (
        f"hysteria2://{quote(s.hysteria2_password, safe='')}@{node.ip}:{node.ports.hysteria2}"
        f"/?insecure={1 if node.insecure else 0}&sni={quote(sni, safe='')}#{_frag(node.name + '-hy2')}"
    )


def _link_trojan(node: Node) -> str:
    s = _secrets(node)
    sni = node.tls_domain or node.sni
    return (
        f"trojan://{quote(s.trojan_password, safe='')}@{node.ip}:{node.ports.trojan}"
        f"?security=tls&sni={quote(sni, safe='')}&allowInsecure={1 if node.insecure else 0}&type=tcp"
        f"#{_frag(node.name + '-trojan')}"
    )


def _link_reality(node: Node, reality_dest: str = DEFAULT_REALITY_DEST) -> str:
    s = _secrets(node)
    return (
        f"vless://{s.reality_uuid}@{node.ip}:{node.ports.vless_reality}"
        f"?encryption=none&security=reality&type=tcp&flow=xtls-rprx-vision"
        f"&sni={quote(reality_dest, safe='')}&fp=chrome"
        f"&pbk={quote(s.reality_public_key, safe='')}&sid={s.reality_short_id}"
        f"#{_frag(node.name + '-reality')}"
    )


def render_happ_links(inv: Inventory, reality_dest: str = DEFAULT_REALITY_DEST) -> List[str]:
    links: List[str] = []
    for node in _renderable(inv):
        if Protocol.HYSTERIA2 in node.protocols:
            links.append(_link_hysteria2(node))
        if Protocol.TROJAN in node.protocols:
            links.append(_link_trojan(node))
        if Protocol.VLESS_REALITY in node.protocols and Protocol.VLESS_REALITY in HAPP_PROTOCOLS:
            links.append(_link_reality(node, reality_dest))
    return links


def render_happ(inv: Inventory, reality_dest: str = DEFAULT_REALITY_DEST) -> str:
    """Happ universal subscription: base64 of newline-joined share links."""
    body = "\n".join(render_happ_links(inv, reality_dest))
    return base64.b64encode(body.encode("utf-8")).decode("ascii")
