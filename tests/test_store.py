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


def test_ambiguous_ids_are_quoted_for_other_parsers(tmp_path, inventory):
    """Node ids like "967e00" must survive a sops (Go YAML) round-trip.

    PyYAML reads them back as strings either way, so assert on the emitted text:
    unquoted, a YAML 1.1/1.2 parser turns 967e00 into the float 967.
    """
    node = inventory.nodes[0]
    node.id = "967e00"
    settings = Settings(state_dir=tmp_path)
    path = tmp_path / "inventory.yaml"
    store.save_inventory(inventory, settings, path=path)

    text = path.read_text()
    assert "id: '967e00'" in text
    assert store.load_inventory(settings).nodes[0].id == "967e00"


def test_unambiguous_scalars_stay_unquoted(tmp_path, inventory):
    inventory.nodes[0].id = "4bd1b6"
    settings = Settings(state_dir=tmp_path)
    store.save_inventory(inventory, settings, path=tmp_path / "inventory.yaml")
    assert "id: 4bd1b6" in (tmp_path / "inventory.yaml").read_text()


def test_registry_load_from_repo_seed():
    # The committed registry seed should parse and exclude hetzner on policy.
    settings = Settings()
    reg = store.load_registry(settings)
    het = reg.get("hetzner")
    assert het is not None and het.policy_ok is False
    assert het.eligible_regions() == []
