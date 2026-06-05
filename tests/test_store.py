from outpost import store
from outpost.config import Settings
from outpost.models import Inventory


def test_inventory_roundtrip_plaintext(tmp_path, inventory):
    settings = Settings(state_dir=tmp_path)
    path = tmp_path / "inventory.yaml"
    store.save_inventory(inventory, settings, path=path)
    assert path.exists()

    loaded = store.load_inventory(settings)
    assert len(loaded.nodes) == 1
    n = loaded.nodes[0]
    assert n.id == inventory.nodes[0].id
    assert n.secrets is not None and inventory.nodes[0].secrets is not None
    assert n.secrets.reality_public_key == inventory.nodes[0].secrets.reality_public_key


def test_registry_load_from_repo_seed():
    # The committed registry seed should parse and exclude hetzner on policy.
    settings = Settings()
    reg = store.load_registry(settings)
    het = reg.get("hetzner")
    assert het is not None and het.policy_ok is False
    assert het.eligible_regions() == []
