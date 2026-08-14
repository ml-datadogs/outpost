"""Load/save the inventory and registry, transparently handling SOPS encryption."""

from __future__ import annotations

import re
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


# PyYAML only quotes strings IT would re-read as another type. Tokens like the node
# id "967e00" are plain strings to PyYAML (its float rule needs a dot) but scientific
# notation to YAML 1.1/1.2 parsers — sops rewrites such a value to "967", silently
# corrupting the id on every encrypt round-trip. Quote anything another parser could
# read as a number or bool.
_AMBIGUOUS_SCALAR = re.compile(
    r"""^[-+]?(?:
        \d[\d_]*(?:\.[\d_]*)?(?:[eE][-+]?\d+)?    # 12  1.5  967e00  1_000
        |\.[\d_]+(?:[eE][-+]?\d+)?                # .5
        |0[bB][01_]+|0[oO][0-7_]+|0[xX][\dA-Fa-f_]+  # 0b1  0o7  0x1f
    )$""",
    re.VERBOSE,
)
_AMBIGUOUS_BOOL = re.compile(r"^(?:y|n|yes|no|true|false|on|off)$", re.IGNORECASE)


class _SafeDumper(yaml.SafeDumper):
    """SafeDumper that quotes scalars other YAML implementations would coerce."""


def _represent_str(dumper: yaml.SafeDumper, value: str):
    style = "'" if _AMBIGUOUS_SCALAR.match(value) or _AMBIGUOUS_BOOL.match(value) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_SafeDumper.add_representer(str, _represent_str)


def _dump_yaml(data: dict) -> str:
    return yaml.dump(
        data,
        Dumper=_SafeDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )


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
