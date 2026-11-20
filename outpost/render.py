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
import hashlib
from typing import List, Optional
from urllib.parse import quote, unquote, urlsplit

from .config import Settings
from .config import settings as default_settings
from .models import SURGE_PROTOCOLS, Inventory, Node, Protocol
from .server.pilot import GECKO_PACKET_SIZE, XHTTP_PATH
from .server.singbox import AI_CLEAN_DOMAIN_SUFFIXES, DEFAULT_REALITY_DEST, parse_upstream_proxy


def _renderable(inv: Inventory) -> List[Node]:
    return [n for n in inv.active() if n.ip and n.secrets and n.is_exit_eligible()]


def _secrets(node: Node):
    """Return node secrets, asserting presence (callers come from _renderable)."""
    assert node.secrets is not None, f"node {node.id} has no secrets"
    return node.secrets


def _happ_fragment(node: Node, label: str) -> str:
    country = node.country
    if len(country) == 2 and country.isascii() and country.isalpha():
        flag = "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in country.upper())
        label = f"{flag} {label}"
    return quote(label, safe="")


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


def render_surge(inv: Inventory, settings: Settings = default_settings) -> str:
    """Surge-format proxy lines (for a Surge subscription / proxy provider URL).

    When ``OUTPOST_AI_PROXY_URL`` is set, also emits a chained ``ai-clean-isp``
    SOCKS5 proxy entry (using the first active node as ``underlying-proxy``) so
    Surge can chain directly into the IPRoyal Static Residential IP even before
    nodes are re-bootstrapped.
    """
    lines = ["# Outpost managed proxies (Surge). Auto-generated; do not edit."]
    names: List[str] = []
    trojan_names: List[str] = []
    for node in _renderable(inv):
        if Protocol.TROJAN in node.protocols and Protocol.TROJAN in SURGE_PROTOCOLS:
            lines.append(_surge_trojan(node))
            names.append(f"{node.name}-trojan")
            trojan_names.append(f"{node.name}-trojan")
        if Protocol.HYSTERIA2 in node.protocols and Protocol.HYSTERIA2 in SURGE_PROTOCOLS:
            lines.append(_surge_hysteria2(node))
            names.append(f"{node.name}-hy2")

    upstream = parse_upstream_proxy(settings.ai_proxy_url)
    if upstream and upstream.scheme == "socks":
        auth_part = ""
        if upstream.username:
            auth_part = f", {upstream.username}, {upstream.password or ''}"
        under_target = trojan_names[0] if trojan_names else (names[0] if names else "")
        under = f", underlying-proxy={under_target}" if under_target else ""
        lines.append(
            f"ai-clean-isp = socks5, {upstream.host}, {upstream.port}{auth_part}{under}, udp-relay=false"
        )
    return "\n".join(lines) + "\n"


def render_surge_ai_ruleset() -> str:
    """Surge DOMAIN-SUFFIX ruleset for AI & billing services (force-remote-dns)."""
    lines = ["# Outpost AI & Billing Clean Exit Ruleset (use with force-remote-dns)"]
    for domain in AI_CLEAN_DOMAIN_SUFFIXES:
        lines.append(f"DOMAIN-SUFFIX,{domain}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- Happ (share links)
def _link_hysteria2(node: Node, gecko: bool = False) -> str:
    s = _secrets(node)
    sni = node.tls_domain or node.sni
    params: dict[str, str] = {"sni": sni}
    if s.hysteria2_obfs_password:
        params["obfs"] = "gecko" if gecko else "salamander"
        if gecko:
            params["obfs-packet-size"] = GECKO_PACKET_SIZE
        params["obfs-password"] = s.hysteria2_obfs_password
    _happ_cert_pin(params, node)
    query = _link_query(**params)
    auth = quote(s.hysteria2_password, safe="")
    suffix = f"/?{query}" if query else "/"
    fragment = _happ_fragment(node, node.name + ("-gecko" if gecko else "-hy2"))
    port = node.ports.gecko if gecko else node.ports.hysteria2
    return f"hysteria2://{auth}@{_link_host(node)}:{port}{suffix}#{fragment}"


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
    fragment = _happ_fragment(node, f"{node.name}-trojan")
    return (
        f"trojan://{quote(s.trojan_password, safe='')}@{_link_host(node)}:{node.ports.trojan}"
        f"?{query}#{fragment}"
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
    return f"vless://{s.reality_uuid}@{node.ip}:{listen_port}?{query}#{_happ_fragment(node, label)}"


def profile_key(link: str, ip: str, country: str) -> str:
    identity = "\n".join((ip, country.upper(), link.partition("#")[0]))
    return hashlib.sha256(identity.encode()).hexdigest()


def happ_profiles(node: Node, reality_dest: str = DEFAULT_REALITY_DEST) -> dict[str, str]:
    profiles = {}
    if Protocol.HYSTERIA2 in node.protocols:
        profiles["hy2"] = _link_hysteria2(node)
    if Protocol.TROJAN in node.protocols:
        profiles["trojan"] = _link_trojan(node)
    if Protocol.VLESS_REALITY in node.protocols:
        for port in dict.fromkeys([node.ports.vless_reality, 443]):
            profiles[f"reality-{port}"] = _link_reality(node, reality_dest, port)
    if "happ-pilot" in node.tags:
        params = {
            "encryption": "none",
            "type": "xhttp",
            "security": "tls",
            "path": XHTTP_PATH,
            "mode": "auto",
            "alpn": "h2",
            "sni": node.tls_domain or node.sni,
        }
        _happ_cert_pin(params, node)
        profiles["xhttp"] = (
            f"vless://{_secrets(node).reality_uuid}@{_link_host(node)}:{node.ports.xhttp}"
            f"?{_link_query(**params)}#{_happ_fragment(node, node.name + '-xhttp')}"
        )
        profiles["gecko"] = _link_hysteria2(node, gecko=True)
    return profiles


def render_happ_links(inv: Inventory, reality_dest: str = DEFAULT_REALITY_DEST) -> List[str]:
    links = []
    for node in _renderable(inv):
        for link in happ_profiles(node, reality_dest).values():
            key = profile_key(link, node.ip or "", node.country)
            if "happ-pilot" not in node.tags or key in node.happ_approved:
                links.append(link)
    return links


def _happ_socks_link(proxy_url: str) -> str:
    message = "OUTPOST_HAPP_SOCKS_URL must be socks5://user:password@host:port"
    try:
        proxy = urlsplit(proxy_url)
        port = proxy.port
    except ValueError:
        raise ValueError(message) from None
    if (
        proxy.scheme != "socks5"
        or not proxy.hostname
        or not port
        or not proxy.username
        or not proxy.password
        or proxy.path
        or proxy.query
        or proxy.fragment
        or any(character.isspace() for character in proxy_url)
    ):
        raise ValueError(message)
    host = f"[{proxy.hostname}]" if ":" in proxy.hostname else proxy.hostname
    credentials = f"{unquote(proxy.username)}:{unquote(proxy.password)}"
    auth = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
    label = quote("🇳🇱 Netherlands · IPRoyal", safe="")
    return f"socks://{auth}@{host}:{port}#{label}"


def render_happ(
    inv: Inventory,
    reality_dest: str = DEFAULT_REALITY_DEST,
    *,
    socks_url: Optional[str] = None,
    control_link: Optional[str] = None,
) -> str:
    """Happ universal subscription: base64 of newline-joined share links."""
    links = render_happ_links(inv, reality_dest)
    if control_link and control_link not in links:
        if not control_link.startswith("vless://") or any(c.isspace() for c in control_link):
            raise ValueError("Invalid Hostkey control link")
        links.insert(0, control_link)
    if socks_url:
        links.insert(0, _happ_socks_link(socks_url))
    body = "\n".join(links)
    return base64.b64encode(body.encode("utf-8")).decode("ascii")
