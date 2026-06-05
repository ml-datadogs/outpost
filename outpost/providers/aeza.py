"""Aeza provider client.

API: https://my.aeza.net/api  (auth header: ``X-API-Key: <token>``)
List endpoints return ``{"data": {"items": [...], "total": N}}``; single-entity
endpoints return ``{"data": {...}}``. Errors come back as ``{"error": {...}}``.

Verified routes (from Aeza dev-docs):
  GET    /services/products          -> tariffs (per location)
  GET    /os                         -> OS images
  POST   /services/orders            -> place order
  GET    /services/orders/{id}       -> order status (createdServiceIds)
  GET    /services/{id}              -> service detail (ip, status)
  POST   /services/{id}/ctl          -> {action: suspend|resume|reboot}
  DELETE /services/{id}              -> destroy (no refund)
  GET/POST /sshkeys                  -> list / add ssh key
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

import requests

from ..models import Region
from .base import BaseProvider, ProviderError, ProvisionResult, ProvisionSpec, infer_country

DEFAULT_BASE_URL = "https://my.aeza.net/api"
PREFERRED_OS = ["ubuntu 24", "ubuntu 22", "debian 12", "ubuntu", "debian"]


class AezaProvider(BaseProvider):
    name = "aeza"

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout: int = 30):
        self.api_key = api_key
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
        existing = self._items(self._request("GET", "/sshkeys?count=500"))
        for item in existing:
            if item.get("pubKey", "").strip() == public_key.strip() or item.get("name") == name:
                return str(item.get("id", name))
        body = self._request("POST", "/sshkeys", json={"name": name, "pubKey": public_key})
        data = self._data(body)
        item = data.get("items", [data])[0] if isinstance(data, dict) else data
        return str(item.get("id", name))

    # --- lifecycle ---------------------------------------------------------
    def create(self, spec: ProvisionSpec) -> ProvisionResult:
        product_id = spec.region.product_ref.get("product_id")
        if not product_id:
            raise ProviderError(f"region {spec.region.code} has no product_id; run discovery first")

        os_id = spec.os_ref or self.find_os_id()
        if os_id is None:
            raise ProviderError("could not determine an OS id for the order")

        parameters: Dict = {"os": int(os_id)}
        if spec.ssh_public_key:
            key_id = self.ensure_ssh_key(spec.ssh_key_name, spec.ssh_public_key)
            # Aeza accepts ssh key id(s) in order parameters.
            parameters["sshKey"] = key_id
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
        order_id = order.get("id")
        service_id = self._await_service_id(order_id)
        return ProvisionResult(
            provider_ref={"service_id": str(service_id), "order_id": str(order_id)},
            raw=order,
        )

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
