"""Generation of per-node protocol secrets (passwords, UUIDs, Reality keypair)."""

from __future__ import annotations

import base64
import secrets
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from .models import NodeSecrets


def _b64_raw_urlsafe(raw: bytes) -> str:
    """Xray/sing-box Reality keys are base64 RawURLEncoding (no padding)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_reality_keypair() -> tuple[str, str]:
    """Return (private_key, public_key) base64-url encoded, matching sing-box."""
    priv = X25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _b64_raw_urlsafe(priv_raw), _b64_raw_urlsafe(pub_raw)


def generate_short_id() -> str:
    """Reality short_id: hex string, 2-16 hex chars. Use 8 (4 bytes)."""
    return secrets.token_hex(4)


def generate_password() -> str:
    return secrets.token_urlsafe(18)


def generate_node_secrets() -> NodeSecrets:
    priv, pub = generate_reality_keypair()
    return NodeSecrets(
        hysteria2_password=generate_password(),
        trojan_password=generate_password(),
        reality_uuid=str(uuid.uuid4()),
        reality_private_key=priv,
        reality_public_key=pub,
        reality_short_id=generate_short_id(),
    )
