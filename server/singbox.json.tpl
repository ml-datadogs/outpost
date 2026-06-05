{
  "log": { "level": "warn", "timestamp": true },
  "inbounds": [
    {
      "type": "hysteria2",
      "tag": "hy2-in",
      "listen": "::",
      "listen_port": {{ hysteria2_port }},
      "users": [{ "password": {{ hysteria2_password | tojson }} }],
      "masquerade": "https://www.bing.com",
      "tls": {
        "enabled": true,
        "alpn": ["h3"],
        "certificate_path": {{ cert_path | tojson }},
        "key_path": {{ key_path | tojson }}
      }
    },
    {
      "type": "trojan",
      "tag": "trojan-in",
      "listen": "::",
      "listen_port": {{ trojan_port }},
      "users": [{ "password": {{ trojan_password | tojson }} }],
      "tls": {
        "enabled": true,
        "server_name": {{ server_name | tojson }},
        "certificate_path": {{ cert_path | tojson }},
        "key_path": {{ key_path | tojson }}
      }
    },
    {
      "type": "vless",
      "tag": "reality-in",
      "listen": "::",
      "listen_port": {{ reality_port }},
      "users": [{ "uuid": {{ reality_uuid | tojson }}, "flow": "xtls-rprx-vision" }],
      "tls": {
        "enabled": true,
        "server_name": {{ reality_dest | tojson }},
        "reality": {
          "enabled": true,
          "handshake": { "server": {{ reality_dest | tojson }}, "server_port": 443 },
          "private_key": {{ reality_private_key | tojson }},
          "short_id": [{{ reality_short_id | tojson }}]
        }
      }
    }
  ],
  "outbounds": [
    { "type": "direct", "tag": "direct" }
  ]
}
