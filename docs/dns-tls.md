# Per-node DNS and real TLS certificates

Every node gets its own hostname under a Cloudflare zone and its own Let's Encrypt
certificate, replacing the fleet-wide `OUTPOST_TLS_DOMAIN`.

## Why per-node

`OUTPOST_TLS_DOMAIN` names **one** host. A domain resolves to one IP, so only one
node in the fleet can pass an ACME challenge for it — every other node silently
falls back to a self-signed cert (`insecure: true` plus a `pcs=` fingerprint pin).
That is worse on three counts:

- Happ 4.11+ dropped `allowInsecure`; self-signed nodes depend on the pin, and a
  re-bootstrap changes the pin and breaks existing clients until they refresh.
- A self-signed cert on :443 is a DPI signal; a real chain for a real domain
  looks like an ordinary HTTPS host.
- Rotation makes it worse: each replacement node needs a cert on first boot.

With a zone configured, node `4bd1b6` becomes `exit-4bd1b6.dirtyinfra.xyz` and
carries a genuine certificate for that name.

## Configuration

```bash
OUTPOST_DNS_ZONE=dirtyinfra.xyz        # zone you control in Cloudflare
CLOUDFLARE_API_TOKEN=...               # Zone:DNS:Edit on that zone
OUTPOST_DNS_PREFIX=exit                # optional; default "exit"
```

The token needs exactly **Zone → DNS → Edit** plus **Zone → Zone → Read**, scoped
to the one zone. Prefer a dedicated token over reusing the cluster's cert-manager
token: outpost's copy lives in a different repo's CI, so a leak there should not
grant DNS control to anything else.

Leave `OUTPOST_DNS_ZONE` unset and everything behaves as before (global
`OUTPOST_TLS_DOMAIN`, or self-signed when that is unset too).

## Lifecycle

| Moment | What happens |
|---|---|
| `provision` / `adopt` | after the IP is known, an **A record** is created, then bootstrap runs certbot |
| bootstrap | HTTP-01 on :80 (opened in ufw), cert installed, renewal deploy hook written |
| renewal (auto, ~60 days) | certbot renews; the deploy hook re-copies the cert, refreshes the pin file, restarts sing-box + xray |
| `destroy` / `rotate --reap` | the A record is deleted with the node |

Records are always created **unproxied** (grey cloud). Cloudflare's proxy only
carries HTTP(S); proxying an exit would break Hysteria2 (UDP/QUIC), Trojan, and
Reality, all of which need raw TCP/UDP to the origin. `release_hostname` also
refuses to delete a name outside the configured zone, so a hand-set domain on a
node is never touched.

## Migrating existing nodes

```bash
uv run outpost recert            # all active nodes
uv run outpost recert 4bd1b6     # one node
```

`recert` assigns the DNS name, re-runs bootstrap (issuing the cert), and saves the
inventory. Re-render and republish afterwards so clients pick up the new SNI:

```bash
uv run outpost render --out dist-subs
```

Clients on a pinned self-signed node keep working until they refresh the
subscription — the pin changes when the cert does, so refresh promptly.

## CI

`.github/workflows/monitor.yml` passes `CLOUDFLARE_API_TOKEN` (secret) and
`OUTPOST_DNS_ZONE` (variable) so rotated nodes get DNS + certs unattended. Without
them, rotation still works but replacements land on self-signed certs.

## Troubleshooting

**`certbot` fails with a connection/timeout error** — the A record must resolve
before bootstrap and :80 must be reachable. Check the record exists and is grey
cloud, and that the provider's own firewall allows 80/tcp.

**Rate limits** — Let's Encrypt allows 50 certs per registered domain per week.
Frequent rotation across many nodes can hit that; each node uses a distinct
subdomain, which shares the domain's limit.

**Verifying from the Mac** — local resolution can be intercepted by Surge's
Enhanced Mode (answers from `198.18.0.0/15` are fake-IP mappings, not real
records). Query an external resolver directly:

```bash
dig +short @1.1.1.1 exit-4bd1b6.dirtyinfra.xyz
```
