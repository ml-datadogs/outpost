"""Load/save the inventory and registry, transparently handling SOPS encryption."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import yaml

from . import crypto
from .config import Settings
from .config import settings as default_settings
from .models import Inventory, Registry


def _read_yaml(path: Path) -> dict:
    if crypto.is_encrypted_path(path):
        text = crypto.decrypt(path)
    else:
        text = path.read_text()
    return yaml.safe_load(text) or {}


def _dump_yaml(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _write_yaml(path: Path, data: dict) -> None:
    text = _dump_yaml(data)
    if crypto.is_encrypted_path(path):
        crypto.encrypt_to(path, text)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


# --------------------------------------------------------------------------- inventory
def load_inventory(settings: Settings = default_settings) -> Inventory:
    path = settings.inventory_path
    if not path.exists():
        return Inventory()
    data = _read_yaml(path)
    return Inventory.model_validate(data)


def save_inventory(
    inv: Inventory, settings: Settings = default_settings, path: Optional[Path] = None
) -> Path:
    target = path or settings.inventory_path
    if not crypto.is_encrypted_path(target) and target.name == "inventory.yaml":
        # plaintext fallback: warn loudly because this file holds live credentials
        print(
            f"WARNING: writing UNENCRYPTED inventory to {target}. "
            "Install sops+age and switch to inventory.enc.yaml for production.",
            file=sys.stderr,
        )
    data = inv.model_dump(mode="json", exclude_none=False)
    _write_yaml(target, data)
    return target


# --------------------------------------------------------------------------- registry
def load_registry(settings: Settings = default_settings) -> Registry:
    path = settings.registry_path
    if not path.exists():
        return Registry()
    data = _read_yaml(path)
    return Registry.model_validate(data)


def save_registry(reg: Registry, settings: Settings = default_settings) -> Path:
    path = settings.registry_path
    data = reg.model_dump(mode="json", exclude_none=False)
    _write_yaml(path, data)
    return path
