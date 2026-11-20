"""Manual fleet pilot. Measurements and billing snapshots stay on the operator machine."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from ipaddress import ip_address
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import typer

from . import store
from .config import settings
from .models import Inventory, NodeStatus
from .pilot_probe import check_origin, measure
from .render import happ_profiles, profile_key

app = typer.Typer(help="On-demand Happ qualification and fleet costs.")
MOSCOW = ZoneInfo("Europe/Moscow")


@dataclass
class Profile:
    node: str
    name: str
    ip: str
    country: str
    link: str

    @property
    def key(self) -> str:
        return profile_key(self.link, self.ip, self.country)


def profiles(inv: Inventory) -> list[Profile]:
    result = []
    for n in inv.nodes:
        if "happ-pilot" not in n.tags or n.status in (NodeStatus.PROVISIONING, NodeStatus.RETIRING):
            continue
        if not n.ip or not n.secrets or not n.is_exit_eligible():
            continue
        result.extend(Profile(n.id, k, n.ip, n.country, v) for k, v in happ_profiles(n).items())
    control = os.getenv("OUTPOST_HAPP_CONTROL_FILE")
    if control:
        ip = str(ip_address(os.environ["OUTPOST_HAPP_CONTROL_IP"]))
        country = os.environ["OUTPOST_HAPP_CONTROL_COUNTRY"].upper()
        result.append(Profile("control", "hostkey", ip, country, Path(control).read_text().strip()))
    return result


def selected(node: Optional[str], profile: Optional[str]) -> list[Profile]:
    manifest = os.getenv("OUTPOST_PILOT_TARGETS")
    targets = (
        [Profile(**entry) for entry in json.loads(Path(manifest).read_text())]
        if manifest
        else profiles(store.load_inventory())
    )
    matched = [
        p for p in targets if (node is None or p.node == node) and (profile is None or p.name == profile)
    ]
    if not matched:
        raise typer.BadParameter("No matching pilot profile. Adopt a pilot VPS or configure the control.")
    return matched


def database() -> sqlite3.Connection:
    path = settings.state_dir / "pilot-private"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(path / "history.sqlite", timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS samples (
          stamp INTEGER, node TEXT, profile TEXT, key TEXT, network TEXT, ok INTEGER,
          stage TEXT, bytes INTEGER, elapsed_ms INTEGER, connect_ms INTEGER, detail TEXT);
        CREATE INDEX IF NOT EXISTS samples_key_time ON samples(key,stamp);
        CREATE TABLE IF NOT EXISTS accounts (
          provider TEXT PRIMARY KEY, currency TEXT, balance TEXT, monthly TEXT, rate TEXT,
          due TEXT, remaining_gb TEXT, checked TEXT);
    """)
    return db


def sample(db, p, network, ok, stage, received=0, elapsed=0, connected=0, detail=""):
    db.execute(
        "INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (int(time.time()), p.node, p.name, p.key, network, ok, stage, received, elapsed, connected, detail),
    )
    db.commit()


@app.command()
def run(node: Optional[str] = None, profile: Optional[str] = None, interface: str = "en0"):
    """Run one authenticated home measurement for each selected profile."""
    targets = selected(node, profile)
    check_origin(interface)
    failures = 0
    with closing(database()) as db:
        for p in targets:
            try:
                obs = measure(p.link, p.ip, p.country, interface)
            except (RuntimeError, OSError, KeyError, ValueError):
                typer.echo(f"{p.node}/{p.name}: local client/configuration unavailable; no sample", err=True)
                failures += 1
                continue
            sample(db, p, "home", obs.ok, obs.stage, obs.bytes_received, obs.transfer_ms, obs.handshake_ms)
            typer.echo(f"{p.node}/{p.name} {obs.stage} {obs.bytes_received} bytes {obs.transfer_ms}ms")
            failures += not obs.ok
    if failures:
        raise typer.Exit(1)


def private_file(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as f:
        try:
            f.write(data)
            f.close()
            os.replace(f.name, path)
        finally:
            Path(f.name).unlink(missing_ok=True)


@app.command()
def trial(out: Path = typer.Option(...), node: Optional[str] = None):
    """Write candidate share links for local import into Happ before qualification."""
    private_file(out, "\n".join(p.link for p in selected(node, None)) + "\n")


@app.command()
def mobile(
    node: str = typer.Option(...),
    profile: str = typer.Option(...),
    operator: str = typer.Option(...),
    version: str = typer.Option(...),
    exit_ip: str = typer.Option(...),
    country: str = typer.Option(...),
    youtube: bool = typer.Option(..., "--youtube/--no-youtube"),
    telegram: bool = typer.Option(..., "--telegram/--no-telegram"),
    github: bool = typer.Option(..., "--github/--no-github"),
):
    """Record a real cellular Happ test. YouTube means ten minutes of playback."""
    p = selected(node, profile)[0]
    checks = {
        "exit": exit_ip == p.ip and country.upper() == p.country,
        "youtube": youtube,
        "telegram": telegram,
        "github": github,
    }
    stage = ",".join(k for k, ok in checks.items() if not ok) or "ok"
    with closing(database()) as db:
        sample(
            db,
            p,
            "mobile",
            all(checks.values()),
            stage,
            detail=json.dumps({"operator": operator, "happ": version}),
        )
    typer.echo(stage)


def home_slots(rows, now: int) -> dict[int, bool]:
    # Use the last 288 completed quarter hours. Keep the first attempt per slot.
    end = now // 900
    slots = {}
    for r in rows:
        slot = r["stamp"] // 900
        if r["network"] == "home" and end - 288 <= slot < end:
            slots.setdefault(slot, bool(r["ok"]))
    return slots


def qualification(rows, now: int) -> Optional[str]:
    home = [r for r in rows if r["network"] == "home"]
    if not home or now - home[0]["stamp"] < 72 * 3600:
        return "Pilot duration below 72 hours"
    slots = home_slots(home, now)
    if len(slots) < 260 or sum(slots.values()) * 100 < 99 * len(slots):
        return f"Home gate: {sum(slots.values())}/{len(slots)} successful, {len(slots)}/288 observed"
    if not home[-1]["ok"] or now - home[-1]["stamp"] > 1800:
        return "Fresh successful home sample required"
    mobile = [r for r in rows if r["network"] == "mobile" and 0 <= now - r["stamp"] <= 72 * 3600]
    if not mobile or not mobile[-1]["ok"] or now - mobile[-1]["stamp"] > 86400:
        return "Fresh successful cellular test required"
    periods: dict = {}
    for r in mobile:
        local = datetime.fromtimestamp(r["stamp"], MOSCOW)
        periods.setdefault(local.date(), {})[local.hour >= 12] = r["ok"]
    if sum(len(halves) == 2 and all(halves.values()) for halves in periods.values()) < 3:
        return "Morning and evening cellular tests required on three days"
    return None


def date_at(ts: int) -> date:
    return datetime.fromtimestamp(ts, MOSCOW).date()


@app.command()
def approve(node: str = typer.Option(...), profile: str = typer.Option(...)):
    """Qualify one measured profile for normal Happ rendering."""
    inv = store.load_inventory()
    n = inv.get(node)
    if not n or "happ-pilot" not in n.tags or n not in inv.active():
        raise typer.BadParameter("An active pilot node is required")
    p = next((p for p in profiles(inv) if p.node == node and p.name == profile), None)
    if p is None:
        raise typer.BadParameter("Unknown profile")
    with closing(database()) as db:
        rows = db.execute("SELECT * FROM samples WHERE key=? ORDER BY stamp,rowid", (p.key,)).fetchall()
    issue = qualification(rows, int(time.time()))
    if issue:
        raise typer.BadParameter(issue)
    if p.key not in n.happ_approved:
        n.happ_approved.append(p.key)
        store.save_inventory(inv)
    typer.echo(f"Qualified {node}/{profile}")


@app.command()
def reconcile():
    """Apply qualification to the inventory after importing measurements."""
    inv = store.load_inventory()
    changed = False
    with closing(database()) as db:
        for p in profiles(inv):
            n = inv.get(p.node)
            if n is None or n.status not in (NodeStatus.ACTIVE, NodeStatus.DEGRADED):
                continue
            rows = db.execute("SELECT * FROM samples WHERE key=? ORDER BY stamp,rowid", (p.key,)).fetchall()
            if not rows:
                continue
            now = int(time.time())
            slots = list(home_slots(rows, now).values())
            if p.key in n.happ_approved:
                if len(slots) >= 3 and not any(slots[-3:]):
                    n.happ_approved.remove(p.key)
                    changed = True
            elif qualification(rows, now) is None:
                n.happ_approved.append(p.key)
                changed = True
    if changed:
        store.save_inventory(inv)
    typer.echo("Qualification updated" if changed else "No qualification changes")


@app.command()
def account(
    provider: str,
    currency: str = typer.Option(...),
    balance: str = typer.Option(...),
    monthly: str = typer.Option(...),
    eur_rate: str = typer.Option(...),
    due: str = typer.Option(...),
    remaining_gb: Optional[int] = None,
):
    """Record balance, all-in monthly forecast, EUR per currency unit and next payment date."""
    try:
        date.fromisoformat(due)
    except ValueError:
        raise typer.BadParameter("Use YYYY-MM-DD for --due") from None
    try:
        amounts = [Decimal(v) for v in (balance, monthly, eur_rate)]
    except InvalidOperation:
        raise typer.BadParameter("Invalid decimal amount") from None
    if not all(v.is_finite() for v in amounts) or amounts[1] < 0 or amounts[2] <= 0:
        raise typer.BadParameter(
            "A finite balance, nonnegative forecast and positive exchange rate are required"
        )
    if remaining_gb is not None and remaining_gb < 0:
        raise typer.BadParameter("Remaining traffic must be nonnegative")
    with closing(database()) as db:
        db.execute(
            "INSERT OR REPLACE INTO accounts VALUES(?,?,?,?,?,?,?,?)",
            (
                provider,
                currency.upper(),
                str(balance),
                str(monthly),
                str(eur_rate),
                due,
                remaining_gb,
                date_at(int(time.time())).isoformat(),
            ),
        )
        db.commit()


@app.command()
def report():
    """Daily JSON summary of recorded attempts and manually reconciled accounts."""
    now = int(time.time())
    since = now - 86400
    with closing(database()) as db:
        samples = db.execute(
            "SELECT * FROM samples WHERE stamp>=? ORDER BY stamp,rowid", (now - 4 * 86400,)
        ).fetchall()
        accounts = [dict(r) for r in db.execute("SELECT * FROM accounts ORDER BY provider")]
    groups: dict = {}
    for r in samples:
        groups.setdefault((r["node"], r["profile"], r["key"], r["network"]), []).append(r)
    summary = []
    for (node, name, _, network), history in groups.items():
        rows = [r for r in history if r["stamp"] >= since]
        slots = home_slots(history, now)
        consecutive = longest = 0
        for r in rows:
            consecutive = 0 if r["ok"] else consecutive + 1
            longest = max(longest, consecutive)
        times = sorted(r["elapsed_ms"] for r in rows if r["ok"] and r["elapsed_ms"])
        summary.append(
            {
                "node": node,
                "profile": name,
                "network": network,
                "attempts": len(rows),
                "successes": sum(r["ok"] for r in rows),
                "failures": [r["stage"] for r in rows if not r["ok"]],
                "observed_slots_72h": len(slots) if network == "home" else None,
                "successful_slots_72h": sum(slots.values()) if network == "home" else None,
                "longest_failed_run": longest,
                "last_success": next((r["stamp"] for r in reversed(history) if r["ok"]), None),
                "last_check": history[-1]["stamp"],
                "last_result": history[-1]["stage"],
                "transfer_p50_ms": times[math.ceil(0.50 * len(times)) - 1] if times else None,
                "transfer_p95_ms": times[math.ceil(0.95 * len(times)) - 1] if times else None,
                "last_exit_check_ms": history[-1]["connect_ms"] or None,
            }
        )
    forecast = sum((Decimal(a["monthly"]) * Decimal(a["rate"]) for a in accounts), Decimal("0"))
    for a in accounts:
        a["payment_due_soon"] = date.fromisoformat(a["due"]) <= date_at(now) + timedelta(days=7)
    typer.echo(
        json.dumps(
            {
                "as_of": datetime.now(timezone.utc).isoformat(),
                "window_hours": 24,
                "profiles": summary,
                "accounts": accounts,
                "budget_eur": 60,
                "forecast_eur": str(forecast) if accounts else None,
                "budget_warning": forecast >= 48,
            },
            indent=2,
        )
    )


@app.command("server-report")
def server_report(node: str):
    """Read the daily service and network snapshots from one pilot VPS."""
    from .server.bootstrap import _connect, _run

    n = store.load_inventory().get(node)
    if n is None or "happ-pilot" not in n.tags:
        raise typer.BadParameter("Select a pilot node")
    with _connect(n, settings, None) as ssh:
        typer.echo(_run(ssh, "tail -n +1 /var/lib/outpost/health/????-??-??"))


@app.command("export-targets")
def export_targets(out: Path = typer.Option(...)):
    """Private client-only manifest for the independent RU probe."""
    private_file(out, json.dumps([asdict(p) for p in selected(None, None)]))


@app.command("export-samples")
def export_samples(out: Path = typer.Option(...)):
    """Export the last four days of home measurements for the operator."""
    with closing(database()) as db:
        rows = db.execute(
            "SELECT stamp,key,stage,bytes,elapsed_ms,connect_ms FROM samples "
            "WHERE network='home' AND stamp>=? ORDER BY stamp,rowid",
            (int(time.time()) - 4 * 86400,),
        )
        private_file(out, json.dumps([dict(r) for r in rows]))


@app.command("import-samples")
def import_samples(source: Path):
    """Merge observations from your trusted RU device. Re-importing a run is idempotent."""
    targets = {p.key: p for p in profiles(store.load_inventory())}
    now = int(time.time())
    with closing(database()) as db, db:
        for r in json.loads(source.read_text()):
            p = targets.get(r["key"])
            if not p or not now - 4 * 86400 <= r["stamp"] <= now:
                continue
            if r["stage"] not in {"ok", "tunnel/trace", "unexpected-exit", "transfer"}:
                raise typer.BadParameter("Unknown measurement stage")
            ok = r["stage"] == "ok"
            if ok and r["bytes"] != 1048576:
                raise typer.BadParameter("Success requires a full MiB")
            db.execute(
                """INSERT INTO samples SELECT ?,?,?,?,?,?,?,?,?,?,?
                          WHERE NOT EXISTS (SELECT 1 FROM samples
                              WHERE key=? AND network='home' AND stamp=?)""",
                (
                    r["stamp"],
                    p.node,
                    p.name,
                    p.key,
                    "home",
                    ok,
                    r["stage"],
                    r["bytes"],
                    r["elapsed_ms"],
                    r["connect_ms"],
                    "",
                    p.key,
                    r["stamp"],
                ),
            )
    reconcile()


@app.command("update")
def update_command(node: str, service: str, binary: Path, interface: str = "en0"):
    """Roll a Linux core binary to one pilot VPS with before/after tunnel checks."""
    from .pilot_update import update

    inv = store.load_inventory()
    n = inv.get(node)
    if n is None or "happ-pilot" not in n.tags or n not in inv.active():
        raise typer.BadParameter("Select one active pilot node")
    if service not in {"xray", "sing-box"}:
        raise typer.BadParameter("Choose xray or sing-box")
    candidates = [
        p
        for p in profiles(inv)
        if p.node == node
        and p.key in n.happ_approved
        and ((p.name in {"trojan", "hy2"}) == (service == "sing-box"))
    ]
    if not candidates:
        raise typer.BadParameter("Qualify a profile served by this core before rolling the binary")

    def verify() -> None:
        check_origin(interface)
        for p in candidates:
            result = measure(p.link, p.ip, p.country, interface)
            if not result.ok:
                raise RuntimeError(f"{p.node}/{p.name} failed at {result.stage}")

    update(n, service, binary, verify)
    typer.echo(f"Verified {node}/{service}")
