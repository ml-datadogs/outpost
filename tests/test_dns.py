import pytest
from outpost import dns as dns_mod
from outpost.config import Settings
from outpost.dns import CloudflareDNS, DNSError, assign_hostname, hostname_for, release_hostname

ZONE = "dirtyinfra.xyz"


def _settings(**kw):
    return Settings(cloudflare_api_token="tok", dns_zone=ZONE, **kw)


class FakeCF:
    """Stand-in for the Cloudflare API: records keyed by fqdn."""

    def __init__(self, records=None):
        self.records = dict(records or {})
        self.calls = []

    def upsert_a(self, fqdn, ip, ttl=60):
        self.calls.append(("upsert", fqdn, ip))
        self.records[fqdn] = ip
        return {"name": fqdn, "content": ip, "proxied": False}

    def delete(self, fqdn):
        self.calls.append(("delete", fqdn))
        return self.records.pop(fqdn, None) is not None


def test_hostname_is_per_node(node):
    node.id = "4bd1b6"
    assert hostname_for(node, ZONE) == "exit-4bd1b6.dirtyinfra.xyz"
    assert hostname_for(node, ZONE, prefix="node") == "node-4bd1b6.dirtyinfra.xyz"


def test_assign_sets_real_tls_identity(monkeypatch, node):
    fake = FakeCF()
    monkeypatch.setattr(dns_mod, "client_for", lambda settings: fake)
    node.ip = "203.0.113.9"

    fqdn = assign_hostname(node, settings=_settings())

    assert fqdn == f"exit-{node.id}.{ZONE}"
    assert node.tls_domain == fqdn and node.sni == fqdn
    assert node.insecure is False  # real cert -> no skip-verify, no pin needed
    assert fake.calls == [("upsert", fqdn, "203.0.113.9")]


def test_assign_without_zone_falls_back_to_global_domain(node):
    node.tls_domain = None
    settings = Settings(tls_domain="exit.example.com")  # no zone/token configured
    assert assign_hostname(node, settings=settings) == "exit.example.com"


def test_assign_requires_ip(monkeypatch, node):
    monkeypatch.setattr(dns_mod, "client_for", lambda settings: FakeCF())
    node.ip = None
    with pytest.raises(DNSError, match="no IP"):
        assign_hostname(node, settings=_settings())


def test_release_deletes_managed_record(monkeypatch, node):
    fqdn = f"exit-{node.id}.{ZONE}"
    fake = FakeCF({fqdn: "203.0.113.9"})
    monkeypatch.setattr(dns_mod, "client_for", lambda settings: fake)
    node.tls_domain = fqdn

    assert release_hostname(node, settings=_settings()) is True
    assert fake.records == {}


def test_release_leaves_foreign_domains_alone(monkeypatch, node):
    """A hand-set domain outside our zone must never be deleted."""
    fake = FakeCF({"exit.mlshitcheatsheet.ru": "1.2.3.4"})
    monkeypatch.setattr(dns_mod, "client_for", lambda settings: fake)
    node.tls_domain = "exit.mlshitcheatsheet.ru"

    assert release_hostname(node, settings=_settings()) is False
    assert fake.calls == []


def test_client_for_is_opt_in():
    assert dns_mod.client_for(Settings()) is None
    assert dns_mod.client_for(Settings(dns_zone=ZONE)) is None  # token missing
    assert dns_mod.client_for(_settings()) is not None


def test_upsert_reuses_correct_existing_record(monkeypatch):
    """No needless PUT when the record already points where we want."""
    cf = CloudflareDNS(token="t", zone=ZONE)
    cf._zone_id = "zid"
    existing = {"id": "rid", "content": "203.0.113.9", "proxied": False}
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path))
        if method == "GET" and path.endswith("/dns_records"):
            return {"result": [existing], "success": True}
        raise AssertionError(f"unexpected {method} {path}")

    monkeypatch.setattr(cf, "_request", fake_request)
    assert cf.upsert_a(f"exit-x.{ZONE}", "203.0.113.9") == existing
    assert all(m == "GET" for m, _ in calls)


def test_records_are_never_proxied(monkeypatch):
    cf = CloudflareDNS(token="t", zone=ZONE)
    cf._zone_id = "zid"
    sent = {}

    def fake_request(method, path, **kwargs):
        if method == "GET":
            return {"result": [], "success": True}
        sent.update(kwargs.get("json") or {})
        return {"result": {}, "success": True}

    monkeypatch.setattr(cf, "_request", fake_request)
    cf.upsert_a(f"exit-x.{ZONE}", "203.0.113.9")
    # Cloudflare's proxy is HTTP-only; proxying would break Hy2/Trojan/Reality.
    assert sent["proxied"] is False
    assert sent["type"] == "A"


def test_api_errors_surface_message(monkeypatch):
    cf = CloudflareDNS(token="t", zone=ZONE)

    class Resp:
        status_code = 403

        def json(self):
            return {"success": False, "errors": [{"message": "Invalid API Token"}]}

    monkeypatch.setattr(dns_mod.requests, "request", lambda *a, **k: Resp())
    with pytest.raises(DNSError, match="Invalid API Token"):
        cf.zone_id()
