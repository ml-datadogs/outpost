"""Rotation: replace blocked/down nodes with fresh ones, then reap retired nodes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from .config import Settings
from .config import settings as default_settings
from .models import Inventory, Node, NodeStatus, Registry
from .orchestrator import provision_node
from .providers import ProviderError, get_provider

DEFAULT_GRACE_MINUTES = 15


def _now():
    return datetime.now(timezone.utc)


def needs_replacement(node: Node) -> bool:
    return node.status in (NodeStatus.BLOCKED, NodeStatus.DOWN) and "managed" in node.tags


def _alternative_provider(registry: Registry, current: str) -> Optional[str]:
    """Prefer a DIFFERENT eligible provider than the failing one (diversity)."""
    eligible = [p.name for p in registry.providers if p.policy_ok and p.eligible_regions()]
    others = [n for n in eligible if n != current]
    return (others or eligible or [None])[0]


def rotate(
    inventory: Inventory,
    registry: Registry,
    settings: Settings = default_settings,
    grace_minutes: int = DEFAULT_GRACE_MINUTES,
    save: Optional[Callable] = None,
    do_provision: bool = True,
) -> List[Node]:
    """Provision replacements for blocked/down nodes and mark the old ones retiring."""
    replacements: List[Node] = []
    for node in list(inventory.nodes):
        if not needs_replacement(node):
            continue
        target_provider = _alternative_provider(registry, node.provider) or node.provider
        if not do_provision:
            continue
        try:
            new_node = provision_node(
                target_provider,
                registry=registry,
                inventory=inventory,
                settings=settings,
                save=save,
            )
            new_node.tags.append(f"replaces:{node.id}")
            node.status = NodeStatus.RETIRING
            node.retire_after = _now() + timedelta(minutes=grace_minutes)
            inventory.upsert(node)
            inventory.upsert(new_node)
            replacements.append(new_node)
            if save:
                save(inventory)
        except (ProviderError, RuntimeError) as exc:
            node.health.note = f"rotation failed: {exc}"
            inventory.upsert(node)
            if save:
                save(inventory)
    return replacements


def reap_retired(
    inventory: Inventory,
    settings: Settings = default_settings,
    save: Optional[Callable] = None,
) -> List[str]:
    """Destroy nodes whose retirement grace period has elapsed."""
    destroyed: List[str] = []
    for node in list(inventory.nodes):
        if node.status != NodeStatus.RETIRING:
            continue
        if node.retire_after and _now() < node.retire_after:
            continue
        try:
            if node.provider_ref and node.provider not in ("byo",):
                client = get_provider(node.provider, settings)
                client.destroy(node.provider_ref)
            inventory.remove(node.id)
            destroyed.append(node.id)
            if save:
                save(inventory)
        except (ProviderError, RuntimeError) as exc:
            node.health.note = f"reap failed: {exc}"
            inventory.upsert(node)
            if save:
                save(inventory)
    return destroyed
