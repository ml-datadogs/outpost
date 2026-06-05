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
        return AezaProvider(api_key=settings.aeza_api_key)
    if name == "zomro":
        from .zomro import ZomroProvider

        if not settings.zomro_auth:
            raise ProviderError("ZOMRO_AUTH is not set")
        return ZomroProvider(auth=settings.zomro_auth)
    raise ProviderError(f"unknown provider: {name}")


__all__ = [
    "BaseProvider",
    "ProviderError",
    "ProvisionResult",
    "ProvisionSpec",
    "get_provider",
]
