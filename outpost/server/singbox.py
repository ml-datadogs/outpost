"""Render a sing-box server config for a node from the Jinja template."""

from __future__ import annotations

import json

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
    )
    # Validate it is well-formed JSON before shipping to the server.
    json.loads(rendered)
    return rendered
