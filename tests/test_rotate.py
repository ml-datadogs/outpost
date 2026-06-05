from datetime import datetime, timedelta, timezone

from outpost import rotate as rotate_mod
from outpost.config import Settings
from outpost.models import Inventory, NodeStatus, Provider, Region, Registry


def _registry():
    return Registry(
        providers=[
            Provider(name="aeza", policy_ok=True, regions=[Region(code="nl", country="NL", enabled=True)]),
            Provider(name="zomro", policy_ok=True, regions=[Region(code="nl", country="NL", enabled=True)]),
        ]
    )


def test_alternative_provider_prefers_different():
    reg = _registry()
    assert rotate_mod._alternative_provider(reg, "aeza") == "zomro"
    assert rotate_mod._alternative_provider(reg, "zomro") == "aeza"


def test_needs_replacement(node):
    node.status = NodeStatus.BLOCKED
    assert rotate_mod.needs_replacement(node)
    node.status = NodeStatus.ACTIVE
    assert not rotate_mod.needs_replacement(node)


def test_reap_destroys_expired(monkeypatch, node):
    node.status = NodeStatus.RETIRING
    node.retire_after = datetime.now(timezone.utc) - timedelta(minutes=1)
    node.provider_ref = {"elid": "1"}
    inv = Inventory()
    inv.upsert(node)

    destroyed = {}

    class FakeClient:
        def destroy(self, ref):
            destroyed["ref"] = ref

    monkeypatch.setattr(rotate_mod, "get_provider", lambda name, settings: FakeClient())
    removed = rotate_mod.reap_retired(inv, settings=Settings())
    assert removed == [node.id]
    assert destroyed["ref"] == {"elid": "1"}
    assert inv.get(node.id) is None


def test_reap_skips_within_grace(node):
    node.status = NodeStatus.RETIRING
    node.retire_after = datetime.now(timezone.utc) + timedelta(minutes=30)
    inv = Inventory()
    inv.upsert(node)
    removed = rotate_mod.reap_retired(inv, settings=Settings())
    assert removed == []
    assert inv.get(node.id) is not None
