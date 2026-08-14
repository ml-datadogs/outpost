"""Per-node DNS records on Cloudflare, so every node can hold a real TLS cert.

One global ``OUTPOST_TLS_DOMAIN`` can only ever be valid for a single node, which
forces every other node onto a self-signed cert. Instead each node gets its own
name under a managed zone (``exit-<id>.<zone>``) pointing at its IP, created before
bootstrap so certbot's HTTP-01 challenge succeeds, and removed when the node dies.

Records are always **unproxied** (grey cloud): Cloudflare's proxy only forwards
HTTP(S) and would break Hysteria2/Trojan/Reality, which need raw TCP/UDP.
"""

from __future__ import annotations

from typing import Optional

import requests

from .config import Settings
from .config import settings as default_settings
from .models import Node

API_BASE = "https://api.cloudflare.com/client/v4"
DEFAULT_PREFIX = "exit"
DEFAULT_TTL = 60


class DNSError(RuntimeError):
    pass


def hostname_for(node: Node, zone: str, prefix: str = DEFAULT_PREFIX) -> str:
    return f"{prefix}-{node.id}.{zone}"


class CloudflareDNS:
    def __init__(self, token: str, zone: str, timeout: int = 30):
        self.token = token
        self.zone = zone
        self.timeout = timeout
        self._zone_id: Optional[str] = None

    def _request(self, method: str, path: str, **kwargs) -> dict:
        resp = requests.request(
            method,
            f"{API_BASE}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
            **kwargs,
        )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise DNSError(f"cloudflare {method} {path}: non-JSON response ({resp.status_code})") from exc
        if not payload.get("success", False):
            errors = payload.get("errors") or [{"message": resp.text}]
            msg = "; ".join(str(e.get("message", e)) for e in errors)
            raise DNSError(f"cloudflare {method} {path}: {msg}")
        return payload

    def zone_id(self) -> str:
        if self._zone_id is None:
            result = self._request("GET", "/zones", params={"name": self.zone})["result"]
            if not result:
                raise DNSError(f"zone {self.zone} not found (check the token's zone access)")
            self._zone_id = result[0]["id"]
        return self._zone_id

    def find_record(self, fqdn: str) -> Optional[dict]:
        result = self._request(
            "GET", f"/zones/{self.zone_id()}/dns_records", params={"type": "A", "name": fqdn}
        )["result"]
        return result[0] if result else None

    def upsert_a(self, fqdn: str, ip: str, ttl: int = DEFAULT_TTL) -> dict:
        """Point ``fqdn`` at ``ip``. Never proxied - see module docstring."""
        body = {"type": "A", "name": fqdn, "content": ip, "ttl": ttl, "proxied": False}
        existing = self.find_record(fqdn)
        if existing:
            if existing.get("content") == ip and existing.get("proxied") is False:
                return existing
            return self._request("PUT", f"/zones/{self.zone_id()}/dns_records/{existing['id']}", json=body)[
                "result"
            ]
        return self._request("POST", f"/zones/{self.zone_id()}/dns_records", json=body)["result"]

    def delete(self, fqdn: str) -> bool:
        """Remove the record; returns False when there was nothing to remove."""
        existing = self.find_record(fqdn)
        if not existing:
            return False
        self._request("DELETE", f"/zones/{self.zone_id()}/dns_records/{existing['id']}")
        return True


def client_for(settings: Settings = default_settings) -> Optional[CloudflareDNS]:
    """A DNS client when the zone + token are configured, else None (opt-in feature)."""
    if not settings.dns_zone or not settings.cloudflare_api_token:
        return None
    return CloudflareDNS(token=settings.cloudflare_api_token, zone=settings.dns_zone)


def assign_hostname(node: Node, settings: Settings = default_settings) -> Optional[str]:
    """Give the node its own name + real-TLS identity. Returns the FQDN, or None.

    Falls back to the legacy global ``tls_domain`` when no zone is configured, and
    leaves the node on a self-signed cert when neither is set.
    """
    dns = client_for(settings)
    if dns is None:
        return node.tls_domain or settings.tls_domain
    if not node.ip:
        raise DNSError(f"node {node.id} has no IP yet; cannot create a DNS record")
    fqdn = hostname_for(node, settings.dns_zone or "", settings.dns_prefix)
    dns.upsert_a(fqdn, node.ip)
    node.tls_domain = fqdn
    node.sni = fqdn
    node.insecure = False
    return fqdn


def release_hostname(node: Node, settings: Settings = default_settings) -> bool:
    """Drop the node's record. Best-effort: a missing record is not an error."""
    dns = client_for(settings)
    if dns is None or not node.tls_domain:
        return False
    if settings.dns_zone and not node.tls_domain.endswith(f".{settings.dns_zone}"):
        return False  # a hand-set domain we do not manage
    return dns.delete(node.tls_domain)
