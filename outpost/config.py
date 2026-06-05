"""Runtime configuration loaded from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv optional at runtime
    pass


def _expand(value: Optional[str]) -> Optional[Path]:
    if not value:
        return None
    return Path(os.path.expanduser(value))


PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent


@dataclass
class Settings:
    # Provider credentials
    aeza_api_key: Optional[str] = None
    aeza_pin: Optional[str] = None  # account PIN; decrypts Aeza secureParameters (root pw)
    zomro_auth: Optional[str] = None

    # SSH
    ssh_public_key_file: Optional[Path] = None
    ssh_private_key_file: Optional[Path] = None

    # Subscription distribution
    sub_token: Optional[str] = None
    tls_domain: Optional[str] = None

    # Encryption
    sops_age_key_file: Optional[Path] = None

    # Local probe network
    lan_gateway: Optional[str] = None
    lan_dns: Optional[str] = None

    # Paths
    state_dir: Path = REPO_ROOT / "state"
    server_dir: Path = REPO_ROOT / "server"
    dist_dir: Path = REPO_ROOT / "dist-subs"

    @classmethod
    def load(cls) -> Settings:
        return cls(
            aeza_api_key=os.getenv("AEZA_API_KEY") or None,
            aeza_pin=os.getenv("AEZA_PIN") or None,
            zomro_auth=os.getenv("ZOMRO_AUTH") or None,
            ssh_public_key_file=_expand(os.getenv("OUTPOST_SSH_PUBLIC_KEY_FILE")),
            ssh_private_key_file=_expand(os.getenv("OUTPOST_SSH_PRIVATE_KEY_FILE")),
            sub_token=os.getenv("OUTPOST_SUB_TOKEN") or None,
            tls_domain=os.getenv("OUTPOST_TLS_DOMAIN") or None,
            sops_age_key_file=_expand(os.getenv("SOPS_AGE_KEY_FILE")),
            lan_gateway=os.getenv("OUTPOST_LAN_GATEWAY") or None,
            lan_dns=os.getenv("OUTPOST_LAN_DNS") or None,
            state_dir=Path(os.getenv("OUTPOST_STATE_DIR", str(REPO_ROOT / "state"))),
            server_dir=Path(os.getenv("OUTPOST_SERVER_DIR", str(REPO_ROOT / "server"))),
            dist_dir=Path(os.getenv("OUTPOST_DIST_DIR", str(REPO_ROOT / "dist-subs"))),
        )

    @property
    def inventory_path(self) -> Path:
        """Preferred inventory file. Encrypted variant wins when present."""
        enc = self.state_dir / "inventory.enc.yaml"
        plain = self.state_dir / "inventory.yaml"
        if enc.exists():
            return enc
        return plain

    @property
    def registry_path(self) -> Path:
        return self.state_dir / "registry.yaml"


settings = Settings.load()
