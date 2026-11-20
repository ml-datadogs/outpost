"""The two additional listeners installed on opt-in Happ pilot nodes."""

from ..models import Node

XHTTP_PATH = "/outpost"
GECKO_PACKET_SIZE = "512-1200"


def hy2_mask(password: str, packet_size: str = "") -> dict:
    mask = {"password": password}
    if packet_size:
        mask["packetSize"] = packet_size
    return {"udp": [{"type": "salamander", "settings": mask}]}


def pilot_inbounds(node: Node) -> list:
    secrets = node.secrets
    if not secrets or not secrets.hysteria2_obfs_password:
        raise ValueError("Happ pilot needs node secrets and a Hysteria obfuscation password")
    certs = [{"certificateFile": "/etc/outpost-tls/cert.pem", "keyFile": "/etc/outpost-tls/key.pem"}]
    return [
        {
            "tag": "vless-xhttp",
            "port": node.ports.xhttp,
            "protocol": "vless",
            "settings": {"clients": [{"id": secrets.reality_uuid}], "decryption": "none"},
            "streamSettings": {
                "network": "xhttp",
                "security": "tls",
                "tlsSettings": {"alpn": ["h2"], "certificates": certs},
                "xhttpSettings": {"path": XHTTP_PATH, "mode": "auto"},
            },
        },
        {
            "tag": "hysteria-gecko",
            "port": node.ports.gecko,
            "protocol": "hysteria",
            "settings": {"version": 2, "clients": [{"auth": secrets.hysteria2_password}]},
            "streamSettings": {
                "network": "hysteria",
                "security": "tls",
                "tlsSettings": {"alpn": ["h3"], "certificates": certs},
                "hysteriaSettings": {"version": 2, "auth": secrets.hysteria2_password},
                "finalmask": hy2_mask(secrets.hysteria2_obfs_password, GECKO_PACKET_SIZE),
            },
        },
    ]
