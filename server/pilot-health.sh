#!/usr/bin/env bash
# Daily VPS-side snapshot for the owner. Never record client addresses or destinations.
set -eu
install -d -m 700 /var/lib/outpost/health
umask 077
file=$(mktemp /var/lib/outpost/health/.snapshot.XXXXXX)
trap 'rm -f "$file"' EXIT
iface=$(ip -4 route show default | awk 'NR == 1 {print $5}')
{
  date --utc '+utc=%FT%TZ'
  printf 'boot_id=%s\n' "$(cat /proc/sys/kernel/random/boot_id)"
  for service in xray sing-box; do
    printf 'service=%s\n' "$service"
    systemctl show "$service" -p ActiveState -p NRestarts
  done
  awk '{print "load1=" $1}' /proc/loadavg
  for stat in rx_bytes tx_bytes rx_dropped tx_dropped; do
    printf '%s=%s\n' "$stat" "$(cat "/sys/class/net/$iface/statistics/$stat")"
  done
  awk '/^Tcp:/{if (!first++) {for(i=2;i<=NF;i++) name[i]=$i} else {
    for(i=2;i<=NF;i++) if(name[i]~/^(EstabResets|RetransSegs|CurrEstab)$/) print name[i]"="$i
  }}' /proc/net/snmp
  openssl x509 -in /etc/sing-box/cert.pem -noout -enddate
} > "$file"
mv -f "$file" "/var/lib/outpost/health/$(date -u +%F)"
find /var/lib/outpost/health -type f -mtime +31 -delete
