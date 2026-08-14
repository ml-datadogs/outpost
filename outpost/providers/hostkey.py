"""Hostkey provider client (InvAPI).

API: https://invapi.hostkey.ru/  (POST form-data per module endpoint, JSON responses).
Auth: account API key -> session token via auth.php login, OR Invapi email/password
via auth/whmcslogin (needed to order the first server — API keys require an active server).

Documented modules used:
  auth.php          -> login (API key -> token)
  presets.php       -> list (VPS presets by location)
  os.php            -> list (compatible OS images for a preset)
  eq.php            -> order_instance, show
  eq_callback.php   -> check (async deploy status)
  whmcs.php         -> request_cancellation (immediate destroy)

Orders provision with a root password (optional ssh_key at order time); the SSH
bootstrap logs in by password, installs our key, and locks password auth.
"""

from __future__ import annotations

import ipaddress
import secrets
import string
import time
from typing import Dict, List, Optional

import requests

from ..models import Region
from .base import BaseProvider, ProviderError, ProvisionResult, ProvisionSpec, infer_country

DEFAULT_BASE_URL = "https://invapi.hostkey.ru"
PREFERRED_OS = ["ubuntu 24", "ubuntu 22", "debian 12", "ubuntu", "debian"]
ELIGIBLE_LOCATIONS = ["NL", "DE", "FI", "SE", "PL", "FR", "GB", "UK", "US", "KZ", "AM", "GE", "TR"]
_LOCATION_COUNTRY = {loc: loc for loc in ELIGIBLE_LOCATIONS}
_LOCATION_COUNTRY["UK"] = "GB"
_ROOT_PASS_SPECIALS = "%-_+"
# Used when traffic_plans/list is unavailable; retried on billing/plan errors.
# RU/whmcs_itb billing: plan 25 triggers a Hostkey float*string bug; 37/62/68 work for vm.pico NL.
_FALLBACK_TRAFFIC_PLANS = ["37", "62", "68", "25", "20", "21", "22", "23", "24", "26", "27", "28", "29", "30"]


class HostkeyProvider(BaseProvider):
    name = "hostkey"

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        whmcs_user: Optional[str] = None,
        whmcs_password: Optional[str] = None,
        traffic_plan: Optional[str] = None,
        deploy_options: Optional[str] = None,
        timeout: int = 45,
    ):
        self.api_key = api_key
        self.whmcs_user = whmcs_user
        self.whmcs_password = whmcs_password
        self.default_traffic_plan = traffic_plan
        self.default_deploy_options = deploy_options
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self._token: Optional[str] = None
        self._token_expire: float = 0.0
        self._currency_code: Optional[str] = None
        self._whmcs_location: Optional[str] = None

    # --- low level ---------------------------------------------------------
    def _store_token(self, result: dict) -> str:
        token = result.get("token")
        if not token:
            raise ProviderError("hostkey login: no token in response")
        self._token = str(token)
        expire = result.get("token_expire")
        if isinstance(expire, (int, float)) and expire > 0:
            self._token_expire = float(expire)
        else:
            self._token_expire = time.time() + 3600
        twofa = result.get("2fa") or result.get("twofa")
        if twofa and str(twofa).lower() not in ("", "0", "false", "none"):
            raise ProviderError(
                "hostkey login requires 2FA; disable 2FA on the Invapi account "
                "or complete login in the panel first"
            )
        currency = result.get("currency_code")
        if currency:
            self._currency_code = str(currency)
        whmcs_location = result.get("whmcs_location")
        if whmcs_location:
            self._whmcs_location = str(whmcs_location)
        return self._token

    def _login_api_key(self) -> str:
        body = self._post("auth", "login", auth=False, key=self.api_key)
        result = body.get("result")
        if isinstance(result, dict) and result.get("token"):
            return self._store_token(result)
        err = body.get("error") or body.get("message")
        if err:
            raise ProviderError(f"hostkey login failed: {err}")
        raise ProviderError("hostkey login: no token in response")

    def _login_whmcs(self) -> str:
        if not self.whmcs_user or not self.whmcs_password:
            raise ProviderError("HOSTKEY_EMAIL and HOSTKEY_PASSWORD are not set")
        body = self._post(
            "auth",
            "whmcslogin",
            auth=False,
            user=self.whmcs_user,
            password=self.whmcs_password,
        )
        result = body.get("result")
        if isinstance(result, dict) and result.get("token"):
            return self._store_token(result)
        err = body.get("error") or body.get("message")
        if err:
            raise ProviderError(f"hostkey whmcslogin failed: {err}")
        raise ProviderError("hostkey whmcslogin: no token in response")

    def _login(self) -> str:
        if self._token and time.time() < self._token_expire - 60:
            return self._token
        if not self.api_key and not (self.whmcs_user and self.whmcs_password):
            raise ProviderError(
                "Hostkey auth not configured: set HOSTKEY_API_KEY or HOSTKEY_EMAIL + HOSTKEY_PASSWORD"
            )
        if self.api_key:
            try:
                token = self._login_api_key()
                self._ensure_session_meta()
                return token
            except ProviderError as exc:
                no_servers = "no appropriate servers" in str(exc).lower()
                if not no_servers or not (self.whmcs_user and self.whmcs_password):
                    raise
        token = self._login_whmcs()
        self._ensure_session_meta()
        return token

    def _ensure_session_meta(self) -> None:
        if self._whmcs_location and self._currency_code:
            return
        try:
            info = self._post("auth", "info")
            result = info.get("result") if isinstance(info.get("result"), dict) else {}
            if result.get("currency_code") and not self._currency_code:
                self._currency_code = str(result["currency_code"])
            if result.get("whmcs_location") and not self._whmcs_location:
                self._whmcs_location = str(result["whmcs_location"])
        except ProviderError:
            pass

    def _deploy_options(self) -> Optional[str]:
        return self.default_deploy_options or self._whmcs_location

    def _api_currency_code(self) -> Optional[str]:
        """InvAPI order_instance accepts EUR/USD only; omit RUB (account default)."""
        if self._currency_code and self._currency_code.upper() in ("EUR", "USD"):
            return self._currency_code.upper()
        return None

    def _traffic_plan_candidates(self, ref: dict, preset: str, location: str) -> List[str]:
        if self.default_traffic_plan:
            return [self.default_traffic_plan]
        plans = self.list_traffic_plans(preset, location)
        main = [
            str(p["id"])
            for p in plans
            if isinstance(p, dict) and p.get("main_plan") and p.get("id") is not None
        ]
        other = [
            str(p["id"])
            for p in plans
            if isinstance(p, dict) and p.get("id") is not None and str(p["id"]) not in main
        ]
        ordered: List[str] = []
        if ref.get("traffic_plan"):
            ordered.append(str(ref["traffic_plan"]))
        for sid in main + other + _FALLBACK_TRAFFIC_PLANS:
            if sid not in ordered:
                ordered.append(sid)
        return ordered

    def _find_stock_id(self, preset_name: str, location: str) -> Optional[str]:
        try:
            body = self._post("presets", "search", name=preset_name, location=location)
        except ProviderError:
            return None
        for row in body.get("servers") or []:
            if not isinstance(row, dict) or row.get("id") is None:
                continue
            row_loc = str(row.get("location") or location).upper()
            if row_loc == location.upper():
                return str(row["id"])
        return None

    def _order_via_stock_deploy(
        self,
        stock_id: str,
        spec: ProvisionSpec,
        ref: dict,
        os_id: str,
        root_password: str,
    ) -> dict:
        fields = {
            "id": stock_id,
            "root_pass": root_password,
            "deploy_data": self._deploy_period(spec, ref),
            "os_id": str(os_id),
            "hostname": spec.name,
            "soft_id": 0,
        }
        deploy_options = self._deploy_options()
        if deploy_options:
            fields["deploy_options"] = deploy_options
        currency = self._api_currency_code()
        if currency:
            fields["currency_code"] = currency
        if spec.ssh_public_key:
            fields["ssh_key"] = spec.ssh_public_key
        fields.update(spec.extra_parameters)
        return self._post("eq", "deploy", **fields)

    def _post(self, module: str, action: str, auth: bool = True, **fields) -> dict:
        data = {"action": action}
        if auth:
            data["token"] = self._login()
        data.update({k: v for k, v in fields.items() if v is not None})
        url = f"{self.base_url}/{module}.php"
        resp = self.session.post(url, data=data, timeout=self.timeout)
        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError(f"hostkey {module}/{action}: non-JSON ({resp.status_code})") from exc
        if not isinstance(body, dict):
            raise ProviderError(f"hostkey {module}/{action}: unexpected response type")
        code = body.get("code")
        if isinstance(code, int) and code < 0:
            msg = body.get("message") or body.get("error") or body
            raise ProviderError(f"hostkey {module}/{action}: {msg}")
        if body.get("result") == "Fail" or body.get("state") == "fail":
            msg = body.get("message") or body.get("error") or body
            raise ProviderError(f"hostkey {module}/{action}: {msg}")
        result = body.get("result")
        if body.get("error") and str(result) in ("-1", -1):
            raise ProviderError(f"hostkey {module}/{action}: {body['error']}")
        if resp.status_code >= 400:
            raise ProviderError(f"hostkey {module}/{action}: HTTP {resp.status_code}")
        return body

    @staticmethod
    def _generate_root_password(length: int = 16) -> str:
        length = max(8, min(30, length))
        pool = string.ascii_letters + string.digits
        for _ in range(20):
            chars = [
                secrets.choice(string.ascii_lowercase),
                secrets.choice(string.ascii_uppercase),
                secrets.choice(string.digits),
                secrets.choice(_ROOT_PASS_SPECIALS),
            ]
            chars.extend(secrets.choice(pool) for _ in range(length - len(chars)))
            secrets.SystemRandom().shuffle(chars)
            password = "".join(chars)
            if password[0].isalnum():
                return password
        raise ProviderError("hostkey: failed to generate a compliant root password")

    @staticmethod
    def _is_public_ipv4(addr: str) -> bool:
        try:
            ip = ipaddress.ip_address(addr.split("/")[0])
        except ValueError:
            return False
        return ip.version == 4 and ip.is_global

    @staticmethod
    def _extract_ip_from_show(body: dict) -> Optional[str]:
        ips = body.get("IP")
        if not isinstance(ips, list):
            return None
        for entry in ips:
            if not isinstance(entry, dict):
                continue
            raw = entry.get("IP") or entry.get("ip")
            if raw and HostkeyProvider._is_public_ipv4(str(raw)):
                return str(raw).split("/")[0]
        return None

    @staticmethod
    def _is_vps_preset(preset: dict) -> bool:
        virtual = preset.get("virtual")
        if virtual is not None:
            return bool(int(virtual))
        server_type = str(preset.get("server_type") or "").lower()
        return "vps" in server_type or "virtual" in server_type

    @staticmethod
    def _preset_locations(preset: dict) -> List[str]:
        locs: List[str] = []
        raw = preset.get("locations")
        if isinstance(raw, str) and raw.strip():
            locs.extend(part.strip().upper() for part in raw.split(",") if part.strip())
        regions = preset.get("regions")
        if isinstance(regions, dict):
            locs.extend(str(k).upper() for k in regions.keys())
        return list(dict.fromkeys(locs))

    @staticmethod
    def _deploy_period(spec: ProvisionSpec, product_ref: dict) -> str:
        override = product_ref.get("deploy_period")
        if override:
            return str(override)
        # Hourly billing requires a prebill Hostkey account; default to monthly.
        return "monthly"

    # --- discovery ---------------------------------------------------------
    def list_products(self) -> List[dict]:
        products: List[dict] = []
        seen: set[str] = set()
        for location in ELIGIBLE_LOCATIONS:
            body = self._post("presets", "list", auth=False, location=location)
            for preset in body.get("presets") or []:
                if not isinstance(preset, dict) or not self._is_vps_preset(preset):
                    continue
                pid = str(preset.get("id") or "")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                enriched = dict(preset)
                enriched.setdefault("locations", location)
                products.append(enriched)
        return products

    def list_os(self, preset_id: Optional[str] = None, location: Optional[str] = None) -> List[dict]:
        fields: Dict[str, str] = {"bill_period": "monthly"}
        if preset_id:
            fields["instance_id"] = str(preset_id)
        if location:
            fields["location"] = str(location)
        body = self._post("os", "list", auth=False, **fields)
        return body.get("os_list") or []

    def list_traffic_plans(self, preset_id: str, location: str) -> List[dict]:
        """Compatible traffic plans for a preset/location (requires auth)."""
        param_sets = [
            {"instance_id": preset_id, "location": location, "bill_period": "monthly"},
            {"instance_id": preset_id, "location": location},
            {"instance": preset_id, "location": location},
            {"id": preset_id, "location": location},
        ]
        for params in param_sets:
            try:
                body = self._post("traffic_plans", "list", auth=True, **params)
            except ProviderError:
                continue
            plans = body.get("traffic_plans") or body.get("plans") or []
            if isinstance(plans, list) and plans:
                return plans
        return []

    def find_os_id(
        self,
        preset_id: str,
        location: Optional[str] = None,
        preferred: Optional[List[str]] = None,
    ) -> Optional[str]:
        preferred = preferred or PREFERRED_OS
        os_items = self.list_os(preset_id, location)
        for want in preferred:
            for item in os_items:
                name = str(item.get("name", "")).lower()
                if want in name and item.get("id") is not None:
                    return str(item["id"])
        for item in os_items:
            if item.get("id") is not None:
                return str(item["id"])
        return None

    def discover_regions(self) -> List[Region]:
        regions: List[Region] = []
        os_cache: Dict[str, Optional[str]] = {}
        for preset in self.list_products():
            preset_id = str(preset.get("id") or "")
            if not preset_id:
                continue
            name = str(preset.get("name") or preset_id)
            locs = self._preset_locations(preset) or [str(preset.get("locations") or "NL").upper()]
            if preset_id not in os_cache:
                os_cache[preset_id] = self.find_os_id(preset_id, locs[0] if locs else None)
            os_id = os_cache[preset_id]
            traffic_plans = self.list_traffic_plans(preset_id, locs[0] if locs else "NL")
            traffic_plan = None
            for plan in traffic_plans:
                if isinstance(plan, dict) and plan.get("main_plan") and plan.get("id") is not None:
                    traffic_plan = str(plan["id"])
                    break
            if traffic_plan is None and traffic_plans:
                first = traffic_plans[0]
                if isinstance(first, dict) and first.get("id") is not None:
                    traffic_plan = str(first["id"])
            for loc in locs:
                if loc == "RU" or loc not in ELIGIBLE_LOCATIONS:
                    continue
                product_ref = {"preset": preset_id, "location_name": loc}
                if os_id:
                    product_ref["os_id"] = os_id
                if traffic_plan:
                    product_ref["traffic_plan"] = traffic_plan
                product_ref["deploy_period"] = "monthly"
                regions.append(
                    Region(
                        code=f"{preset_id}-{loc}",
                        country=_LOCATION_COUNTRY.get(loc) or infer_country(loc, name),
                        city=name or None,
                        product_ref=product_ref,
                    )
                )
        return regions

    # --- ssh keys (optional at order time; bootstrap installs the key) ------
    def ensure_ssh_key(self, name: str, public_key: str) -> str:
        return name

    # --- lifecycle ---------------------------------------------------------
    def _resolve_server_from_callback(
        self, callback: str, timeout: int = 120, interval: int = 6
    ) -> Optional[str]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                body = self._post("eq_callback", "check", key=callback)
            except ProviderError:
                time.sleep(interval)
                continue
            ctx = body.get("context")
            if isinstance(ctx, dict) and ctx.get("id") is not None:
                return str(ctx["id"])
            time.sleep(interval)
        return None

    def _find_server_by_hostname(self, hostname: str) -> Optional[str]:
        try:
            body = self._post("eq", "unified_server_search", query=hostname)
        except ProviderError:
            return None
        results = body.get("results") if isinstance(body.get("results"), dict) else {}
        for row in results.get("servers") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("hostname") or "")
            if hostname in name or name == hostname:
                if row.get("id") is not None:
                    return str(row["id"])
        return None

    def _resolve_order_ids(self, body: dict, hostname: str) -> tuple[str, Optional[str]]:
        server_id = body.get("id") or body.get("server_id")
        callback = body.get("callback")
        if server_id is not None:
            return str(server_id), str(callback) if callback else None

        invoice = body.get("invoice")
        if invoice is not None:
            found = self._find_server_by_hostname(hostname)
            if found:
                return found, str(callback) if callback else None
            status = body.get("status") or body.get("invoice_status")
            raise ProviderError(
                f"hostkey order created invoice {invoice} (status={status}) but no server id yet; "
                "ensure account balance covers the order, then retry provision"
            )

        if callback:
            found = self._resolve_server_from_callback(str(callback))
            if found:
                return found, str(callback)

        found = self._find_server_by_hostname(hostname)
        if found:
            return found, str(callback) if callback else None

        keys = ", ".join(sorted(str(k) for k in body.keys()))
        raise ProviderError(f"hostkey order_instance: no server id in response (keys: {keys})")

    def create(self, spec: ProvisionSpec) -> ProvisionResult:
        ref = spec.region.product_ref
        preset = ref.get("preset")
        location = ref.get("location_name") or spec.region.code
        if not preset:
            raise ProviderError(f"region {spec.region.code} missing preset; run discovery first")

        os_id = spec.os_ref or ref.get("os_id") or self.find_os_id(str(preset), str(location))
        if not os_id:
            raise ProviderError("hostkey create requires an os_id (spec.os_ref or product_ref)")

        root_password = self._generate_root_password()
        fields_base = {
            "preset": str(preset),
            "location_name": str(location),
            "os_id": str(os_id),
            "root_pass": root_password,
            "hostname": spec.name,
            "deploy_period": self._deploy_period(spec, ref),
            "deploy_notify": 0,
            "soft_id": 0,
        }
        deploy_options = self._deploy_options()
        if deploy_options:
            fields_base["deploy_options"] = deploy_options
        currency = self._api_currency_code()
        if currency:
            fields_base["currency_code"] = currency
        if spec.ssh_public_key:
            fields_base["ssh_key"] = spec.ssh_public_key
        fields_base.update(spec.extra_parameters)

        plan_candidates = self._traffic_plan_candidates(ref, str(preset), str(location))
        if not plan_candidates:
            plan_candidates = [None]  # type: ignore[list-item]

        last_err: Optional[Exception] = None
        body: dict = {}
        for traffic_plan in plan_candidates:
            fields = dict(fields_base)
            if traffic_plan is not None:
                fields["traffic_plan"] = traffic_plan
            try:
                body = self._post("eq", "order_instance", **fields)
                last_err = None
                break
            except ProviderError as exc:
                last_err = exc
                msg = str(exc).lower()
                if "float * string" in msg or "not compatible" in msg or "traffic plan" in msg:
                    continue
                raise
        if last_err is not None:
            preset_name = ref.get("preset_name") or spec.region.city or str(preset)
            stock_id = self._find_stock_id(str(preset_name), str(location))
            if stock_id:
                try:
                    body = self._order_via_stock_deploy(stock_id, spec, ref, str(os_id), root_password)
                    last_err = None
                except ProviderError as exc:
                    last_err = exc
        if last_err is not None:
            raise ProviderError(
                f"hostkey order failed for preset {preset} in {location} "
                f"(tried traffic plans: {plan_candidates}): {last_err}"
            )
        server_id, callback = self._resolve_order_ids(body, spec.name)
        provider_ref = {"server_id": server_id}
        if callback:
            provider_ref["callback"] = callback
        return ProvisionResult(
            provider_ref=provider_ref,
            root_password=root_password,
            raw=body,
        )

    def _deploy_done(self, provider_ref: Dict[str, str]) -> bool:
        callback = provider_ref.get("callback")
        if not callback:
            return True
        try:
            body = self._post("eq_callback", "check", key=callback)
        except ProviderError:
            return False
        ctx = body.get("context")
        if isinstance(ctx, dict):
            status = str(ctx.get("status") or ctx.get("deploy_status") or "").lower()
            if status in {"done", "complete", "completed", "ok", "active", "rent"}:
                return True
            if status in {"fail", "failed", "error"}:
                raise ProviderError(f"hostkey deploy failed: {ctx}")
        return body.get("result") == "OK" and not body.get("pending")

    def wait_for_ip(self, provider_ref: Dict[str, str], timeout: int = 600, interval: int = 10) -> str:
        server_id = provider_ref.get("server_id")
        if not server_id:
            raise ProviderError("wait_for_ip requires provider_ref.server_id")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if provider_ref.get("callback") and not self._deploy_done(provider_ref):
                time.sleep(interval)
                continue
            body = self._post("eq", "show", id=server_id)
            ip = self._extract_ip_from_show(body)
            if ip:
                return ip
            time.sleep(interval)
        raise ProviderError(f"hostkey server {server_id} got no IP within {timeout}s")

    def destroy(self, provider_ref: Dict[str, str]) -> None:
        server_id = provider_ref.get("server_id")
        if not server_id:
            raise ProviderError("destroy requires provider_ref.server_id")
        self._post(
            "whmcs",
            "request_cancellation",
            id=server_id,
            cancellation_type=1,
        )
