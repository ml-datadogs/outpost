#!/bin/bash
# Probe which egress TCP ports the restricted network allows, over the raw ISP
# path (bypassing Surge's TUN). Server-side listeners must be running.
# Run with:  sudo bash scripts/portsweep.sh
set -u
IP="217.144.186.48"
GW="192.168.1.1"
PORTS=(22 53 80 443 465 587 990 993 995 2052 2082 2086 2095 2096 8080 8443 8880 2053)

echo "== adding host route $IP via $GW (bypass Surge TUN) =="
route -q -n add -host "$IP" "$GW" >/dev/null 2>&1 && echo "route added" || echo "route add FAILED"

echo "== TCP egress sweep (ALLOWED = handshake completes) =="
allowed=""
for p in "${PORTS[@]}"; do
  if /usr/bin/nc -z -G 5 "$IP" "$p" >/dev/null 2>&1; then
    echo "  tcp/$p: ALLOWED"
    allowed="$allowed $p"
  else
    echo "  tcp/$p: blocked"
  fi
done

echo "== removing host route =="
route -q -n delete -host "$IP" "$GW" >/dev/null 2>&1 && echo "route removed" || echo "route remove FAILED"
echo ""
echo "ALLOWED PORTS:$allowed"
