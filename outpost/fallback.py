"""Tier-3 fallback sync: mirror igareck/vpn-configs-for-russia into our subscription.

Fetches the upstream auto-tested share-link lists (see docs/fallbacks.md), validates
and dedupes them, and produces base64 subscription bodies in the same universal
format as the Happ render. CI publishes them to KV so the worker can serve them at
``/<token>/fallback`` and ``/<token>/fallback-white`` — a stable URL that keeps
working from RU even when github raw itself is blocked.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Callable, Dict, List

import requests

UPSTREAM_REPO = "igareck/vpn-configs-for-russia"

# Tried in order; first base that answers wins. jsDelivr serves the same repo via
# CDN and tends to survive when raw.githubusercontent is blocked from RU.
BASE_URLS = [
    f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/main",
    f"https://cdn.jsdelivr.net/gh/{UPSTREAM_REPO}@main",
]

# KV key / output name -> upstream files merged into it (order preserved).
# "fallback" = normal internet with blocked resources; "fallback-white" = whitelist
# regime (only allowlisted IPs/SNI pass).
BUNDLES: Dict[str, List[str]] = {
    "fallback": ["BLACK_VLESS_RUS_mobile.txt", "BLACK_SS+All_RUS.txt"],
    "fallback-white": ["WHITE-CIDR-RU-all.txt", "WHITE-SNI-RU-all.txt"],
}

SHARE_SCHEMES = ("vless://", "vmess://", "trojan://", "ss://", "hysteria2://", "hy2://", "tuic://")


class FallbackError(RuntimeError):
    pass


def fetch_upstream(filename: str, timeout: int = 30) -> str:
    """Fetch one upstream file, falling through the mirror list."""
    errors: List[str] = []
    for base in BASE_URLS:
        url = f"{base}/{filename}"
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc}")
    raise FallbackError(f"all mirrors failed for {filename}: " + "; ".join(errors))


def extract_links(body: str) -> List[str]:
    """Share links from a subscription body (plain or base64), unknown lines dropped."""
    text = body
    if not any(s in body for s in SHARE_SCHEMES):
        try:
            text = base64.b64decode(body.strip(), validate=False).decode("utf-8", errors="ignore")
        except (binascii.Error, ValueError):
            return []
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith(SHARE_SCHEMES)]


def build_bundles(fetch: Callable[[str], str] = fetch_upstream) -> Dict[str, str]:
    """Merged + deduped base64 subscription body per bundle key.

    A bundle survives individual file failures but must end up non-empty —
    an empty fallback silently replacing a working one in KV would be worse
    than keeping the previous version.
    """
    out: Dict[str, str] = {}
    for key, files in BUNDLES.items():
        links: List[str] = []
        seen = set()
        errors: List[str] = []
        for fname in files:
            try:
                body = fetch(fname)
            except FallbackError as exc:
                errors.append(str(exc))
                continue
            for link in extract_links(body):
                if link not in seen:
                    seen.add(link)
                    links.append(link)
        if not links:
            raise FallbackError(f"bundle {key} came back empty ({'; '.join(errors) or 'no valid links'})")
        out[key] = base64.b64encode("\n".join(links).encode()).decode("ascii")
    return out


def sync(out_dir: Path, fetch: Callable[[str], str] = fetch_upstream) -> Dict[str, Path]:
    """Write ``outpost.<bundle>.txt`` files; returns bundle -> written path."""
    bundles = build_bundles(fetch)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: Dict[str, Path] = {}
    for key, body in bundles.items():
        path = out_dir / f"outpost.{key}.txt"
        path.write_text(body)
        written[key] = path
    return written
