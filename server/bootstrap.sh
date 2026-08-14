#!/usr/bin/env bash
# Idempotent sing-box bootstrap. Run as root on a freshly provisioned node.
# Usage: bootstrap.sh /path/to/config.json
# Env:
#   OUTPOST_SNI           TLS CN / fallback SNI (default www.bing.com)
#   OUTPOST_TLS_DOMAIN    if set, obtain Let's Encrypt cert for this domain
#   OUTPOST_PORTS         comma-separated ports to open, e.g. "443,2053"
#   OUTPOST_XRAY_CONFIG   if set, install Xray-core and apply this Reality config
set -euo pipefail

CONFIG_SRC="${1:-/tmp/outpost-singbox.json}"
XRAY_CONFIG_SRC="${OUTPOST_XRAY_CONFIG:-}"
SNI="${OUTPOST_SNI:-www.bing.com}"
TLS_DOMAIN="${OUTPOST_TLS_DOMAIN:-}"
PORTS="${OUTPOST_PORTS:-443,2053}"

echo "[outpost] installing prerequisites"
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y >/dev/null
  apt-get install -y curl openssl ca-certificates >/dev/null
fi

if ! command -v sing-box >/dev/null 2>&1; then
  echo "[outpost] installing sing-box"
  curl -fsSL https://sing-box.app/install.sh | sh
fi

mkdir -p /etc/sing-box

if [ -n "$TLS_DOMAIN" ]; then
  echo "[outpost] obtaining Let's Encrypt cert for ${TLS_DOMAIN}"
  apt-get install -y certbot >/dev/null
  systemctl stop sing-box 2>/dev/null || true
  # HTTP-01 answers on :80, so it must be open here and at every renewal.
  if command -v ufw >/dev/null 2>&1; then
    ufw allow 80/tcp >/dev/null 2>&1 || true
  fi
  certbot certonly --standalone -d "$TLS_DOMAIN" --non-interactive --agree-tos \
    --register-unsafely-without-email --preferred-challenges http

  # Renewal writes only to /etc/letsencrypt; without this hook the copies below go
  # stale and TLS silently breaks ~90 days later.
  mkdir -p /etc/letsencrypt/renewal-hooks/deploy
  cat > /etc/letsencrypt/renewal-hooks/deploy/outpost.sh <<'HOOK'
#!/usr/bin/env bash
set -eu
DOMAIN_DIR="${RENEWED_LINEAGE:-}"
[ -n "$DOMAIN_DIR" ] || exit 0
install -m 644 "${DOMAIN_DIR}/fullchain.pem" /etc/sing-box/cert.pem
install -m 600 "${DOMAIN_DIR}/privkey.pem"  /etc/sing-box/key.pem
openssl x509 -in /etc/sing-box/cert.pem -noout -fingerprint -sha256 \
  | sed 's/sha256 Fingerprint=//I; s/://g' | tr '[:upper:]' '[:lower:]' \
  > /etc/sing-box/cert.sha256
systemctl restart sing-box 2>/dev/null || true
systemctl restart xray 2>/dev/null || true
HOOK
  chmod +x /etc/letsencrypt/renewal-hooks/deploy/outpost.sh

  install -m 644 "/etc/letsencrypt/live/${TLS_DOMAIN}/fullchain.pem" /etc/sing-box/cert.pem
  install -m 600 "/etc/letsencrypt/live/${TLS_DOMAIN}/privkey.pem" /etc/sing-box/key.pem
elif [ ! -f /etc/sing-box/cert.pem ] || [ ! -f /etc/sing-box/key.pem ]; then
  echo "[outpost] generating self-signed cert (CN=${SNI})"
  openssl req -x509 -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
    -keyout /etc/sing-box/key.pem -out /etc/sing-box/cert.pem \
    -subj "/CN=${SNI}" -days 3650 >/dev/null 2>&1
fi

# Fingerprint for Happ/Xray share links (pcs / pinSHA256); hex lowercase, no colons.
openssl x509 -in /etc/sing-box/cert.pem -noout -fingerprint -sha256 \
  | sed 's/sha256 Fingerprint=//I; s/://g' | tr '[:upper:]' '[:lower:]' \
  > /etc/sing-box/cert.sha256

echo "[outpost] installing config"
cp "$CONFIG_SRC" /etc/sing-box/config.json
sing-box check -c /etc/sing-box/config.json

echo "[outpost] enabling service"
systemctl enable sing-box >/dev/null 2>&1 || true
systemctl restart sing-box

if [ -n "$XRAY_CONFIG_SRC" ] && [ -f "$XRAY_CONFIG_SRC" ]; then
  echo "[outpost] installing xray-core (Happ Reality)"
  if ! command -v xray >/dev/null 2>&1; then
    bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
  fi
  mkdir -p /usr/local/etc/xray
  install -m 644 "$XRAY_CONFIG_SRC" /usr/local/etc/xray/config.json
  xray run -test -config /usr/local/etc/xray/config.json
  systemctl enable xray >/dev/null 2>&1 || true
  systemctl restart xray
fi

if command -v ufw >/dev/null 2>&1; then
  echo "[outpost] opening firewall ports ${PORTS}"
  IFS=',' read -ra PARR <<< "$PORTS"
  for p in "${PARR[@]}"; do
    ufw allow "${p}/tcp" >/dev/null 2>&1 || true
    ufw allow "${p}/udp" >/dev/null 2>&1 || true
  done
fi

echo "[outpost] done"
systemctl --no-pager status sing-box | head -n 5 || true
if [ -n "$XRAY_CONFIG_SRC" ] && [ -f "$XRAY_CONFIG_SRC" ]; then
  systemctl --no-pager status xray | head -n 5 || true
fi
