# Outpost

Automated proxy fleet for restricted networks. Outpost provisions fresh VPS proxies
on RU-friendly hosts (iteration 1: **Zomro** + **Aeza** + **Hostkey**, non-RU regions), bootstraps a
[`sing-box`](https://sing-box.sagernet.org/) stack over SSH, and renders **one
token-gated subscription URL** that auto-updates in both **Surge** and **Happ** — with
CI + on-Mac monitoring that **auto-rotates blocked nodes**.

> Goal: a working exit **outside** Russian jurisdiction/RKN, reachable from inside it,
> on providers that accept Russian payment.

## Protocols

Each node runs **sing-box** (Hy2 + Trojan) and **Xray-core** (Reality for Happ):

| Protocol | Transport | Server | Surge | Happ | Notes |
|---|---|---|---|---|---|
| Hysteria 2 | UDP/QUIC | sing-box :443 | yes | yes | Often blocked on RU UDP paths |
| Trojan | TCP/TLS | sing-box :8443 | yes | yes | Often DPI’d on RU TCP paths |
| VLESS + Reality | TCP | **Xray** :2053 + :443 | no | yes | **Primary for Happ** — see [docs/happ.md](docs/happ.md) |

The Surge subscription contains Hysteria2 + Trojan; the Happ subscription contains all
three (two Reality links: ports 2053 and 443). Both render from one source of truth (the inventory).

**Happ 4.11+:** no `allowInsecure` / `insecure` / `vcn`; use Let's Encrypt (`OUTPOST_TLS_DOMAIN`) or `pcs=` hex pin for Hy2/Trojan. Reality uses `pbk` + `sid` only.

## Three-gate provider eligibility

A `(provider, region)` may host an exit only if **all three** hold:

1. **Not Russia** — `region.country != RU` (a RU exit bypasses nothing).
2. **Reachable** — the on-Mac probe confirmed the region's canary IP is reachable from
   your ISP (`region.enabled`).
3. **Policy OK** — the provider accepts RU users + usable payment (`provider.policy_ok`).

Hetzner is kept in the registry but excluded on gate 3 (it bans/suspends RU/CIS
accounts). DigitalOcean is avoided (range-blocked). See `state/registry.yaml`.

**Manual (adopt-tier) providers:** hosts with RU-friendly billing but **no
provisioning API** — currently **iphoster** — are registered too, but only for
hand-ordered boxes brought in via `outpost adopt`; rotation never auto-provisions
or API-destroys them. Public tested configs (igareck/vpn-configs-for-russia) are
the emergency tier below that — see [docs/fallbacks.md](docs/fallbacks.md).

## Architecture

```
 local CLI (Mac) --provision/rotate-->  Aeza/Zomro/Hostkey API  --> VPS (sing-box) via SSH
        |                                                          ^
        v                                                          |
 state/inventory.enc.yaml  --render-->  Surge sub + Happ sub       |
        ^                                   |                       |
   Mac launchd probe (route-bypass) --------+                       |
        |  reachability scores                                      |
        v                                                           |
 GitHub Actions (every 15m): monitor -> rotate -> render -> Cloudflare KV
                                                          |
                                          Surge / Happ <--+ (token-gated Worker)
```

## Install

Uses [uv](https://docs.astral.sh/uv/). The Python version is pinned in
`.python-version` (3.12) and uv will fetch it automatically.

```bash
uv sync                # creates .venv from uv.lock (incl. dev group)
cp .env.example .env   # then fill it in
```

Run anything through uv (no manual venv activation needed):

```bash
uv run outpost --help
uv run pytest -q
```

Generate the bootstrap SSH key:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/outpost_ed25519 -N ""
```

(Recommended) install `sops` + `age` so the inventory is encrypted at rest:

```bash
brew install sops age
age-keygen -o ~/.config/sops/age/outpost.key      # copy the printed public key
# paste it into .sops.yaml (replace the placeholder age1... recipient)
export SOPS_AGE_KEY_FILE=~/.config/sops/age/outpost.key
```

## Credentials (`.env`)

| Var | What |
|---|---|
| `AEZA_API_KEY` | https://my.aeza.net/settings/apikeys |
| `ZOMRO_AUTH` | Zomro API session token (api.zomro.com) |
| `HOSTKEY_API_KEY` | Hostkey InvAPI account key (works after you have an active server) |
| `HOSTKEY_EMAIL` / `HOSTKEY_PASSWORD` | Invapi panel login — **required to order the first server** |
| `HOSTKEY_TRAFFIC_PLAN` | Optional traffic plan id (auto-tried; use `37` for RU/whmcs_itb if needed) |
| `HOSTKEY_DEPLOY_OPTIONS` | Optional billing endpoint (e.g. `whmcs_itb` for RU accounts) |
| `OUTPOST_SSH_PUBLIC_KEY_FILE` / `..._PRIVATE_KEY_FILE` | bootstrap SSH key |
| `OUTPOST_SUB_TOKEN` | secret path segment for the subscription URL |
| `OUTPOST_DNS_ZONE` / `CLOUDFLARE_API_TOKEN` | per-node DNS + real certs — see [docs/dns-tls.md](docs/dns-tls.md) |
| `OUTPOST_TLS_DOMAIN` | legacy single-domain fallback (only one node can use it) |
| `OUTPOST_LAN_GATEWAY` / `OUTPOST_LAN_DNS` | for the route-bypass probe |

## Usage

```bash
# 1. Discover live regions/products and fill state/registry.yaml product_ref values
uv run outpost discover --provider aeza  --what regions
uv run outpost discover --provider zomro --what products
uv run outpost discover --provider zomro --what os        # note the OS uid for Zomro
uv run outpost discover --provider hostkey --what regions # preset/location/os_id for registry

# 2. Provision an exit (picks an eligible non-RU region automatically)
uv run outpost provision --provider zomro
uv run outpost provision --provider aeza --region Netherlands
uv run outpost provision --provider hostkey --region 108-NL

# 3. See the fleet
uv run outpost list

# 4. Render subscriptions
uv run outpost render --out dist-subs     # writes outpost.surge.conf + outpost.happ.txt

# 5. Adopt a hand-made server (no provider API)
uv run outpost adopt --ip 1.2.3.4 --country KZ
uv run outpost adopt --provider iphoster --ip 1.2.3.4 --country DE --password <root-pw>

# 6. Give nodes their own DNS name + real cert (after setting OUTPOST_DNS_ZONE)
uv run outpost recert          # all active nodes; or: recert <id>

# Monitoring / rotation (also run by CI)
uv run outpost probe          # reachability from inside the restricted network (needs sudo route)
uv run outpost monitor        # liveness + status transitions
uv run outpost rotate --reap  # replace blocked/down nodes, destroy retired
uv run outpost destroy <id>
```

> Note on provider responses: Aeza/Zomro/Hostkey field shapes are handled defensively but a few
> (product location fields, Zomro instance-list keys) may need minor tweaks against the
> live API. `outpost discover` prints raw payloads to verify, and the provider clients
> centralize these in `outpost/providers/{aeza,zomro,hostkey}.py`.

## Subscription URL (Cloudflare Worker)

The subscription holds live credentials, so it is served from a **token-gated** Worker.

```bash
cd worker
npx wrangler kv namespace create SUBS     # paste the id into wrangler.toml
npx wrangler secret put SUB_TOKEN          # use the same value as OUTPOST_SUB_TOKEN
npx wrangler deploy
```

URLs (replace host + token):

- Surge: `https://outpost-subs.<you>.workers.dev/<SUB_TOKEN>/surge`
- Happ:  `https://outpost-subs.<you>.workers.dev/<SUB_TOKEN>/happ`
- Emergency public configs (tier 3, [docs/fallbacks.md](docs/fallbacks.md)):
  `…/<SUB_TOKEN>/fallback` and `…/<SUB_TOKEN>/fallback-white`

CI publishes the rendered bodies into KV (`surge` / `happ` keys) after each render.
To publish manually:

```bash
npx wrangler kv key put --namespace-id <ID> surge --path dist-subs/outpost.surge.conf
npx wrangler kv key put --namespace-id <ID> happ  --path dist-subs/outpost.happ.txt
```

### Add it in Surge
Settings → Subscription (or `[Proxy]` provider) → add the `/surge` URL. Surge fetches
the managed proxies and keeps them updated.

### Add it in Happ
Add subscription → paste the `/happ` URL. Happ decodes the base64 share-link bundle
(Hysteria2 + Trojan + Reality) and refreshes automatically.

## On-Mac probe (launchd)

The probe runs **inside** the restricted network and is the authority on reachability
(CI runners abroad can't see RU blocking). It uses a temporary host route via your real
gateway to bypass Surge Enhanced Mode.

```bash
# allow passwordless route (visudo):  <user> ALL=(root) NOPASSWD: /sbin/route
cp agent/com.outpost.probe.plist ~/Library/LaunchAgents/   # edit paths first
launchctl load ~/Library/LaunchAgents/com.outpost.probe.plist
```

## CI (GitHub Actions)

`.github/workflows/monitor.yml` runs every 15 min: `monitor → rotate --reap → render →
publish to KV → commit state`. `.github/workflows/fallback.yml` runs every 6 h:
`outpost fallback` mirrors the tier-3 public configs
(igareck/vpn-configs-for-russia) into KV (`fallback` / `fallback-white` keys); it
needs only the Cloudflare secrets and the same `CF_OK` variable. Required repository **secrets**:

`AGE_KEY`, `AEZA_API_KEY`, `AEZA_PIN`, `ZOMRO_AUTH`, `HOSTKEY_API_KEY`,
`HOSTKEY_EMAIL`, `HOSTKEY_PASSWORD`, `OUTPOST_SSH_PRIVATE_KEY`,
`OUTPOST_SSH_PUBLIC_KEY`, `OUTPOST_TLS_DOMAIN` (optional),
`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `KV_NAMESPACE_ID`.

Rotation provisions replacements, so a provider whose credentials are missing here
is unusable as a rotation target even when the registry lists it.

Repository **variables**: `CF_OK=true` once the Worker + KV exist (enables
publishing), `OUTPOST_DNS_ZONE` for per-node DNS + certs, and optionally
`HOSTKEY_TRAFFIC_PLAN` / `HOSTKEY_DEPLOY_OPTIONS`.

## Security

- The subscription contains live credentials → keep `SUB_TOKEN` secret; rotating it
  invalidates all clients.
- `state/inventory.yaml` (plaintext) is gitignored; commit only `inventory.enc.yaml`.
- Never commit `.env`, SSH private keys, or the age key.

## Layout

```
outpost/            Python package (models, providers, render, orchestrator, monitor, rotate, cli)
server/             singbox.json.tpl + bootstrap.sh
worker/             Cloudflare Worker (token-gated subscription)
agent/              on-Mac launchd reachability probe
state/              registry.yaml (committed) + inventory (managed)
.github/workflows/  monitor + rotate schedule
tests/              pytest suite
```

## Code quality

Lint/format with [ruff](https://docs.astral.sh/ruff/) and type-check with
[ty](https://github.com/astral-sh/ty). Install the git hooks once:

```bash
uv run pre-commit install
```

Run everything the way CI does:

```bash
uv run ruff format --check .   # formatting
uv run ruff check .            # lint
uv run ty check                # types
uv run pytest -q               # tests
```

`.github/workflows/ci.yml` runs all four on every push / PR.

## Tests

```bash
uv run pytest -q
```
