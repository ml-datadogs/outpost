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
    { "protocol": "freedom", "tag": "direct" }{% if ai_proxy %},
    {
      "protocol": {{ ai_proxy.scheme | tojson }},
      "tag": "ai-clean",
      "settings": {
        "servers": [
          {
            "address": {{ ai_proxy.host | tojson }},
            "port": {{ ai_proxy.port }}{% if ai_proxy.username %},
            "users": [
              {
                "user": {{ ai_proxy.username | tojson }},
                "pass": {{ (ai_proxy.password or "") | tojson }}
              }
            ]{% endif %}
          }
        ]
      }
    }
{% endif %}
  ]{% if ai_proxy %},
  "routing": {
    "domainStrategy": "AsIs",
    "rules": [
      {
        "type": "field",
        "domain": {{ ai_domains | tojson }},
        "outboundTag": "ai-clean"
      }
    ]
  }
{% endif %}
}
