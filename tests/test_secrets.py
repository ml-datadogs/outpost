import base64

from outpost.secrets_gen import generate_node_secrets, generate_reality_keypair


def _is_b64url_raw(s: str) -> bool:
    pad = "=" * (-len(s) % 4)
    try:
        base64.urlsafe_b64decode(s + pad)
        return True
    except Exception:
        return False


def test_reality_keypair_distinct_and_decodable():
    priv, pub = generate_reality_keypair()
    assert priv != pub
    assert _is_b64url_raw(priv) and _is_b64url_raw(pub)
    # x25519 raw keys are 32 bytes
    assert len(base64.urlsafe_b64decode(priv + "=" * (-len(priv) % 4))) == 32
    assert len(base64.urlsafe_b64decode(pub + "=" * (-len(pub) % 4))) == 32


def test_node_secrets_unique():
    a = generate_node_secrets()
    b = generate_node_secrets()
    assert a.hysteria2_password != b.hysteria2_password
    assert a.reality_uuid != b.reality_uuid
    assert len(a.reality_short_id) == 8
