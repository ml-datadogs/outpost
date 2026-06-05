#!/usr/bin/env bash
# Idempotent sing-box bootstrap. Run as root on a freshly provisioned node.
# Usage: bootstrap.sh /path/to/config.json
# Env:
#   OUTPOST_SNI    common name for the self-signed cert (default www.bing.com)
#   OUTPOST_PORTS  comma-separated ports to open, e.g. "443,8443,2053"
set -euo pipefail

CONFIG_SRC="${1:-/tmp/outpost-singbox.json}"
SNI="${OUTPOST_SNI:-www.bing.com}"
PORTS="${OUTPOST_PORTS:-443,8443,2053}"

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

if [ ! -f /etc/sing-box/cert.pem ] || [ ! -f /etc/sing-box/key.pem ]; then
  echo "[outpost] generating self-signed cert (CN=${SNI})"
  openssl req -x509 -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
    -keyout /etc/sing-box/key.pem -out /etc/sing-box/cert.pem \
    -subj "/CN=${SNI}" -days 3650 >/dev/null 2>&1
fi

echo "[outpost] installing config"
cp "$CONFIG_SRC" /etc/sing-box/config.json
sing-box check -c /etc/sing-box/config.json

echo "[outpost] enabling service"
systemctl enable sing-box >/dev/null 2>&1 || true
systemctl restart sing-box

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
