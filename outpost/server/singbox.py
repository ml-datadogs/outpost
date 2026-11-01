"""Render a sing-box server config for a node from the Jinja template."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote, urlparse

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..config import Settings
from ..config import settings as default_settings
from ..models import Node

CERT_PATH = "/etc/sing-box/cert.pem"
KEY_PATH = "/etc/sing-box/key.pem"
# Reality steals the TLS handshake from this host, so its certificate chain must fit
# in Reality's handshake buffer (~2.9 KB). www.microsoft.com was used until its chain
# grew to ~5.9 KB (8273-byte Certificate message): auth still succeeds, but the spliced
# handshake never completes and every authenticated client is dropped. Cloudflare's
# ECDSA chain is ~2.5 KB. Verify a replacement before switching:
#   openssl s_client -connect <host>:443 -servername <host> -showcerts
DEFAULT_REALITY_DEST = "www.cloudflare.com"

# Domains routed through the clean Static Residential (ISP) second hop when
# OUTPOST_AI_PROXY_URL is configured. Covers Anthropic/Claude Code, OpenAI/Codex,
# Cursor, Stripe billing, OpenRouter, and IP-check utilities.
AI_CLEAN_DOMAIN_SUFFIXES = [
    "anthropic.com",
    "claude.ai",
    "claude.com",
    "claudeusercontent.com",
    "statsig.anthropic.com",
    "statsig.com",
    "statsigapi.net",
    "featuregates.org",
    "intercom.io",
    "intercomcdn.com",
    "workos.com",
    "openai.com",
    "chatgpt.com",
    "oaistatic.com",
    "oaiusercontent.com",
    "auth0.openai.com",
    "auth0.com",
    "cursor.sh",
    "cursor.com",
    "cursorapi.com",
    "stripe.com",
    "stripe.network",
    "stripecdn.com",
    "hcaptcha.com",
    "arkoselabs.com",
    "sift.com",
    "siftscience.com",
    "withpersona.com",
    "openrouter.ai",
    "ipinfo.io",
]


@dataclass(frozen=True)
class UpstreamProxy:
    scheme: str  # "socks" or "http"
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None


def parse_upstream_proxy(proxy_url: Optional[str]) -> Optional[UpstreamProxy]:
    """Parse a SOCKS5 or HTTP upstream proxy URL.

    Supports both standard URL format ``socks5://user:pass@host:port`` and
    IPRoyal's raw panel format ``host:port:user:pass`` (or ``socks5://host:port:user:pass``).
    """
    if not proxy_url or not proxy_url.strip():
        return None
    raw = proxy_url.strip()
    scheme_prefix = "socks5"
    if "://" in raw:
        scheme_prefix, raw = raw.split("://", 1)

    scheme_raw = scheme_prefix.lower()
    if scheme_raw in ("socks5", "socks5h", "socks"):
        scheme = "socks"
    elif scheme_raw in ("http", "https"):
        scheme = "http"
    else:
        raise ValueError(f"unsupported OUTPOST_AI_PROXY_URL scheme: {scheme_prefix!r}")

    # Support IPRoyal raw format: host:port:user:pass (when no '@' is present)
    if "@" not in raw and raw.count(":") == 3:
        host, port_s, user, pw = raw.split(":", 3)
        return UpstreamProxy(
            scheme=scheme,
            host=host,
            port=int(port_s),
            username=user,
            password=pw,
        )

    parsed = urlparse(f"{scheme_prefix}://{raw}")
    if not parsed.hostname or not parsed.port:
        raise ValueError("OUTPOST_AI_PROXY_URL must include host and port (socks5://user:pass@host:port)")
    return UpstreamProxy(
        scheme=scheme,
        host=parsed.hostname,
        port=int(parsed.port),
        username=unquote(parsed.username) if parsed.username is not None else None,
        password=unquote(parsed.password) if parsed.password is not None else None,
    )


def render_singbox_config(
    node: Node,
    settings: Settings = default_settings,
    reality_dest: str = DEFAULT_REALITY_DEST,
) -> str:
    if node.secrets is None:
        raise ValueError(f"node {node.id} has no secrets; cannot render config")

    env = Environment(
        loader=FileSystemLoader(str(settings.server_dir)),
        undefined=StrictUndefined,
        autoescape=False,
    )
    env.filters["tojson"] = lambda v: json.dumps(v)
    template = env.get_template("singbox.json.tpl")

    server_name = node.tls_domain or node.sni
    upstream = parse_upstream_proxy(settings.ai_proxy_url)
    rendered = template.render(
        hysteria2_port=node.ports.hysteria2,
        trojan_port=node.ports.trojan,
        reality_port=node.ports.vless_reality,
        hysteria2_password=node.secrets.hysteria2_password,
        hysteria2_obfs_password=node.secrets.hysteria2_obfs_password,
        trojan_password=node.secrets.trojan_password,
        reality_uuid=node.secrets.reality_uuid,
        reality_private_key=node.secrets.reality_private_key,
        reality_short_id=node.secrets.reality_short_id,
        reality_dest=reality_dest,
        server_name=server_name,
        cert_path=CERT_PATH,
        key_path=KEY_PATH,
        ai_proxy=upstream,
        ai_domains=AI_CLEAN_DOMAIN_SUFFIXES,
    )
    # Validate it is well-formed JSON before shipping to the server.
    json.loads(rendered)
    return rendered
