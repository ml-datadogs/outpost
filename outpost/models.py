"""Core data models: protocols, nodes, provider/region registry, inventory."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Protocol(str, enum.Enum):
    HYSTERIA2 = "hysteria2"
    TROJAN = "trojan"
    VLESS_REALITY = "vless-reality"


# Which protocols each client app can actually consume.
SURGE_PROTOCOLS = {Protocol.HYSTERIA2, Protocol.TROJAN}
HAPP_PROTOCOLS = {Protocol.HYSTERIA2, Protocol.TROJAN, Protocol.VLESS_REALITY}


class NodeStatus(str, enum.Enum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    DEGRADED = "degraded"  # reachable but slow / partial
    BLOCKED = "blocked"  # unreachable from inside the restricted network
    DOWN = "down"  # server itself not responding (liveness)
    RETIRING = "retiring"  # replacement is up; pending destroy


class HealthCheck(BaseModel):
    """Most recent observation for a node, written by probe / monitor."""

    checked_at: Optional[datetime] = None
    reachable: Optional[bool] = None  # from inside the restricted network
    alive: Optional[bool] = None  # liveness from CI / abroad
    latency_ms: Optional[float] = None
    source: Optional[str] = None  # "probe" | "ci"
    note: Optional[str] = None


class NodeSecrets(BaseModel):
    """Per-node generated secrets. Sensitive; lives only in encrypted inventory."""

    hysteria2_password: str
    trojan_password: str
    reality_uuid: str
    reality_private_key: str
    reality_public_key: str
    reality_short_id: str


class NodePorts(BaseModel):
    hysteria2: int = 443
    trojan: int = 8443
    vless_reality: int = 2053


class Node(BaseModel):
    id: str
    name: str
    provider: str
    region: str
    country: str
    ip: Optional[str] = None
    ssh_user: str = "root"
    ssh_port: int = 22

    status: NodeStatus = NodeStatus.PROVISIONING
    created_at: datetime = Field(default_factory=_now)
    retire_after: Optional[datetime] = None

    # TLS / SNI: a real domain when available, else a benign SNI for self-signed.
    tls_domain: Optional[str] = None
    sni: str = "www.bing.com"
    insecure: bool = True  # skip-cert-verify when using self-signed

    ports: NodePorts = Field(default_factory=NodePorts)
    protocols: List[Protocol] = Field(
        default_factory=lambda: [Protocol.HYSTERIA2, Protocol.TROJAN, Protocol.VLESS_REALITY]
    )
    secrets: Optional[NodeSecrets] = None
    health: HealthCheck = Field(default_factory=HealthCheck)
    tags: List[str] = Field(default_factory=list)

    # provider-native identifiers needed to manage / destroy the resource
    provider_ref: Dict[str, str] = Field(default_factory=dict)

    @property
    def display(self) -> str:
        loc = f"{self.country}/{self.region}"
        return f"{self.name} [{self.provider} {loc}]"

    def is_exit_eligible(self) -> bool:
        return self.country.upper() != "RU"


class Region(BaseModel):
    code: str  # provider-native region/datacenter code
    country: str  # ISO-ish country code, e.g. "NL", "DE", "KZ", "RU"
    city: Optional[str] = None
    canary: Optional[str] = None  # real VPS-range host/IP used for reachability tests
    enabled: bool = True
    # provider-native product id to order in this region (filled from live API)
    product_ref: Dict[str, str] = Field(default_factory=dict)


class Provider(BaseModel):
    name: str
    policy_ok: bool = True  # accepts RU users + usable payment
    sanctioned: bool = False  # e.g. OFAC; usable but risky
    notes: Optional[str] = None
    regions: List[Region] = Field(default_factory=list)

    def eligible_regions(self) -> List[Region]:
        """Regions usable as exits: enabled, non-RU, on a policy-ok provider."""
        if not self.policy_ok:
            return []
        return [r for r in self.regions if r.enabled and r.country.upper() != "RU"]


class Registry(BaseModel):
    """Provider/region catalog with eligibility flags. Safe to commit (no secrets)."""

    providers: List[Provider] = Field(default_factory=list)

    def get(self, name: str) -> Optional[Provider]:
        for p in self.providers:
            if p.name == name:
                return p
        return None

    def eligible(self) -> List[tuple]:
        """All (provider, region) pairs eligible to host an exit node."""
        out: List[tuple] = []
        for p in self.providers:
            for r in p.eligible_regions():
                out.append((p, r))
        return out


class Inventory(BaseModel):
    """Single source of truth for live nodes."""

    version: int = 1
    nodes: List[Node] = Field(default_factory=list)

    def get(self, node_id: str) -> Optional[Node]:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def upsert(self, node: Node) -> None:
        for i, n in enumerate(self.nodes):
            if n.id == node.id:
                self.nodes[i] = node
                return
        self.nodes.append(node)

    def remove(self, node_id: str) -> None:
        self.nodes = [n for n in self.nodes if n.id != node_id]

    def active(self) -> List[Node]:
        return [n for n in self.nodes if n.status in (NodeStatus.ACTIVE, NodeStatus.DEGRADED)]
