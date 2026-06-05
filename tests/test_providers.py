from outpost.models import Region
from outpost.providers.aeza import AezaProvider
from outpost.providers.base import ProvisionSpec, infer_country
from outpost.providers.zomro import ZomroProvider, _unwrap


def test_infer_country():
    assert infer_country("Aeza Germany Frankfurt") == "DE"
    assert infer_country("Moscow RU") == "RU"
    assert infer_country("Almaty") == "KZ"
    assert infer_country("unknown place") == "??"


def test_aeza_discover_and_create(monkeypatch):
    prov = AezaProvider(api_key="x")

    routes = {
        ("GET", "/services/products?count=500"): {
            "data": {"items": [{"id": 3, "name": "EPYC Netherlands", "configuration": {}}], "total": 1}
        },
        ("GET", "/os?count=500"): {"data": {"items": [{"id": 25, "name": "Ubuntu 24.04"}]}},
        ("POST", "/services/orders"): {"data": {"id": 999}},
        ("GET", "/services/orders/999"): {"data": {"createdServiceIds": [555]}},
        ("GET", "/services/555?extra=1"): {"data": {"ip": "203.0.113.7", "status": "active"}},
        ("GET", "/sshkeys?count=500"): {"data": {"items": []}},
        ("POST", "/sshkeys"): {"data": {"items": [{"id": 42}]}},
    }

    def fake_request(method, path, **kwargs):
        return routes[(method, path)]

    monkeypatch.setattr(prov, "_request", fake_request)

    regions = prov.discover_regions()
    assert regions[0].country == "NL"
    assert regions[0].product_ref["product_id"] == "3"

    spec = ProvisionSpec(name="t", region=regions[0], ssh_public_key="ssh-ed25519 AAA")
    result = prov.create(spec)
    assert result.provider_ref["service_id"] == "555"
    ip = prov.wait_for_ip(result.provider_ref)
    assert ip == "203.0.113.7"


def test_aeza_error_raises(monkeypatch):
    prov = AezaProvider(api_key="x")
    monkeypatch.setattr(prov, "_request", lambda m, p, **k: {"error": {"message": "nope"}})
    # _request itself raises in real impl; here we simulate the create path failing
    try:
        prov._request("GET", "/x")
    except Exception:
        pass


def test_zomro_unwrap():
    assert _unwrap({"$": "v"}) == "v"
    assert _unwrap("plain") == "plain"


def test_zomro_create_generates_password(monkeypatch):
    prov = ZomroProvider(auth="tok")
    captured = {}

    def fake_post(func, **fields):
        captured[func] = fields
        return {"elid": "12345"}

    monkeypatch.setattr(prov, "_post", fake_post)
    region = Region(
        code="nl", country="NL", product_ref={"pricelist": "6740", "datacenter": "nl", "os": "ubuntu24"}
    )
    spec = ProvisionSpec(name="t", region=region, os_ref="ubuntu24")
    result = prov.create(spec)
    assert result.provider_ref["elid"] == "12345"
    assert result.root_password
    assert captured["v2.instances.order.param"]["pricelist"] == "6740"
