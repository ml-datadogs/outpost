"""Vultr API v2 provider client.

API: https://api.vultr.com/v2 (auth header: ``Authorization: Bearer <token>``)

Verified routes:
  GET    /plans                      -> plans (id, monthly_cost, locations)
  GET    /regions                    -> regions (id, city, country)
  GET    /os                         -> OS images (id, name, family)
  GET    /ssh-keys                   -> registered SSH keys
  POST   /ssh-keys                   -> upload SSH public key
  POST   /instances                  -> create VPS instance
  GET    /instances/{id}             -> instance detail (main_ip, status, power_status)
  DELETE /instances/{id}             -> destroy instance
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

import requests

from ..models import Region
from .base import BaseProvider, ProviderError, ProvisionResult, ProvisionSpec, infer_country

DEFAULT_BASE_URL = "https://api.vultr.com/v2"
PREFERRED_OS = ["ubuntu 24.04", "ubuntu 22.04", "debian 12", "ubuntu"]
DEFAULT_PLAN = "vc2-1c-1gb"


class VultrProvider(BaseProvider):
    name = "vultr"

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError(f"vultr {method} {path}: non-JSON response ({resp.status_code})") from exc
        if resp.status_code >= 400:
            err = body.get("error") if isinstance(body, dict) else resp.text
            raise ProviderError(f"vultr {method} {path}: HTTP {resp.status_code}: {err}")
        return body if isinstance(body, dict) else {"data": body}

    # --- discovery ---------------------------------------------------------
    def list_products(self) -> List[dict]:
        return self._request("GET", "/plans?type=vc2&per_page=500").get("plans", [])

    def list_os(self) -> List[dict]:
        return self._request("GET", "/os?per_page=500").get("os", [])

    def list_regions_raw(self) -> List[dict]:
        return self._request("GET", "/regions?per_page=500").get("regions", [])

    def find_os_id(self, preferred: Optional[List[str]] = None) -> int:
        preferred = preferred or PREFERRED_OS
        os_items = self.list_os()
        for want in preferred:
            for item in os_items:
                name = str(item.get("name", "")).lower()
                if want in name and "x64" in name:
                    return int(item["id"])
            for item in os_items:
                name = str(item.get("name", "")).lower()
                if want in name:
                    return int(item["id"])
        if os_items:
            return int(os_items[0]["id"])
        return 2284  # Ubuntu 24.04 LTS x64 fallback on Vultr

    def discover_regions(self) -> List[Region]:
        plans = self.list_products()
        # Pick cheapest 1c-1gb vc2 plan (or DEFAULT_PLAN).
        chosen_plan = DEFAULT_PLAN
        plan_locations = set()
        for p in sorted(plans, key=lambda x: float(x.get("monthly_cost", 999))):
            if p.get("id") == DEFAULT_PLAN or (p.get("vcpu_count") == 1 and p.get("ram", 0) >= 1024):
                chosen_plan = str(p["id"])
                plan_locations = set(p.get("locations") or [])
                break

        os_id = str(self.find_os_id())
        out: List[Region] = []
        for reg in self.list_regions_raw():
            rid = str(reg.get("id", ""))
            if not rid:
                continue
            if plan_locations and rid not in plan_locations:
                continue
            country = str(reg.get("country") or infer_country(reg.get("city"), rid)).upper()
            out.append(
                Region(
                    code=rid,
                    country=country,
                    city=reg.get("city"),
                    product_ref={
                        "region": rid,
                        "plan": chosen_plan,
                        "os_id": os_id,
                    },
                )
            )
        return out

    # --- ssh keys ----------------------------------------------------------
    def ensure_ssh_key(self, name: str, public_key: str) -> str:
        norm = " ".join(public_key.strip().split()[:2])
        keys = self._request("GET", "/ssh-keys").get("ssh_keys", [])
        for k in keys:
            existing = " ".join(str(k.get("ssh_key", "")).strip().split()[:2])
            if existing == norm or k.get("name") == name:
                return str(k["id"])
        created = self._request("POST", "/ssh-keys", json={"name": name, "ssh_key": public_key.strip()})
        key_obj = created.get("ssh_key") or created
        return str(key_obj["id"])

    # --- lifecycle ---------------------------------------------------------
    def create(self, spec: ProvisionSpec) -> ProvisionResult:
        ref = spec.region.product_ref
        region_id = ref.get("region") or spec.region.code
        plan_id = ref.get("plan") or DEFAULT_PLAN
        os_id = int(spec.os_ref or ref.get("os_id") or self.find_os_id())

        payload: Dict = {
            "region": region_id,
            "plan": plan_id,
            "os_id": os_id,
            "label": spec.name,
            "hostname": spec.name,
            "backups": "disabled",
        }
        if spec.ssh_public_key:
            key_id = self.ensure_ssh_key(spec.ssh_key_name, spec.ssh_public_key)
            payload["sshkey_id"] = [key_id]
        payload.update(spec.extra_parameters)

        body = self._request("POST", "/instances", json=payload)
        inst = body.get("instance") or body
        instance_id = inst.get("id")
        if not instance_id:
            raise ProviderError(f"vultr POST /instances returned no instance id: {body}")

        ip = inst.get("main_ip")
        if ip in ("0.0.0.0", "", None):
            ip = None
        root_password = inst.get("default_password") or None
        return ProvisionResult(
            provider_ref={"instance_id": str(instance_id)},
            ip=ip,
            root_password=root_password,
            raw=inst,
        )

    def wait_for_ip(self, provider_ref: Dict[str, str], timeout: int = 300, interval: int = 6) -> str:
        instance_id = provider_ref.get("instance_id") or provider_ref.get("service_id")
        if not instance_id:
            raise ProviderError("wait_for_ip requires provider_ref.instance_id")
        deadline = time.time() + timeout
        while time.time() < deadline:
            body = self._request("GET", f"/instances/{instance_id}")
            inst = body.get("instance") or body
            ip = inst.get("main_ip")
            status = str(inst.get("status", "")).lower()
            power = str(inst.get("power_status", "")).lower()
            if ip and ip != "0.0.0.0" and status == "active" and power in ("running", ""):
                return str(ip)
            time.sleep(interval)
        raise ProviderError(f"vultr instance {instance_id} got no active IP within {timeout}s")

    def destroy(self, provider_ref: Dict[str, str]) -> None:
        instance_id = provider_ref.get("instance_id") or provider_ref.get("service_id")
        if not instance_id:
            raise ProviderError("destroy requires provider_ref.instance_id")
        self._request("DELETE", f"/instances/{instance_id}")
