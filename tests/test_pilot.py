import hashlib
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from outpost.cli import app
from outpost.pilot import MOSCOW, qualification
from outpost.pilot_probe import client_outbound, curl, tunnel
from outpost.render import happ_profiles, profile_key, render_happ_links
from outpost.server.xray import render_xray_config
from typer.testing import CliRunner


def test_pilot_feed_only_contains_qualified_link(node, inventory):
    node.tags.append("happ-pilot")
    assert render_happ_links(inventory) == []
    links = happ_profiles(node)
    assert len(links) == 6
    node.happ_approved = [profile_key(links["xhttp"], node.ip, node.country)]
    assert render_happ_links(inventory) == [links["xhttp"]]
    node.ports.xhttp += 1
    assert render_happ_links(inventory) == []


def test_manual_qualification():
    now = datetime(2026, 11, 20, 20, tzinfo=MOSCOW)
    samples = []
    for day in range(4):
        for hour in [8, 19]:
            stamp = int(((now - timedelta(days=3 - day)).replace(hour=hour)).timestamp())
            for network in ["home", "mobile"]:
                samples.append({"stamp": stamp, "ok": True, "network": network})
    assert qualification(samples, int(now.timestamp()))
    for i in range(289):
        samples.append({"stamp": int(now.timestamp()) - (288 - i) * 900, "ok": True, "network": "home"})
    samples.sort(key=lambda r: r["stamp"])
    assert qualification(samples, int(now.timestamp())) is None
    assert qualification(samples[:4], int(now.timestamp()))
    assert qualification(samples[-4:], int(now.timestamp()))
    samples[-1]["ok"] = False
    assert qualification(samples, int(now.timestamp()))


def test_budget_and_empty_pilot(tmp_path, monkeypatch):
    from outpost.config import settings

    monkeypatch.setattr(settings, "state_dir", tmp_path)
    runner = CliRunner()
    res = runner.invoke(
        app,
        [
            "pilot",
            "account",
            "mivocloud",
            "--currency",
            "EUR",
            "--monthly",
            "48",
            "--balance",
            "12.50",
            "--eur-rate",
            "1",
            "--due",
            "2026-12-01",
        ],
    )
    assert res.exit_code == 0, res.output
    summary = json.loads(runner.invoke(app, ["pilot", "report"]).output)
    assert summary["budget_warning"] and summary["forecast_eur"] == "48"
    assert summary["profiles"] == []


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.parametrize("profile", ["xhttp", "gecko"])
def test_real_xray_pilot_roundtrip(profile, node, tmp_path):
    """Real client, generated listener, TLS pin, SOCKS and a full MiB over loopback."""
    core = os.getenv("OUTPOST_XRAY") or shutil.which("xray")
    if not core:
        pytest.skip("set OUTPOST_XRAY to run actual transport integration checks")
    interface = "lo0" if sys.platform == "darwin" else "lo"
    node.tags.append("happ-pilot")
    node.ip = "127.0.0.1"
    node.ports.xhttp, node.ports.gecko = free_port(), free_port()
    private, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            f"/CN={node.sni}",
            "-keyout",
            str(private),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    node.tls_cert_sha256 = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
    config = json.loads(render_xray_config(node))
    for inbound in config["inbounds"]:
        if inbound["streamSettings"]["security"] == "reality":
            inbound.update(listen="127.0.0.1", port=free_port())
            continue
        inbound["listen"] = "127.0.0.1"
        inbound["streamSettings"]["tlsSettings"]["certificates"] = [
            {"certificateFile": str(cert), "keyFile": str(private)}
        ]
    server = tmp_path / "server.json"
    server.write_text(
        json.dumps(
            {
                **config,
                "outbounds": [
                    {
                        "protocol": "freedom",
                        "settings": {"finalRules": [{"action": "allow", "ip": ["127.0.0.1"]}]},
                    }
                ],
            }
        )
    )
    link = happ_profiles(node)[profile]
    (tmp_path / "payload").write_bytes(os.urandom(1048576))
    http = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    with subprocess.Popen(
        [core, "run", "-c", str(server)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ) as process:
        try:
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", node.ports.xhttp), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.05)
            with tunnel(client_outbound(link, node.ip, interface), core) as proxy:
                r = curl(
                    f"http://127.0.0.1:{http.server_port}/payload",
                    ["--noproxy", "", "--proxy", proxy],
                    1048576,
                )
                assert r.returncode == 0, r.stderr.decode()
                assert r.stdout == (tmp_path / "payload").read_bytes()
        finally:
            process.terminate()
            http.shutdown()
            http.server_close()


def test_slot_thresholds_and_retries():
    end = datetime(2026, 11, 20, 21, tzinfo=MOSCOW)
    now = int(end.timestamp())
    rows: list[dict] = [{"stamp": now - 73 * 3600, "ok": True, "network": "home"}]
    for days in range(3):
        for hour in (8, 19):
            t = (end - timedelta(days=days)).replace(hour=hour)
            rows.append({"stamp": int(t.timestamp()), "ok": True, "network": "mobile"})
    for i in range(260):
        rows.append({"stamp": now - (260 - i) * 900, "ok": i >= 2, "network": "home"})
    rows.sort(key=lambda r: r["stamp"])
    assert qualification(rows, now) is None
    failed = next(r for r in rows if not r["ok"])
    rows.append(dict(failed, ok=True, stamp=failed["stamp"] + 1))
    third = next(r for r in rows if r["stamp"] == now - 258 * 900 and r["network"] == "home")
    third["ok"] = False
    rows.sort(key=lambda r: r["stamp"])
    assert "257/260" in (qualification(rows, now) or "")
    rows.remove(third)
    assert "259/288" in (qualification(rows, now) or "")


def test_import_twice_and_short_transfer_rollback(tmp_path, monkeypatch, inventory, node):
    from outpost import pilot

    node.tags.append("happ-pilot")
    monkeypatch.setattr(pilot.settings, "state_dir", tmp_path)
    monkeypatch.setattr(pilot.store, "load_inventory", lambda: inventory)
    monkeypatch.delenv("OUTPOST_HAPP_CONTROL_FILE", raising=False)
    p = pilot.profiles(inventory)[0]
    r = {
        "stamp": int(time.time()),
        "key": p.key,
        "stage": "ok",
        "bytes": 1048576,
        "elapsed_ms": 40,
        "connect_ms": 10,
    }
    src = tmp_path / "samples"
    src.write_text(json.dumps([r]))
    pilot.import_samples(src)
    pilot.import_samples(src)
    src.write_text(json.dumps([dict(r, stamp=r["stamp"] - 2), dict(r, bytes=1337)]))
    with pytest.raises(Exception, match="full MiB"):
        pilot.import_samples(src)
    db = pilot.database()
    try:
        assert db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1
    finally:
        db.close()


def test_update_rolls_back_on_failed_postcheck(node, tmp_path, monkeypatch):
    from unittest.mock import MagicMock, Mock

    from outpost import pilot_update

    monkeypatch.setattr(pilot_update.settings, "state_dir", tmp_path)
    monkeypatch.setattr(pilot_update, "_connect", MagicMock())
    commands = []

    def remote(client, cmd):
        commands.append(cmd)
        if "MainPID" in cmd:
            return "42"
        if "readlink" in cmd:
            return "/usr/local/bin/xray"
        return ""

    monkeypatch.setattr(pilot_update, "_run", remote)
    binary = tmp_path / "linux-xray"
    binary.touch()
    with pytest.raises(RuntimeError, match="transfer failed"):
        pilot_update.update(node, "xray", binary, Mock(side_effect=[None, RuntimeError("transfer failed")]))
    armed = next(i for i, c in enumerate(commands) if c.startswith("systemd-run"))
    restart = next(i for i, c in enumerate(commands) if c.startswith("mv -f"))
    rollback = next(i for i, c in enumerate(commands) if c.startswith("if test -f"))
    assert armed < restart < rollback
    assert "systemctl restart xray" in commands[rollback]
