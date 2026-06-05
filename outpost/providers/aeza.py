"""Aeza provider client.

API: https://my.aeza.net/api  (auth header: ``X-API-Key: <token>``)
List endpoints return ``{"data": {"items": [...], "total": N}}``; single-entity
endpoints return ``{"data": {...}}``. Errors come back as ``{"error": {...}}``.

Verified routes (live API, 2026):
  GET    /services/products          -> tariffs (per location)
  GET    /os                         -> OS images
  POST   /services/orders            -> place order
  GET    /services/orders/{id}       -> order status (createdServiceIds)
  GET    /services/{id}              -> service detail (ip, status, secureParameters)
  POST   /services/{id}/ctl          -> {action: suspend|resume|reboot}
  DELETE /services/{id}              -> destroy (no refund)

There is NO SSH-key API on Aeza: the VPS order schema only accepts
``{os, recipe, isoUrl, ddosNotifications}`` (see GET /services/types ->
vps.computedParameters). Instead Aeza auto-generates a root password and stores
it in the service's ``secureParameters`` (AES-256-CTR, key derived from the
account PIN). We decrypt it and hand it to the SSH bootstrapper, which logs in
by password and installs our public key for subsequent runs.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Dict, List, Optional

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..models import Region
from .base import BaseProvider, ProviderError, ProvisionResult, ProvisionSpec, infer_country

DEFAULT_BASE_URL = "https://my.aeza.net/api"
PREFERRED_OS = ["ubuntu 24", "ubuntu 22", "debian 12", "ubuntu", "debian"]

# Aeza derives the secureParameters key as scrypt(pin, "rabbit-billing", N=16, r=8, p=1, 32).
_SECURE_SALT = b"rabbit-billing"


class AezaProvider(BaseProvider):
    name = "aeza"

    def __init__(
        self,
        api_key: str,
        pin: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.pin = pin
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key, "Accept": "application/json"})

    # --- low level ---------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError(f"aeza {method} {path}: non-JSON response ({resp.status_code})") from exc
        if isinstance(body, dict) and body.get("error"):
            err = body["error"]
            msg = err.get("message") if isinstance(err, dict) else err
            raise ProviderError(f"aeza {method} {path}: {msg}")
        if resp.status_code >= 400:
            raise ProviderError(f"aeza {method} {path}: HTTP {resp.status_code}")
        return body if isinstance(body, dict) else {"data": body}

    @staticmethod
    def _data(body: dict) -> dict:
        return body.get("data", body) if isinstance(body, dict) else {}

    def _items(self, body: dict) -> List[dict]:
        data = self._data(body)
        if isinstance(data, dict) and "items" in data:
            return data["items"] or []
        if isinstance(data, list):
            return data
        return []

    # --- discovery ---------------------------------------------------------
    def list_products(self) -> List[dict]:
        return self._items(self._request("GET", "/services/products?count=500"))

    def list_os(self) -> List[dict]:
        return self._items(self._request("GET", "/os?count=500"))

    def _location_text(self, product: dict) -> str:
        # Aeza products carry their location in a few possible places.
        parts = [str(product.get("name", ""))]
        for key in ("group", "location", "country", "city"):
            val = product.get(key)
            if isinstance(val, dict):
                parts.append(str(val.get("name", "")))
            elif val:
                parts.append(str(val))
        cfg = product.get("configuration")
        if isinstance(cfg, dict):
            parts.append(str(cfg.get("location", "")))
        return " ".join(parts)

    def discover_regions(self) -> List[Region]:
        regions: List[Region] = []
        for prod in self.list_products():
            text = self._location_text(prod)
            country = infer_country(text)
            pid = prod.get("id")
            if pid is None:
                continue
            regions.append(
                Region(
                    code=str(prod.get("name", pid)),
                    country=country,
                    city=text or None,
                    product_ref={"product_id": str(pid)},
                )
            )
        return regions

    def find_os_id(self, preferred: Optional[List[str]] = None) -> Optional[int]:
        preferred = preferred or PREFERRED_OS
        os_items = self.list_os()
        for want in preferred:
            for item in os_items:
                name = str(item.get("name", "")).lower()
                if want in name:
                    return int(item["id"])
        if os_items:
            return int(os_items[0]["id"])
        return None

    # --- ssh keys ----------------------------------------------------------
    def ensure_ssh_key(self, name: str, public_key: str) -> str:
        # Aeza has no SSH-key API; the VPS comes up with a root password instead.
        # The bootstrapper logs in by password and installs our key. No-op here.
        return name

    # --- lifecycle ---------------------------------------------------------
    def create(self, spec: ProvisionSpec) -> ProvisionResult:
        product_id = spec.region.product_ref.get("product_id")
        if not product_id:
            raise ProviderError(f"region {spec.region.code} has no product_id; run discovery first")

        os_id = spec.os_ref or self.find_os_id()
        if os_id is None:
            raise ProviderError("could not determine an OS id for the order")

        # VPS order schema (GET /services/types -> vps.computedParameters):
        # only os / recipe / isoUrl / ddosNotifications are accepted.
        parameters: Dict = {"os": int(os_id), "ddosNotifications": False}
        parameters.update(spec.extra_parameters)

        order_body = self._request(
            "POST",
            "/services/orders",
            json={
                "count": 1,
                "term": spec.term,
                "name": spec.name,
                "productId": int(product_id),
                "parameters": parameters,
                "autoProlong": spec.auto_prolong,
                "method": "balance",
            },
        )
        order = self._data(order_body)
        order_id, service_id = self._resolve_order(order, spec.name)

        # Aeza gives no SSH key + no order-time password. Two ways to get root:
        #   - PIN set:  decrypt the auto-generated password from secureParameters.
        #   - no PIN:   wait for install, then set a known password via changePassword.
        if self.pin:
            root_password = self._fetch_root_password(service_id)
        else:
            root_password = self._set_known_password(service_id)
        return ProvisionResult(
            provider_ref={"service_id": str(service_id), "order_id": str(order_id or "")},
            root_password=root_password,
            raw=order,
        )

    def _resolve_order(self, order: dict, name: str) -> tuple[Optional[str], str]:
        """Find the created service id from the order response (shape varies).

        Aeza's order POST response does not reliably expose an order id, so we fall
        back to matching the freshly created service by the unique name we set.
        """
        if isinstance(order, dict):
            ids = order.get("createdServiceIds")
            if ids:
                return None, str(ids[0])
            order_id = order.get("id") or order.get("orderId")
            nested = order.get("order")
            if not order_id and isinstance(nested, dict):
                order_id = nested.get("id")
            if order_id:
                return str(order_id), self._await_service_id(order_id)
        return None, self._await_service_by_name(name)

    def _await_service_by_name(self, name: str, timeout: int = 180, interval: int = 5) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for item in self._items(self._request("GET", "/services?count=500")):
                if item.get("name") == name and item.get("id") is not None:
                    return str(item["id"])
            time.sleep(interval)
        raise ProviderError(f"aeza order for '{name}' produced no service within {timeout}s")

    def _wait_active(self, service_id, timeout: int = 420, interval: int = 8) -> None:
        """Block until the service finishes installing (so a password set sticks)."""
        installing = {"creating", "pending", "queued", "installing", "deploying", ""}
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self._data(self._request("GET", f"/services/{service_id}?extra=1"))
            status = (data.get("currentStatus") or data.get("status") or "").lower()
            if status and status not in installing:
                return
            time.sleep(interval)
        # Don't hard-fail: proceed and let the password set / SSH wait surface issues.

    def _set_known_password(self, service_id) -> str:
        """Set a strong root password we control (PIN-free path)."""
        self._wait_active(service_id)
        password = secrets.token_urlsafe(18)
        last_err: Optional[Exception] = None
        for _ in range(5):
            try:
                self._request("PUT", f"/services/{service_id}/changePassword", json={"password": password})
                return password
            except ProviderError as exc:  # service may still be settling
                last_err = exc
                time.sleep(8)
        raise ProviderError(f"aeza changePassword failed for service {service_id}: {last_err}")

    # --- root password (decrypt secureParameters) --------------------------
    def _decrypt_secure(self, secure: dict) -> Optional[dict]:
        """Decrypt Aeza secureParameters (AES-256-CTR; key = scrypt(PIN, salt))."""
        if not self.pin:
            raise ProviderError(
                "AEZA_PIN is not set; cannot decrypt the VPS root password from "
                "secureParameters. Set AEZA_PIN in .env (Aeza panel -> Settings -> Security)."
            )
        iv_hex = secure.get("iv")
        content_hex = secure.get("content")
        if not iv_hex or not content_hex:
            return None
        key = hashlib.scrypt(self.pin.encode(), salt=_SECURE_SALT, n=16, r=8, p=1, dklen=32)
        cipher = Cipher(algorithms.AES(key), modes.CTR(bytes.fromhex(iv_hex)))
        dec = cipher.decryptor()
        plain = dec.update(bytes.fromhex(content_hex)) + dec.finalize()
        try:
            return json.loads(plain.decode())
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderError("decrypted secureParameters is not valid JSON; is AEZA_PIN correct?") from exc

    @staticmethod
    def _find_password(payload: dict) -> Optional[str]:
        for key in ("password", "rootPassword", "root_password", "pass"):
            val = payload.get(key)
            if val:
                return str(val)
        # Fall back to any key that looks like a password.
        for k, v in payload.items():
            if "pass" in k.lower() and v:
                return str(v)
        return None

    def _fetch_root_password(self, service_id, timeout: int = 180, interval: int = 6) -> Optional[str]:
        deadline = time.time() + timeout
        last_secure: Optional[dict] = None
        while time.time() < deadline:
            data = self._data(self._request("GET", f"/services/{service_id}?extra=1"))
            secure = data.get("secureParameters")
            if isinstance(secure, dict) and secure.get("content"):
                last_secure = secure
                payload = self._decrypt_secure(secure)
                if payload:
                    pw = self._find_password(payload)
                    if pw:
                        return pw
            time.sleep(interval)
        if last_secure is not None:
            raise ProviderError(
                f"aeza service {service_id}: secureParameters present but no password "
                f"field found (keys seen after decrypt). Check AEZA_PIN / payload shape."
            )
        raise ProviderError(f"aeza service {service_id} exposed no secureParameters within {timeout}s")

    def _await_service_id(self, order_id, timeout: int = 120, interval: int = 4) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            body = self._request("GET", f"/services/orders/{order_id}")
            data = self._data(body)
            ids = data.get("createdServiceIds") or []
            if ids:
                return str(ids[0])
            time.sleep(interval)
        raise ProviderError(f"aeza order {order_id} produced no service in {timeout}s")

    def wait_for_ip(self, provider_ref: Dict[str, str], timeout: int = 300, interval: int = 6) -> str:
        service_id = provider_ref["service_id"]
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self._data(self._request("GET", f"/services/{service_id}?extra=1"))
            ip = data.get("ip")
            status = (data.get("status") or data.get("currentStatus") or "").lower()
            if ip and status not in ("creating", "pending", "queued", ""):
                return str(ip)
            if ip:
                return str(ip)
            time.sleep(interval)
        raise ProviderError(f"aeza service {service_id} got no IP within {timeout}s")

    def destroy(self, provider_ref: Dict[str, str]) -> None:
        service_id = provider_ref.get("service_id")
        if not service_id:
            raise ProviderError("destroy requires provider_ref.service_id")
        self._request("DELETE", f"/services/{service_id}")
