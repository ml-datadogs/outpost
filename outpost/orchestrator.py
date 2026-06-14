"""Provisioning orchestration: pick an eligible region, order, bootstrap, record."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from .config import Settings
from .config import settings as default_settings
from .models import Node, NodeStatus, Provider, Region, Registry
from .providers import ProvisionSpec, get_provider
from .secrets_gen import generate_node_secrets
from .server.bootstrap import bootstrap_node

# Exit-location preference: Central Asia / Caucasus first, then nearby EU.
COUNTRY_PREFERENCE = ["KZ", "AM", "GE", "TR", "FI", "SE", "NL", "DE", "PL", "GB", "FR"]


class OrchestrationError(RuntimeError):
    pass


def _country_rank(country: str) -> int:
    try:
        return COUNTRY_PREFERENCE.index(country.upper())
    except ValueError:
        return len(COUNTRY_PREFERENCE)


def pick_region(provider: Provider, region_code: Optional[str] = None) -> Region:
    eligible = provider.eligible_regions()
    if not eligible:
        raise OrchestrationError(
            f"provider {provider.name} has no eligible (non-RU, enabled, policy-ok) regions"
        )
    if region_code:
        for r in eligible:
            if r.code == region_code:
                return r
        raise OrchestrationError(f"region {region_code} not eligible on {provider.name}")
    # Prefer reachability-confirmed regions, then country preference.
    eligible.sort(key=lambda r: (not r.enabled, _country_rank(r.country)))
    return eligible[0]


def _read_ssh_public_key(settings: Settings) -> Optional[str]:
    p = settings.ssh_public_key_file
    if p and Path(p).exists():
        return Path(p).read_text().strip()
    return None


def make_node(provider_name: str, region: Region, settings: Settings, name: Optional[str] = None) -> Node:
    short = uuid.uuid4().hex[:6]
    node_name = name or f"{provider_name}-{region.country.lower()}-{short}"
    return Node(
        id=short,
        name=node_name,
        provider=provider_name,
        region=region.code,
        country=region.country,
        tls_domain=settings.tls_domain or None,
        insecure=settings.tls_domain is None,
        secrets=generate_node_secrets(),
        tags=["managed"],
    )


def provision_node(
    provider_name: str,
    registry: Registry,
    inventory,
    settings: Settings = default_settings,
    region_code: Optional[str] = None,
    name: Optional[str] = None,
    save=None,
) -> Node:
    provider_meta = registry.get(provider_name)
    if provider_meta is None:
        raise OrchestrationError(f"provider {provider_name} not in registry")
    if not provider_meta.policy_ok:
        raise OrchestrationError(f"provider {provider_name} fails policy gate (policy_ok=false)")

    region = pick_region(provider_meta, region_code)
    client = get_provider(provider_name, settings)
    node = make_node(provider_name, region, settings, name=name)

    spec = ProvisionSpec(
        name=node.name,
        region=region,
        ssh_public_key=_read_ssh_public_key(settings),
        ssh_key_name="outpost",
        term="hour",
        auto_prolong=False,
    )

    result = client.create(spec)
    node.provider_ref = result.provider_ref
    node.status = NodeStatus.PROVISIONING
    inventory.upsert(node)
    if save:
        save(inventory)  # persist early so a failed bootstrap is still tracked

    node.ip = result.ip or client.wait_for_ip(result.provider_ref)
    inventory.upsert(node)
    if save:
        save(inventory)

    bootstrap_node(node, settings=settings, root_password=result.root_password)
    node.status = NodeStatus.ACTIVE
    inventory.upsert(node)
    if save:
        save(inventory)
    return node


def adopt_node(
    ip: str,
    inventory,
    settings: Settings = default_settings,
    country: str = "??",
    provider_name: str = "byo",
    name: Optional[str] = None,
    root_password: Optional[str] = None,
    provider_ref: Optional[dict] = None,
    save=None,
) -> Node:
    """Adopt a hand-made server (BYO): IP + SSH access (key or password), install sing-box.

    Pass ``root_password`` when the box only has password auth (e.g. an Aeza VPS
    whose key we authorized in the panel); the bootstrapper then installs our key.
    """
    short = uuid.uuid4().hex[:6]
    node = Node(
        id=short,
        name=name or f"{provider_name}-{country.lower()}-{short}",
        provider=provider_name,
        region="manual",
        country=country,
        ip=ip,
        provider_ref=provider_ref or {},
        tls_domain=settings.tls_domain or None,
        insecure=settings.tls_domain is None,
        secrets=generate_node_secrets(),
        tags=["byo" if provider_name == "byo" else "adopted"],
    )
    node.status = NodeStatus.PROVISIONING
    inventory.upsert(node)
    if save:
        save(inventory)
    bootstrap_node(node, settings=settings, root_password=root_password)
    node.status = NodeStatus.ACTIVE
    inventory.upsert(node)
    if save:
        save(inventory)
    return node
