import base64

import pytest
from outpost.models import Inventory, Node, NodePorts, NodeStatus
from outpost.secrets_gen import generate_node_secrets


@pytest.fixture
def node() -> Node:
    return Node(
        id="abc123",
        name="zomro-nl-abc123",
        provider="zomro",
        region="nl",
        country="NL",
        ip="203.0.113.10",
        ports=NodePorts(hysteria2=443, trojan=443, vless_reality=2053),
        secrets=generate_node_secrets(),
        status=NodeStatus.ACTIVE,
        tags=["managed"],
    )


@pytest.fixture
def ru_node() -> Node:
    return Node(
        id="ru0001",
        name="aeza-ru-ru0001",
        provider="aeza",
        region="Russia",
        country="RU",
        ip="198.51.100.5",
        secrets=generate_node_secrets(),
        status=NodeStatus.ACTIVE,
        tags=["managed"],
    )


@pytest.fixture
def inventory(node) -> Inventory:
    inv = Inventory()
    inv.upsert(node)
    return inv
