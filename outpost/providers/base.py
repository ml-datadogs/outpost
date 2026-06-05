"""Common provider interface and helpers shared by all provider clients."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..models import Region


class ProviderError(RuntimeError):
    pass


@dataclass
class ProvisionSpec:
    name: str
    region: Region
    ssh_public_key: Optional[str] = None
    ssh_key_name: str = "outpost"
    os_ref: Optional[str] = None  # provider-native OS id override
    term: str = "hour"  # hourly billing where supported
    auto_prolong: bool = False
    extra_parameters: Dict = field(default_factory=dict)


@dataclass
class ProvisionResult:
    provider_ref: Dict[str, str]  # ids needed to manage / destroy
    ip: Optional[str] = None
    root_password: Optional[str] = None
    raw: Dict = field(default_factory=dict)


# Keyword -> ISO country code, used to tag a provider region/location string.
_COUNTRY_KEYWORDS = {
    "RU": ["russia", "moscow", "saint", "petersburg", "spb", "msk", "россия", "москва"],
    "NL": ["netherlands", "amsterdam", "ams", "нидерланды", "амстердам"],
    "DE": ["germany", "frankfurt", "fra", "nuremberg", "falkenstein", "германия"],
    "FI": ["finland", "helsinki", "hel", "финляндия"],
    "SE": ["sweden", "stockholm", "sto", "швеция"],
    "KZ": ["kazakhstan", "almaty", "astana", "казахстан", "алматы"],
    "PL": ["poland", "warsaw", "waw", "польша"],
    "GB": ["united kingdom", "london", "lon", "england"],
    "US": ["united states", "usa", "new york", "miami", "los angeles"],
    "FR": ["france", "paris", "par"],
    "AM": ["armenia", "yerevan", "армения"],
    "GE": ["georgia", "tbilisi", "грузия"],
    "TR": ["turkey", "istanbul", "türkiye"],
}


def infer_country(*texts: Optional[str]) -> str:
    """Best-effort country code from human location strings. Returns '??' if unknown."""
    blob = " ".join(t for t in texts if t).lower()
    for code, keywords in _COUNTRY_KEYWORDS.items():
        for kw in keywords:
            if kw in blob:
                return code
    return "??"


class BaseProvider(ABC):
    name: str = "base"

    # --- discovery (populate the registry) ---------------------------------
    @abstractmethod
    def list_products(self) -> List[dict]:
        """Raw product/tariff list from the provider API."""

    @abstractmethod
    def list_os(self) -> List[dict]:
        """Raw OS image list from the provider API."""

    @abstractmethod
    def discover_regions(self) -> List[Region]:
        """Country-tagged regions derived from the provider's products."""

    # --- provisioning lifecycle --------------------------------------------
    @abstractmethod
    def ensure_ssh_key(self, name: str, public_key: str) -> str:
        """Upload the SSH public key if absent; return its provider id/name."""

    @abstractmethod
    def create(self, spec: ProvisionSpec) -> ProvisionResult:
        """Order an instance. Returns provider_ref needed for later management."""

    @abstractmethod
    def wait_for_ip(self, provider_ref: Dict[str, str], timeout: int = 300, interval: int = 6) -> str:
        """Poll until the instance has a public IP; return it."""

    @abstractmethod
    def destroy(self, provider_ref: Dict[str, str]) -> None:
        """Destroy the instance (no refund)."""
