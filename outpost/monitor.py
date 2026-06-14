"""Monitoring: CI liveness checks + status transitions from probe/liveness health."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, List, Optional

from .models import Inventory, Node, NodeStatus
from .reachability import tcp_check


def _now():
    return datetime.now(timezone.utc)


def check_liveness(node: Node, timeout: float = 8.0) -> bool:
    """Server-up check from anywhere (CI). Any open TCP inbound counts as alive.

    Tests the Trojan and Reality TCP ports (Hysteria2 is UDP and not TCP-probeable).
    """
    if not node.ip:
        return False
    for port in (node.ports.trojan, node.ports.vless_reality):
        res = tcp_check(node.ip, port, timeout=timeout)
        if res.ok:
            node.health.latency_ms = res.latency_ms
            return True
    return False


def monitor_liveness(inventory: Inventory, save: Optional[Callable] = None) -> List[Node]:
    """Update liveness for all managed nodes. Returns nodes whose status changed."""
    changed: List[Node] = []
    for node in inventory.nodes:
        if not {"managed", "byo", "adopted"} & set(node.tags):
            continue
        if not node.ip:
            continue
        alive = check_liveness(node)
        node.health.alive = alive
        node.health.checked_at = _now()
        node.health.source = "ci"
        prev = node.status
        if not alive and node.status in (NodeStatus.ACTIVE, NodeStatus.DEGRADED):
            node.status = NodeStatus.DOWN
        elif alive and node.status == NodeStatus.DOWN:
            node.status = NodeStatus.ACTIVE
        if node.status != prev:
            changed.append(node)
    if save:
        save(inventory)
    return changed


def apply_health_transitions(inventory: Inventory, save: Optional[Callable] = None) -> List[Node]:
    """Translate probe-observed reachability (from inside the country) into status.

    health.reachable is written by the on-Mac probe; False means the node's range is
    blocked from the restricted network even if the server is alive.
    """
    changed: List[Node] = []
    for node in inventory.nodes:
        prev = node.status
        if node.health.reachable is False and node.status in (NodeStatus.ACTIVE, NodeStatus.DEGRADED):
            node.status = NodeStatus.BLOCKED
        elif node.health.reachable is True and node.status == NodeStatus.BLOCKED:
            node.status = NodeStatus.ACTIVE
        if node.status != prev:
            changed.append(node)
    if save:
        save(inventory)
    return changed
