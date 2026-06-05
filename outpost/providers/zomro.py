"""Zomro provider client.

API: https://api.zomro.com/  (BILLmanager-style: POST form-data, ``func=...``,
``auth=<session token>``, ``out=json``). List responses are wrapped in a top-level
``doc`` object; scalar values are often wrapped as ``{"$": "value"}``.

Documented funcs used:
  v2.instances.order.pricelist   -> available tariffs
  v2.instances.order.param       -> tariff params (datacenters, OS) AND order placement (with sok=ok)
  service.reboot                 -> reboot (elid)
  service.delete                 -> destroy (elid)

Because Zomro orders provision with a root *password* (no SSH-key upload via API),
``create`` generates a strong root password and returns it; the SSH bootstrap then
logs in with it, installs our key, and locks password auth.
"""

from __future__ import annotations

import secrets
import time
from typing import Dict, List, Optional

import requests

from ..models import Region
from .base import BaseProvider, ProviderError, ProvisionResult, ProvisionSpec, infer_country

DEFAULT_BASE_URL = "https://api.zomro.com/"


def _unwrap(value):
    """BILLmanager wraps scalars as {"$": "x"} and sometimes {"$id": "..."}."""
    if isinstance(value, dict):
        if "$" in value:
            return value["$"]
        return value
    return value


class ZomroProvider(BaseProvider):
    name = "zomro"

    def __init__(self, auth: str, base_url: str = DEFAULT_BASE_URL, timeout: int = 40):
        self.auth = auth
        self.base_url = base_url
        self.timeout = timeout
        self.session = requests.Session()

    # --- low level ---------------------------------------------------------
    def _post(self, func: str, **fields) -> dict:
        data = {"func": func, "auth": self.auth, "out": "json", "sok": "ok"}
        data.update({k: v for k, v in fields.items() if v is not None})
        resp = self.session.post(self.base_url, data=data, timeout=self.timeout)
        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError(f"zomro {func}: non-JSON response ({resp.status_code})") from exc
        doc = body.get("doc", body) if isinstance(body, dict) else {}
        if isinstance(doc, dict) and doc.get("error"):
            err = doc["error"]
            msg = _unwrap(err.get("msg")) if isinstance(err, dict) else err
            raise ProviderError(f"zomro {func}: {msg}")
        return doc

    @staticmethod
    def _rows(doc: dict, key: str = "elem") -> List[dict]:
        rows = doc.get(key)
        if rows is None and "list" in doc:
            lst = doc["list"]
            if isinstance(lst, list) and lst and isinstance(lst[0], dict):
                rows = lst[0].get("elem")
        if isinstance(rows, dict):
            return [rows]
        return rows or []

    # --- discovery ---------------------------------------------------------
    def list_products(self) -> List[dict]:
        doc = self._post("v2.instances.order.pricelist")
        return self._rows(doc)

    def list_os(self, pricelist: Optional[str] = None) -> List[dict]:
        params = self._post("v2.instances.order.param", pricelist=pricelist)
        # OS list typically under a 'slist' entry named 'instances_os'
        for sl in params.get("slist", []) if isinstance(params, dict) else []:
            if _unwrap(sl.get("name")) == "instances_os":
                return sl.get("val", [])
        return []

    def list_datacenters(self, pricelist: Optional[str] = None) -> List[dict]:
        params = self._post("v2.instances.order.param", pricelist=pricelist)
        for sl in params.get("slist", []) if isinstance(params, dict) else []:
            if _unwrap(sl.get("name")) == "datacenter":
                return sl.get("val", [])
        return []

    def discover_regions(self) -> List[Region]:
        regions: List[Region] = []
        products = self.list_products()
        pricelist_id = None
        if products:
            pricelist_id = str(_unwrap(products[0].get("id") or products[0].get("key")))
        for dc in self.list_datacenters(pricelist_id):
            label = str(_unwrap(dc.get("name") or dc.get("v") or ""))
            code = str(_unwrap(dc.get("key") or dc.get("$key") or label))
            regions.append(
                Region(
                    code=code,
                    country=infer_country(label),
                    city=label or None,
                    product_ref={"pricelist": pricelist_id or "", "datacenter": code},
                )
            )
        return regions

    # --- ssh keys (not supported via API; bootstrap installs the key) -------
    def ensure_ssh_key(self, name: str, public_key: str) -> str:
        return name

    # --- lifecycle ---------------------------------------------------------
    def create(self, spec: ProvisionSpec) -> ProvisionResult:
        pricelist = spec.region.product_ref.get("pricelist")
        datacenter = spec.region.product_ref.get("datacenter") or spec.region.code
        if not pricelist:
            raise ProviderError(f"region {spec.region.code} missing pricelist; run discovery first")

        os_uid = spec.os_ref or spec.region.product_ref.get("os")
        if not os_uid:
            raise ProviderError("zomro create requires an OS uid (spec.os_ref)")

        root_password = secrets.token_urlsafe(20)
        fields = {
            "pricelist": pricelist,
            "datacenter": datacenter,
            "instances_os": os_uid,
            "password": root_password,
            "instances_name": spec.name,
        }
        fields.update(spec.extra_parameters)
        doc = self._post("v2.instances.order.param", **fields)
        elid = str(_unwrap(doc.get("elid") or doc.get("id") or ""))
        return ProvisionResult(
            provider_ref={"elid": elid, "name": spec.name},
            root_password=root_password,
            raw=doc,
        )

    def _find_instance(self, provider_ref: Dict[str, str]) -> Optional[dict]:
        doc = self._post("v2.instances")
        for row in self._rows(doc):
            rid = str(_unwrap(row.get("id") or ""))
            rname = str(_unwrap(row.get("name") or ""))
            if rid and rid == provider_ref.get("elid"):
                return row
            if rname and rname == provider_ref.get("name"):
                return row
        return None

    @staticmethod
    def _extract_ip(row: dict) -> Optional[str]:
        for key in ("ip", "mainipaddress", "ipaddr", "addr"):
            val = _unwrap(row.get(key))
            if val:
                return str(val).split()[0]
        return None

    def wait_for_ip(self, provider_ref: Dict[str, str], timeout: int = 360, interval: int = 8) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            row = self._find_instance(provider_ref)
            if row:
                if not provider_ref.get("elid"):
                    provider_ref["elid"] = str(_unwrap(row.get("id") or ""))
                ip = self._extract_ip(row)
                if ip:
                    return ip
            time.sleep(interval)
        raise ProviderError(f"zomro instance {provider_ref} got no IP within {timeout}s")

    def destroy(self, provider_ref: Dict[str, str]) -> None:
        elid = provider_ref.get("elid")
        if not elid:
            raise ProviderError("destroy requires provider_ref.elid")
        self._post("service.delete", elid=elid)
