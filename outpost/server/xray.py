"""Render an Xray-core server config for VLESS/Reality (Happ uses Xray-core)."""

from __future__ import annotations

import json

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..config import Settings
from ..config import settings as default_settings
from ..models import Node
from .singbox import DEFAULT_REALITY_DEST


def render_xray_config(
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
    template = env.get_template("xray.json.tpl")

    rendered = template.render(
        reality_ports=sorted({node.ports.vless_reality, 443}),
        reality_uuid=node.secrets.reality_uuid,
        reality_private_key=node.secrets.reality_private_key,
        reality_short_id=node.secrets.reality_short_id,
        reality_dest=reality_dest,
    )
    json.loads(rendered)
    return rendered
