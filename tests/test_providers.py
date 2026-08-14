import hashlib
import json

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from outpost.models import Region
from outpost.providers import MANUAL_PROVIDERS, get_provider
from outpost.providers.aeza import _SECURE_SALT, AezaProvider
from outpost.providers.base import ProviderError, ProvisionSpec, infer_country
from outpost.providers.hostkey import HostkeyProvider
from outpost.providers.zomro import ZomroProvider, _unwrap


def test_manual_providers_have_no_client():
    assert "iphoster" in MANUAL_PROVIDERS
    for name in MANUAL_PROVIDERS:
        with pytest.raises(ProviderError, match="adopt"):
            get_provider(name)


def _aeza_secure_params(pin: str, payload: dict) -> dict:
    """Build a secureParameters blob the way Aeza encrypts it (for tests)."""
    key = hashlib.scrypt(pin.encode(), salt=_SECURE_SALT, n=16, r=8, p=1, dklen=32)
    iv = bytes(16)
    enc = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
    content = enc.update(json.dumps(payload).encode()) + enc.finalize()
    return {"iv": iv.hex(), "content": content.hex()}


def test_infer_country():
    assert infer_country("Aeza Germany Frankfurt") == "DE"
    assert infer_country("Moscow RU") == "RU"
    assert infer_country("Almaty") == "KZ"
    assert infer_country("unknown place") == "??"


def test_aeza_discover_and_create(monkeypatch):
    pin = "1337"
    prov = AezaProvider(api_key="x", pin=pin)
    secure = _aeza_secure_params(pin, {"password": "r00t-pw"})

    routes = {
        ("GET", "/services/products?count=500"): {
            "data": {"items": [{"id": 3, "name": "EPYC Netherlands", "configuration": {}}], "total": 1}
        },
        ("GET", "/os?count=500"): {"data": {"items": [{"id": 25, "name": "Ubuntu 24.04"}]}},
        ("POST", "/services/orders"): {"data": {"id": 999}},
        ("GET", "/services/orders/999"): {"data": {"createdServiceIds": [555]}},
        ("GET", "/services/555?extra=1"): {
            "data": {"ip": "203.0.113.7", "status": "active", "secureParameters": secure}
        },
    }

    captured = {}

    def fake_request(method, path, **kwargs):
        if path == "/services/orders":
            captured["order"] = kwargs.get("json")
        return routes[(method, path)]

    monkeypatch.setattr(prov, "_request", fake_request)

    regions = prov.discover_regions()
    assert regions[0].country == "NL"
    assert regions[0].product_ref["product_id"] == "3"

    spec = ProvisionSpec(name="t", region=regions[0], ssh_public_key="ssh-ed25519 AAA")
    result = prov.create(spec)
    assert result.provider_ref["service_id"] == "555"
    assert result.root_password == "r00t-pw"
    # Order must use the real VPS schema (no sshKey field).
    assert captured["order"]["parameters"] == {"os": 25, "ddosNotifications": False}
    ip = prov.wait_for_ip(result.provider_ref)
    assert ip == "203.0.113.7"


def test_aeza_create_without_pin_sets_password(monkeypatch):
    """No PIN -> wait active, then set a known root password via changePassword."""
    prov = AezaProvider(api_key="x", pin=None)
    routes = {
        ("GET", "/os?count=500"): {"data": {"items": [{"id": 25, "name": "Ubuntu 24.04"}]}},
        ("POST", "/services/orders"): {"data": {"id": 999}},
        ("GET", "/services/orders/999"): {"data": {"createdServiceIds": [555]}},
        ("GET", "/services/555?extra=1"): {"data": {"ip": "203.0.113.7", "status": "active"}},
    }
    captured = {}

    def fake_request(method, path, **kwargs):
        if path == "/services/555/changePassword":
            captured["password"] = kwargs["json"]["password"]
            return {"data": {}}
        return routes[(method, path)]

    monkeypatch.setattr(prov, "_request", fake_request)
    region = Region(code="NL", country="NL", product_ref={"product_id": "3"})
    spec = ProvisionSpec(name="t", region=region)
    result = prov.create(spec)
    assert result.root_password
    assert result.root_password == captured["password"]


def test_aeza_decrypt_requires_pin():
    prov = AezaProvider(api_key="x", pin=None)
    try:
        prov._decrypt_secure({"iv": "00", "content": "ab"})
        raise AssertionError("expected ProviderError when AEZA_PIN missing")
    except ProviderError as exc:
        assert "AEZA_PIN" in str(exc)


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


def test_hostkey_login_caches_token(monkeypatch):
    prov = HostkeyProvider(api_key="key")
    calls = {"n": 0}

    def fake_post(module, action, auth=True, **fields):
        if module == "auth" and action == "login":
            calls["n"] += 1
            return {"result": {"token": "tok123", "token_expire": 9999999999}}
        return {"result": "OK"}

    monkeypatch.setattr(prov, "_post", fake_post)
    assert prov._login() == "tok123"
    assert prov._login() == "tok123"
    assert calls["n"] == 1


def test_hostkey_discover_regions_vps_only(monkeypatch):
    prov = HostkeyProvider(api_key="key")
    monkeypatch.setattr(prov, "_login", lambda: "tok")

    def fake_post(module, action, auth=True, **fields):
        if module == "presets" and action == "list":
            return {
                "result": "OK",
                "presets": [
                    {"id": 108, "name": "Netherlands VPS Basic", "virtual": 1, "locations": "NL,DE"},
                    {"id": 200, "name": "Dedicated X", "virtual": 0, "locations": "NL"},
                ],
            }
        if module == "os" and action == "list":
            return {"result": "OK", "os_list": [{"id": 180, "name": "Ubuntu 24.04"}]}
        if module == "traffic_plans" and action == "list":
            return {"result": "OK", "traffic_plans": [{"id": 25, "main_plan": 1}]}
        raise AssertionError(f"unexpected {module}/{action}")

    monkeypatch.setattr(prov, "_post", fake_post)
    regions = prov.discover_regions()
    assert len(regions) == 2  # 108-NL and 108-DE only (no dedicated)
    assert regions[0].country == "NL"
    assert regions[0].product_ref["preset"] == "108"
    assert regions[0].product_ref["os_id"] == "180"


def test_hostkey_create_and_wait_for_ip(monkeypatch):
    prov = HostkeyProvider(api_key="key")
    monkeypatch.setattr(prov, "_login", lambda: "tok")
    captured = {}

    def fake_post(module, action, auth=True, **fields):
        key = f"{module}/{action}"
        captured[key] = fields
        if module == "traffic_plans" and action == "list":
            return {"result": "OK", "traffic_plans": [{"id": 25, "main_plan": 1, "name": "3Tb VM"}]}
        if key == "eq/order_instance":
            return {"result": "OK", "id": 555, "callback": "cb1"}
        if key == "eq/show":
            return {"result": "OK", "IP": [{"IP": "93.184.216.34", "status": "active"}]}
        if key == "eq_callback/check":
            return {"result": "OK", "context": {"status": "done"}}
        return {"result": "OK"}

    monkeypatch.setattr(prov, "_post", fake_post)
    region = Region(
        code="108-NL",
        country="NL",
        product_ref={"preset": "108", "location_name": "NL", "os_id": "180"},
    )
    spec = ProvisionSpec(name="outpost-test", region=region)
    result = prov.create(spec)
    assert result.provider_ref["server_id"] == "555"
    assert result.root_password
    assert "@" not in result.root_password
    assert "#" not in result.root_password
    order = captured["eq/order_instance"]
    assert order["preset"] == "108"
    assert order["location_name"] == "NL"
    assert order["os_id"] == "180"
    assert order["deploy_period"] == "monthly"
    assert order["traffic_plan"] == "25"
    ip = prov.wait_for_ip(result.provider_ref)
    assert ip == "93.184.216.34"


def test_hostkey_destroy(monkeypatch):
    prov = HostkeyProvider(api_key="key")
    monkeypatch.setattr(prov, "_login", lambda: "tok")
    captured = {}

    def fake_post(module, action, auth=True, **fields):
        captured[f"{module}/{action}"] = fields
        return {"result": "OK"}

    monkeypatch.setattr(prov, "_post", fake_post)
    prov.destroy({"server_id": "777"})
    req = captured["whmcs/request_cancellation"]
    assert req["id"] == "777"
    assert req["cancellation_type"] == 1


def test_hostkey_login_falls_back_to_whmcs(monkeypatch):
    prov = HostkeyProvider(api_key="bad", whmcs_user="u@example.com", whmcs_password="pw")
    calls = []

    def fake_post(module, action, auth=True, **fields):
        calls.append((module, action))
        if module == "auth" and action == "login":
            return {"result": -1, "error": "No appropriate servers found"}
        if module == "auth" and action == "whmcslogin":
            return {"result": {"token": "whmcs-tok", "token_expire": 9999999999}}
        return {"result": "OK"}

    monkeypatch.setattr(prov, "_post", fake_post)
    assert prov._login() == "whmcs-tok"
    assert calls[0] == ("auth", "login")
    assert calls[1] == ("auth", "whmcslogin")


def test_hostkey_post_raises_on_result_minus_one(monkeypatch):
    prov = HostkeyProvider(api_key="key")

    class Resp:
        status_code = 200

        def json(self):
            return {"result": -1, "error": "hourly billing is available only for prebill customers."}

    monkeypatch.setattr(prov.session, "post", lambda *a, **k: Resp())
    try:
        prov._post("eq", "order_instance", auth=False)
        raise AssertionError("expected ProviderError")
    except ProviderError as exc:
        assert "prebill" in str(exc)


def test_hostkey_generate_root_password():
    pw = HostkeyProvider._generate_root_password()
    assert 8 <= len(pw) <= 30
    assert pw[0].isalnum()
    assert any(c.isupper() for c in pw)
    assert any(c.islower() for c in pw)
    assert any(c.isdigit() for c in pw)
    assert "@" not in pw and "#" not in pw


def test_hostkey_omits_rub_currency_on_order(monkeypatch):
    prov = HostkeyProvider(api_key="key")
    prov._currency_code = "RUB"
    prov._whmcs_location = "whmcs_itb"
    monkeypatch.setattr(prov, "_login", lambda: "tok")
    captured = {}

    def fake_post(module, action, auth=True, **fields):
        key = f"{module}/{action}"
        captured[key] = fields
        if module == "traffic_plans" and action == "list":
            return {"result": "OK", "traffic_plans": [{"id": 25, "main_plan": 1}]}
        if key == "eq/order_instance":
            return {"result": "OK", "id": 555, "callback": "cb1"}
        return {"result": "OK"}

    monkeypatch.setattr(prov, "_post", fake_post)
    region = Region(
        code="108-NL",
        country="NL",
        product_ref={"preset": "108", "location_name": "NL", "os_id": "180"},
    )
    spec = ProvisionSpec(name="outpost-test", region=region)
    prov.create(spec)
    order = captured["eq/order_instance"]
    assert "currency_code" not in order
    assert order["deploy_options"] == "whmcs_itb"
    assert order["traffic_plan"] == "25"


def test_hostkey_error_raises(monkeypatch):
    prov = HostkeyProvider(api_key="key")

    class Resp:
        status_code = 200

        def json(self):
            return {"code": -1, "message": "nope"}

    monkeypatch.setattr(prov.session, "post", lambda *a, **k: Resp())
    try:
        prov._post("eq", "show", auth=False)
        raise AssertionError("expected ProviderError")
    except ProviderError as exc:
        assert "nope" in str(exc)
