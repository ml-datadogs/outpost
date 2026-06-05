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
from .providers import ProviderError, get_provider

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
    provider: str = typer.Option(..., "--provider", "-p", help="aeza | zomro"),
    what: str = typer.Option("regions", "--what", help="regions | products | os"),
):
    """Pull live data from a provider API to populate the registry."""
    client = get_provider(provider, settings)
    if what == "products":
        data = client.list_products()
    elif what == "os":
        data = client.list_os()
    elif what == "regions":
        data = [r.model_dump() for r in client.discover_regions()]
    else:
        raise typer.BadParameter("what must be regions | products | os")
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
):
    """Adopt a hand-made server (IP + SSH) and install the sing-box stack."""
    from .orchestrator import adopt_node

    if country.upper() == "RU":
        console.print("[red]refusing:[/red] RU exit bypasses nothing.")
        raise typer.Exit(1)
    inv = _load_inv()
    try:
        node = adopt_node(ip, inventory=inv, settings=settings, country=country, name=name, save=_save)
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
        if node.provider_ref and node.provider != "byo":
            get_provider(node.provider, settings).destroy(node.provider_ref)
    except (ProviderError, RuntimeError) as exc:
        console.print(f"[yellow]provider destroy warning:[/yellow] {exc}")
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
