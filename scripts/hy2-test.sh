#!/bin/bash
# Test Hysteria2 (UDP/443) over the raw ISP path, bypassing Surge's TUN.
# Run with:  sudo bash scripts/hy2-test.sh
set -u
IP="217.144.186.48"
GW="192.168.1.1"
SB_BIN="$(command -v sing-box || echo /opt/homebrew/bin/sing-box)"
CFG="/tmp/hy2-selftest.json"

echo "== adding host route $IP via $GW (bypass Surge TUN) =="
route -q -n add -host "$IP" "$GW" && echo "route added" || echo "route add FAILED"

echo "== Hysteria2 (UDP/443) proxy over ISP path =="
"$SB_BIN" run -c "$CFG" >/tmp/hy2.log 2>&1 &
SB=$!
sleep 4
OUT="$(curl -s --max-time 20 -x socks5h://127.0.0.1:11092 https://ifconfig.me)"
echo "  exit IP via Hysteria2: '${OUT}'  (want ${IP})"
if [ "$OUT" = "$IP" ]; then
  echo "  RESULT: HYSTERIA2 WORKS over ISP path  => use UDP/QUIC; TCP proxies are DPI-blocked."
else
  echo "  RESULT: Hysteria2 also blocked. Last log lines:"
  tail -4 /tmp/hy2.log
fi
kill "$SB" 2>/dev/null

echo "== removing host route =="
route -q -n delete -host "$IP" "$GW" >/dev/null 2>&1 && echo "route removed" || echo "route remove FAILED (remove manually: sudo route delete -host $IP)"
