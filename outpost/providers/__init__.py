"""Provider clients. Each implements BaseProvider against a host's provisioning API."""

from __future__ import annotations

from ..config import Settings
from ..config import settings as default_settings
from .base import BaseProvider, ProviderError, ProvisionResult, ProvisionSpec


def get_provider(name: str, settings: Settings = default_settings) -> BaseProvider:
    name = name.lower()
    if name == "aeza":
        from .aeza import AezaProvider

        if not settings.aeza_api_key:
            raise ProviderError("AEZA_API_KEY is not set")
        return AezaProvider(api_key=settings.aeza_api_key, pin=settings.aeza_pin)
    if name == "zomro":
        from .zomro import ZomroProvider

        if not settings.zomro_auth:
            raise ProviderError("ZOMRO_AUTH is not set")
        return ZomroProvider(auth=settings.zomro_auth)
    if name == "hostkey":
        from .hostkey import DEFAULT_BASE_URL, HostkeyProvider

        return HostkeyProvider(
            api_key=settings.hostkey_api_key or "",
            base_url=settings.hostkey_base_url or DEFAULT_BASE_URL,
            whmcs_user=settings.hostkey_email,
            whmcs_password=settings.hostkey_password,
            traffic_plan=settings.hostkey_traffic_plan,
            deploy_options=settings.hostkey_deploy_options,
        )
    raise ProviderError(f"unknown provider: {name}")


__all__ = [
    "BaseProvider",
    "ProviderError",
    "ProvisionResult",
    "ProvisionSpec",
    "get_provider",
]
