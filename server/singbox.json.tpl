{
  "log": { "level": "warn", "timestamp": true },
  "inbounds": [
    {
      "type": "hysteria2",
      "tag": "hy2-in",
      "listen": "::",
      "listen_port": {{ hysteria2_port }},
      "users": [{ "password": {{ hysteria2_password | tojson }} }],
      {% if hysteria2_obfs_password %}
      "obfs": {
        "type": "salamander",
        "password": {{ hysteria2_obfs_password | tojson }}
      },
      {% endif %}
      "ignore_client_bandwidth": true,
      "masquerade": "https://www.bing.com",
      "tls": {
        "enabled": true,
        "server_name": {{ server_name | tojson }},
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
    }
  ],
  "outbounds": [
    { "type": "direct", "tag": "direct" }{% if ai_proxy %},
    {
      "type": {{ ai_proxy.scheme | tojson }},
      "tag": "ai-clean",
      "server": {{ ai_proxy.host | tojson }},
      "server_port": {{ ai_proxy.port }}{% if ai_proxy.username %},
      "username": {{ ai_proxy.username | tojson }}{% endif %}{% if ai_proxy.password %},
      "password": {{ ai_proxy.password | tojson }}{% endif %}
    }
{% endif %}
  ]{% if ai_proxy %},
  "route": {
    "final": "direct",
    "rules": [
      {
        "domain_suffix": {{ ai_domains | tojson }},
        "outbound": "ai-clean"
      }
    ]
  }
{% endif %}
}
