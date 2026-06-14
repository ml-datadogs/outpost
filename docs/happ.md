# Happ client — working setup

Validated on **Happ 4.11+** (Xray-core) from a restricted network (RU ISP → NL VPS).

## Summary

| Layer | What works | What often fails |
|-------|------------|------------------|
| **Happ + Reality** | **Yes** — primary approach | — |
| Hy2 / Trojan in Happ | Sometimes on LTE | Home ISP: UDP QUIC blocked, TCP TLS DPI |

**Use VLESS + Reality (Xray on the server).** Do not rely on Hy2/Trojan from Happ on a heavily filtered path.

---

## Server architecture

Two daemons on each node:

```
┌─────────────────────────────────────────────────────────┐
│  VPS (e.g. Hostkey NL)                                    │
│                                                           │
│  sing-box                                                 │
│    UDP 443  → Hysteria2 (+ Salamander obfs)               │
│    TCP 8443 → Trojan (LE or self-signed TLS)              │
│                                                           │
│  Xray-core                                                │
│    TCP 2053 → VLESS + Reality  ← Happ uses this           │
│    TCP 443  → VLESS + Reality  (same keys, backup port)   │
└─────────────────────────────────────────────────────────┘
```

**Why two cores:** Happ runs **Xray-core** for Reality. sing-box’s Reality inbound did not handshake reliably with Happ (`REALITY: processed invalid connection`). Xray as the Reality server fixed it.

Bootstrap deploys both: `outpost/server/bootstrap.py` uploads sing-box + Xray configs; `server/bootstrap.sh` installs certs, sing-box, and Xray.

---

## Reality parameters that work

| Parameter | Value | Notes |
|-----------|-------|-------|
| Protocol | VLESS + Reality | TCP |
| Ports | **2053** (primary), **443** (backup) | Xray listens on both |
| SNI | `www.microsoft.com` | Must match server `dest` / `serverNames` |
| Fingerprint | **`firefox`** | `chrome` triggered more DPI on test path |
| Flow | **none** | Do **not** use `xtls-rprx-vision` on this path |
| `pbk` / `sid` | From inventory | Must match server keypair + `shortIds` |
| `allowInsecure` / `insecure` / `vcn` | **Never** | Happ 4.11+ rejects these |

Example share link (credentials from your inventory):

```
vless://<uuid>@<ip>:2053?encryption=none&security=reality&type=tcp&sni=www.microsoft.com&fp=firefox&pbk=<public_key>&sid=<short_id>&spx=%2F#node-reality
```

Port **443** link is identical except `:443` and name suffix `-443`.

Generate fresh links:

```bash
uv run outpost render --happ --out dist-subs
base64 -D -i dist-subs/outpost.happ.txt
```

---

## TLS for Hy2 / Trojan (optional)

Happ 4.11+ removed skip-verify flags. Two options:

### A. Real certificate (recommended if you use Hy2/Trojan)

1. DNS **A** record: `exit.example.com` → VPS IP (REG.RU: DNS only, no proxy).
2. `.env`: `OUTPOST_TLS_DOMAIN=exit.example.com`
3. Re-bootstrap: certbot on the VPS, `node.insecure=false`.
4. Happ links use the domain as host/SNI; **no** `pcs=` / `insecure=`.

### B. Self-signed + cert pin

- Links include `pcs=<hex SHA-256>` of the leaf cert only.
- No `insecure=`, `allowInsecure`, or `vcn=`.

Reality does **not** use this — it pins via `pbk` + `sid`.

---

## Bootstrap / re-deploy

```bash
# .env must include OUTPOST_TLS_DOMAIN if using Let's Encrypt
OUTPOST_TLS_DOMAIN=exit.yourdomain.ru uv run python -c "
from outpost.config import settings
from outpost.store import load_inventory, save_inventory
from outpost.server.bootstrap import bootstrap_node
inv = load_inventory(settings)
node = inv.get('<node_id>')
bootstrap_node(node, settings=settings)
inv.upsert(node)
save_inventory(inv, settings)
"
```

Bootstrap order matters: `tls_domain` is applied **before** rendering sing-box config so Trojan/Hy2 SNI matches the LE cert.

Xray config is written to `/usr/local/etc/xray/config.json` (mode **644** — the service runs as `nobody`).

---

## Happ client checklist

1. **Import only the Reality link** first (delete old profiles).
2. **Routing: Global** (not rule-only).
3. **Turn off Surge TUN** while testing Happ (macOS) — both fight for traffic.
4. If home Wi‑Fi fails, try **mobile LTE** (different DPI).
5. Prefer **2053**; switch to **443** if one port is throttled.

---

## Port layout (defaults)

| Port | Proto | Service | Client |
|------|-------|---------|--------|
| 443 | UDP | Hy2 | Happ / Surge |
| 443 | TCP | Reality | Happ (backup) |
| 8443 | TCP | Trojan | Happ / Surge |
| 2053 | TCP | Reality | Happ (primary) |

Hy2 (UDP) and Reality (TCP) can share **443** — different protocols, no bind conflict.

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| Ping OK, sites don’t load | DNS not via tunnel — enable remote DNS in Happ; use Global routing |
| `allowInsecure removed` | Old link or `insecure`/`vcn` in URL — re-render, re-import |
| Reality silent fail | Wrong port (2053 closed) or old sing-box-only Reality — re-bootstrap with Xray |
| `failed to read client hello` (server log) | ISP DPI dropping TLS ClientHello — try `fp=firefox`, no flow, other port, or LTE |
| Hy2 timeout, no server logs | UDP/443 blocked — use Reality instead |
| Trojan TLS timeout | TCP DPI on 443 — use Reality instead |

Server-side check:

```bash
ssh root@<ip> 'systemctl is-active xray sing-box; ss -tlnp | grep -E "2053|:443"; journalctl -u xray -n 20 --no-pager'
```

---

## What we learned (Hostkey NL, RU path)

1. **Reality on Xray** + **firefox** + **no Vision flow** + ports **2053/443** = stable in Happ.
2. **Let's Encrypt** on `exit.<domain>` fixes Happ 4.11 TLS for Hy2/Trojan when those paths are reachable.
3. **sing-box Reality ≠ Happ Xray client** — keep Xray for Reality even if sing-box runs everything else.
4. From RU residential ISP, **Reality worked** while Hy2/Trojan did not — protocol choice matters more than cert polish.
