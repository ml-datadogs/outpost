#!/usr/bin/env python3
"""On-Mac reachability probe (runs inside the restricted network).

Tests, over the raw ISP path (route-bypass, since Surge Enhanced Mode captures
everything), whether:
  * each live node's TCP port is reachable  -> node.health.reachable
  * each registry region's canary host is reachable -> region.enabled (gate 1+2)

Writes the results back into the (encrypted) inventory + registry and an optional
plaintext report, then optionally commits & pushes so CI can act on the inside view.

Run standalone (no install needed):  python3 agent/probe.py --push
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from outpost import store  # noqa: E402
from outpost.config import settings  # noqa: E402
from outpost.reachability import isp_probe  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Outpost on-Mac reachability probe")
    parser.add_argument("--gateway", default=settings.lan_gateway)
    parser.add_argument("--dns", default=settings.lan_dns)
    parser.add_argument("--push", action="store_true", help="git commit & push updated state")
    parser.add_argument("--report", default=str(REPO_ROOT / "state" / "probe-report.json"))
    args = parser.parse_args()

    if not args.gateway:
        print("ERROR: no LAN gateway (set OUTPOST_LAN_GATEWAY or --gateway)", file=sys.stderr)
        return 2

    inv = store.load_inventory(settings)
    reg = store.load_registry(settings)
    report = {"checked_at": _now_iso(), "nodes": [], "regions": []}

    # Node reachability (trojan TCP port is a good general signal).
    for node in inv.nodes:
        if not node.ip:
            continue
        res = isp_probe(node.ip, node.ports.trojan, gateway=args.gateway, dns=args.dns)
        node.health.reachable = res.ok
        node.health.latency_ms = res.latency_ms
        node.health.checked_at = datetime.now(timezone.utc)
        node.health.source = "probe"
        node.health.note = res.error
        report["nodes"].append(
            {
                "id": node.id,
                "name": node.name,
                "ip": node.ip,
                "reachable": res.ok,
                "latency_ms": res.latency_ms,
                "error": res.error,
            }
        )
        print(f"node {node.name} {node.ip} reachable={res.ok} {res.latency_ms or ''}")

    # Provider/region canary reachability -> drives the reachability eligibility gate.
    for provider in reg.providers:
        for region in provider.regions:
            if not region.canary:
                continue
            res = isp_probe(region.canary, 443, gateway=args.gateway, dns=args.dns)
            region.enabled = res.ok
            report["regions"].append(
                {
                    "provider": provider.name,
                    "region": region.code,
                    "country": region.country,
                    "canary": region.canary,
                    "reachable": res.ok,
                    "latency_ms": res.latency_ms,
                }
            )
            print(f"region {provider.name}/{region.code} canary reachable={res.ok}")

    store.save_inventory(inv, settings)
    store.save_registry(reg, settings)
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=2))
    print(f"wrote {args.report}")

    if args.push:
        _git_push()
    return 0


def _git_push() -> None:
    try:
        subprocess.run(["git", "-C", str(REPO_ROOT), "add", "state/"], check=True)
        subprocess.run(
            ["git", "-C", str(REPO_ROOT), "commit", "-m", "probe: update reachability"],
            check=True,
        )
        subprocess.run(["git", "-C", str(REPO_ROOT), "push"], check=True)
    except subprocess.CalledProcessError as exc:
        print(f"git push skipped/failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
