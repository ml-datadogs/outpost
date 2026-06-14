{
  "log": { "loglevel": "info" },
  "inbounds": [
{% for port in reality_ports %}
    {
      "listen": "0.0.0.0",
      "port": {{ port }},
      "protocol": "vless",
      "tag": "reality-in-{{ port }}",
      "settings": {
        "clients": [
          {
            "id": {{ reality_uuid | tojson }}
          }
        ],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "show": false,
          "dest": {{ (reality_dest ~ ":443") | tojson }},
          "xver": 0,
          "serverNames": [{{ reality_dest | tojson }}],
          "privateKey": {{ reality_private_key | tojson }},
          "shortIds": [{{ reality_short_id | tojson }}, ""]
        }
      },
      "sniffing": {
        "enabled": true,
        "destOverride": ["http", "tls"]
      }
    }{% if not loop.last %},{% endif %}
{% endfor %}
  ],
  "outbounds": [
    { "protocol": "freedom", "tag": "direct" }
  ]
}
