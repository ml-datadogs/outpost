"""Outpost CLI: provision, adopt, list, destroy, render, monitor, rotate, probe."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import store
from .config import settings
from .models import Inventory
from .providers import MANUAL_PROVIDERS, ProviderError, get_provider

app = typer.Typer(
    add_completion=False, help="Automated proxy fleet: provision, monitor, rotate, render subscriptions."
)
console = Console()


def _save(inv: Inventory):
    store.save_inventory(inv, settings)


def _load_inv() -> Inventory:
    return store.load_inventory(settings)


def _load_reg():
    return store.load_registry(settings)


# --------------------------------------------------------------------------- discover
@app.command()
def discover(
    provider: str = typer.Option(..., "--provider", "-p", help="aeza | zomro | hostkey"),
    what: str = typer.Option("regions", "--what", help="regions | products | os | traffic_plans"),
    preset: str = typer.Option("108", "--preset", help="Hostkey preset id (for traffic_plans)"),
    location: str = typer.Option("NL", "--location", help="Hostkey location code (for traffic_plans)"),
):
    """Pull live data from a provider API to populate the registry."""
    client = get_provider(provider, settings)
    if what == "products":
        data = client.list_products()
    elif what == "os":
        data = client.list_os()
    elif what == "traffic_plans":
        if provider.lower() != "hostkey":
            raise typer.BadParameter("traffic_plans is only supported for hostkey")
        data = client.list_traffic_plans(preset, location)
    elif what == "regions":
        data = [r.model_dump() for r in client.discover_regions()]
    else:
        raise typer.BadParameter("what must be regions | products | os | traffic_plans")
    console.print_json(json.dumps(data, default=str))


# --------------------------------------------------------------------------- provision
@app.command()
def provision(
    provider: str = typer.Option(..., "--provider", "-p"),
    region: Optional[str] = typer.Option(None, "--region", "-r"),
    name: Optional[str] = typer.Option(None, "--name", "-n"),
):
    """Order a VPS in an eligible (non-RU) region and bootstrap sing-box."""
    from .orchestrator import provision_node

    inv = _load_inv()
    reg = _load_reg()
    try:
        node = provision_node(
            provider,
            registry=reg,
            inventory=inv,
            settings=settings,
            region_code=region,
            name=name,
            save=_save,
        )
    except (ProviderError, RuntimeError) as exc:
        console.print(f"[red]provision failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]provisioned[/green] {node.display} ip={node.ip}")


# --------------------------------------------------------------------------- adopt (BYO)
@app.command()
def adopt(
    ip: str = typer.Option(..., "--ip"),
    country: str = typer.Option("??", "--country", "-c"),
    name: Optional[str] = typer.Option(None, "--name", "-n"),
    password: Optional[str] = typer.Option(
        None, "--password", help="root password for first login (else SSH key is used)"
    ),
    provider: str = typer.Option("byo", "--provider", "-p", help="provider tag (e.g. aeza)"),
    service_id: Optional[str] = typer.Option(
        None, "--service-id", help="provider service id, so the node stays manageable"
    ),
):
    """Adopt a hand-made server (IP + SSH) and install the sing-box stack."""
    from .orchestrator import adopt_node

    if country.upper() == "RU":
        console.print("[red]refusing:[/red] RU exit bypasses nothing.")
        raise typer.Exit(1)
    inv = _load_inv()
    if service_id:
        provider_ref = (
            {"server_id": service_id} if provider.lower() == "hostkey" else {"service_id": service_id}
        )
    else:
        provider_ref = None
    try:
        node = adopt_node(
            ip,
            inventory=inv,
            settings=settings,
            country=country,
            name=name,
            root_password=password,
            provider_name=provider,
            provider_ref=provider_ref,
            save=_save,
        )
    except RuntimeError as exc:
        console.print(f"[red]adopt failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]adopted[/green] {node.display} ip={node.ip}")


# --------------------------------------------------------------------------- list
@app.command(name="list")
def list_nodes():
    """Show the current fleet."""
    inv = _load_inv()
    if not inv.nodes:
        console.print("[yellow]no nodes[/yellow]")
        return
    table = Table(title="Outpost fleet")
    for col in ("id", "name", "provider", "country", "ip", "status", "reachable", "alive", "latency"):
        table.add_column(col)
    for n in inv.nodes:
        table.add_row(
            n.id,
            n.name,
            n.provider,
            n.country,
            n.ip or "-",
            n.status.value,
            _fmt(n.health.reachable),
            _fmt(n.health.alive),
            f"{n.health.latency_ms}ms" if n.health.latency_ms else "-",
        )
    console.print(table)


def _fmt(v: Optional[bool]) -> str:
    return "-" if v is None else ("yes" if v else "NO")


# --------------------------------------------------------------------------- destroy
@app.command()
def destroy(
    node_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Destroy a node at its provider and remove it from inventory."""
    inv = _load_inv()
    node = inv.get(node_id)
    if not node:
        console.print(f"[red]no node[/red] {node_id}")
        raise typer.Exit(1)
    if not yes:
        typer.confirm(f"Destroy {node.display}?", abort=True)
    try:
        if node.provider_ref and node.provider not in MANUAL_PROVIDERS:
            get_provider(node.provider, settings).destroy(node.provider_ref)
    except (ProviderError, RuntimeError) as exc:
        console.print(f"[yellow]provider destroy warning:[/yellow] {exc}")
    try:
        from .dns import release_hostname

        if release_hostname(node, settings=settings):
            console.print(f"[green]released DNS[/green] {node.tls_domain}")
    except RuntimeError as exc:
        console.print(f"[yellow]dns release warning:[/yellow] {exc}")
    inv.remove(node_id)
    _save(inv)
    console.print(f"[green]removed[/green] {node_id}")


# --------------------------------------------------------------------------- render
@app.command()
def render(
    surge: bool = typer.Option(False, "--surge"),
    happ: bool = typer.Option(False, "--happ"),
    out: Optional[Path] = typer.Option(None, "--out", help="directory to write subscription files"),
):
    """Render Surge and/or Happ subscriptions from the inventory."""
    from .render import render_happ, render_surge

    inv = _load_inv()
    if not surge and not happ:
        surge = happ = True

    outputs = {}
    if surge:
        outputs["outpost.surge.conf"] = render_surge(inv)
    if happ:
        outputs["outpost.happ.txt"] = render_happ(inv)

    if out:
        out.mkdir(parents=True, exist_ok=True)
        for fname, content in outputs.items():
            (out / fname).write_text(content)
            console.print(f"[green]wrote[/green] {out / fname}")
    else:
        for fname, content in outputs.items():
            console.rule(fname)
            console.print(content)


# --------------------------------------------------------------------------- recert
@app.command()
def recert(
    node_id: Optional[str] = typer.Argument(None, help="node to re-issue (default: all active)"),
    password: Optional[str] = typer.Option(
        None, "--password", help="root password, if key auth is not set up"
    ),
):
    """Give nodes their own DNS name + a real Let's Encrypt cert, then re-bootstrap.

    Use after configuring OUTPOST_DNS_ZONE to migrate nodes off self-signed certs.
    """
    from .dns import DNSError, assign_hostname
    from .server.bootstrap import BootstrapError, bootstrap_node

    if not settings.dns_zone or not settings.cloudflare_api_token:
        console.print("[red]not configured:[/red] set OUTPOST_DNS_ZONE and CLOUDFLARE_API_TOKEN first")
        raise typer.Exit(1)

    inv = _load_inv()
    targets = [inv.get(node_id)] if node_id else list(inv.active())
    if not targets or targets[0] is None:
        console.print(f"[red]no node[/red] {node_id or ''}")
        raise typer.Exit(1)

    failed = False
    for node in targets:
        if node is None or not node.ip:
            continue
        try:
            fqdn = assign_hostname(node, settings=settings)
            console.print(f"{node.name}: dns -> [bold]{fqdn}[/bold]")
            bootstrap_node(node, settings=settings, root_password=password)
            _save(inv)
        except (DNSError, BootstrapError, RuntimeError) as exc:
            failed = True
            console.print(f"[red]{node.name} failed:[/red] {exc}")
            continue
        status = "real cert" if not node.insecure else "self-signed"
        console.print(f"[green]{node.name}[/green] re-bootstrapped ({status})")
    if failed:
        raise typer.Exit(1)


# --------------------------------------------------------------------------- fallback
@app.command()
def fallback(
    out: Path = typer.Option(Path("dist-subs"), "--out", help="directory to write fallback files"),
):
    """Sync tier-3 public fallback subscriptions from igareck/vpn-configs-for-russia."""
    from .fallback import FallbackError, sync

    try:
        written = sync(out)
    except FallbackError as exc:
        console.print(f"[red]fallback sync failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    for key, path in written.items():
        console.print(f"[green]wrote[/green] {path} ({key})")


# --------------------------------------------------------------------------- monitor
@app.command()
def monitor():
    """Run CI-style liveness checks and apply probe-driven status transitions."""
    from .monitor import apply_health_transitions, monitor_liveness

    inv = _load_inv()
    changed = monitor_liveness(inv, save=None)
    changed += apply_health_transitions(inv, save=_save)
    if not changed:
        console.print("[green]all nodes nominal[/green]")
    for n in changed:
        console.print(f"{n.name}: -> [bold]{n.status.value}[/bold]")


# --------------------------------------------------------------------------- rotate
@app.command()
def rotate(
    no_provision: bool = typer.Option(False, "--no-provision", help="dry run: don't create replacements"),
    reap: bool = typer.Option(False, "--reap", help="also destroy nodes past their retire grace"),
):
    """Replace blocked/down nodes and optionally reap retired ones."""
    from .rotate import reap_retired
    from .rotate import rotate as do_rotate

    inv = _load_inv()
    reg = _load_reg()
    new_nodes = do_rotate(inv, reg, settings=settings, save=_save, do_provision=not no_provision)
    for n in new_nodes:
        console.print(f"[green]replacement[/green] {n.display} ip={n.ip}")
    if reap:
        removed = reap_retired(inv, settings=settings, save=_save)
        for rid in removed:
            console.print(f"[green]reaped[/green] {rid}")
    if not new_nodes and not reap:
        console.print("[green]nothing to rotate[/green]")


# --------------------------------------------------------------------------- probe
@app.command()
def probe(
    node_id: Optional[str] = typer.Option(None, "--node", help="probe a single node id"),
    gateway: Optional[str] = typer.Option(None, "--gateway", help="LAN gateway (defaults to env)"),
    dns: Optional[str] = typer.Option(None, "--dns", help="LAN resolver (defaults to env)"),
):
    """Test reachability from inside the restricted network (route-bypass) and record it."""
    from .reachability import isp_probe

    gw = gateway or settings.lan_gateway
    if not gw:
        console.print("[red]no gateway[/red]: set OUTPOST_LAN_GATEWAY or pass --gateway")
        raise typer.Exit(1)
    resolver = dns or settings.lan_dns

    inv = _load_inv()
    targets = [n for n in inv.nodes if n.ip and (node_id is None or n.id == node_id)]
    for n in targets:
        if not n.ip:
            continue
        res = isp_probe(n.ip, n.ports.trojan, gateway=gw, dns=resolver)
        n.health.reachable = res.ok
        n.health.latency_ms = res.latency_ms
        n.health.checked_at = datetime.now(timezone.utc)
        n.health.source = "probe"
        n.health.note = res.error
        verdict = "[green]reachable[/green]" if res.ok else f"[red]blocked[/red] ({res.error})"
        console.print(f"{n.name} {n.ip}:{n.ports.trojan} -> {verdict} {res.latency_ms or ''}")
    _save(inv)


if __name__ == "__main__":
    app()
