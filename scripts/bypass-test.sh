#!/bin/bash
# ISP-path reachability test: bypass Surge's TUN with a temporary host route,
# then probe the server at multiple layers to tell IP-block vs port-block apart.
# Run with:  sudo bash scripts/bypass-test.sh
set -u
IP="217.144.186.48"
GW="192.168.1.1"
SB_BIN="$(command -v sing-box || echo /opt/homebrew/bin/sing-box)"
CFG="/tmp/srv-selftest.json"

echo "== adding host route $IP via $GW (bypass Surge TUN) =="
route -q -n add -host "$IP" "$GW" && echo "route added" || echo "route add FAILED"

echo "== ICMP ping (is the IP reachable at all?) =="
ping -c 3 -t 5 "$IP" | tail -3

echo "== raw TCP reachability over ISP path =="
for p in 22 443 2053 8443; do
  if /usr/bin/nc -z -G 6 "$IP" "$p" >/dev/null 2>&1; then
    echo "  tcp/$p: REACHABLE"
  else
    echo "  tcp/$p: BLOCKED/timeout"
  fi
done

echo "== Reality proxy over ISP path =="
"$SB_BIN" run -c "$CFG" >/tmp/bypass.log 2>&1 &
SB=$!
sleep 4
OUT="$(curl -s --max-time 15 -x socks5h://127.0.0.1:11090 https://ifconfig.me)"
echo "  exit IP via Reality: '${OUT}'  (want ${IP})"
kill "$SB" 2>/dev/null

echo "== removing host route =="
route -q -n delete -host "$IP" "$GW" >/dev/null 2>&1 && echo "route removed" || echo "route remove FAILED (remove manually: sudo route delete -host $IP)"

echo ""
echo "INTERPRETATION:"
echo "  - ping OK + tcp/22 OK but tcp/2053+8443 BLOCKED  => PORT-level block (try 443)."
echo "  - ping FAIL + all tcp BLOCKED                    => IP/subnet block (need new IP/provider)."
