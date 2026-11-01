import base64

from outpost.render import render_happ, render_happ_links, render_surge


def test_surge_includes_hy2_and_trojan_only(node, inventory):
    out = render_surge(inventory)
    assert "= hysteria2," in out
    assert "= trojan," in out
    # Surge cannot read Reality
    assert "reality" not in out.lower()
    assert node.ip in out
    assert node.secrets.hysteria2_password in out


def test_happ_links_include_all_three(node, inventory):
    links = render_happ_links(inventory)
    schemes = [link.split("://", 1)[0] for link in links]
    assert "hysteria2" in schemes
    assert "trojan" in schemes
    assert "vless" in schemes


def test_happ_subscription_is_base64(inventory):
    sub = render_happ(inventory)
    decoded = base64.b64decode(sub).decode()
    assert "hysteria2://" in decoded
    assert "vless://" in decoded


def test_happ_trojan_uses_pcs_not_allow_insecure(node, inventory):
    node.tls_cert_sha256 = "ab" * 32
    trojan = next(link for link in render_happ_links(inventory) if link.startswith("trojan://"))
    assert f"pcs={'ab' * 32}" in trojan
    assert "allowInsecure" not in trojan
    assert "vcn=" not in trojan


def test_happ_hysteria2_uses_obfs_and_pcs(node, inventory):
    node.tls_cert_sha256 = "cd" * 32
    node.secrets.hysteria2_obfs_password = "obfs-secret"
    hy2 = next(link for link in render_happ_links(inventory) if link.startswith("hysteria2://"))
    assert "obfs=salamander" in hy2
    assert "obfs-password=obfs-secret" in hy2
    assert "pcs=" + ("cd" * 32) in hy2
    assert "insecure=" not in hy2
    assert "pinSHA256=" not in hy2
    assert "allowInsecure" not in hy2


def test_happ_trojan_pcs_no_vcn(node, inventory):
    node.tls_cert_sha256 = "ab" * 32
    trojan = next(link for link in render_happ_links(inventory) if link.startswith("trojan://"))
    assert f"pcs={'ab' * 32}" in trojan
    assert "vcn=" not in trojan
    assert "allowInsecure" not in trojan


def test_happ_secure_node_no_pcs(node, inventory):
    node.insecure = False
    node.tls_domain = "exit.example.com"
    node.tls_cert_sha256 = "ab" * 32
    hy2 = next(link for link in render_happ_links(inventory) if link.startswith("hysteria2://"))
    trojan = next(link for link in render_happ_links(inventory) if link.startswith("trojan://"))
    assert "pcs=" not in hy2
    assert "insecure=" not in hy2
    assert "@exit.example.com:" in hy2
    assert "@exit.example.com:" in trojan


def test_happ_reality_v2rayn_params(node, inventory):
    reality = next(
        link
        for link in render_happ_links(inventory)
        if link.startswith("vless://") and "-443" not in link.split("#")[-1]
    )
    assert "security=reality" in reality
    assert "fp=firefox" in reality
    assert "flow=" not in reality
    assert "allowInsecure" not in reality


def test_ru_nodes_excluded_from_render(inventory, ru_node):
    inventory.upsert(ru_node)
    out = render_surge(inventory)
    assert ru_node.ip not in out
    links = render_happ_links(inventory)
    assert all(ru_node.ip not in link for link in links)


def test_singbox_and_xray_render_ai_clean_upstream(node):
    import json

    from outpost.config import Settings
    from outpost.server.singbox import render_singbox_config
    from outpost.server.xray import render_xray_config

    cfg = Settings(ai_proxy_url="socks5://ipuser:ippass@geo.iproyal.com:12324")
    sb = json.loads(render_singbox_config(node, settings=cfg))
    out_tags = {o["tag"]: o for o in sb["outbounds"]}
    assert "ai-clean" in out_tags
    assert out_tags["ai-clean"]["type"] == "socks"
    assert out_tags["ai-clean"]["server"] == "geo.iproyal.com"
    assert out_tags["ai-clean"]["server_port"] == 12324
    assert out_tags["ai-clean"]["username"] == "ipuser"
    assert out_tags["ai-clean"]["password"] == "ippass"
    assert sb["route"]["rules"][0]["outbound"] == "ai-clean"
    assert "anthropic.com" in sb["route"]["rules"][0]["domain_suffix"]

    xr = json.loads(render_xray_config(node, settings=cfg))
    xr_tags = {o["tag"]: o for o in xr["outbounds"]}
    assert "ai-clean" in xr_tags
    assert xr_tags["ai-clean"]["protocol"] == "socks"
    assert xr["routing"]["rules"][0]["outboundTag"] == "ai-clean"
    assert "domain:anthropic.com" in xr["routing"]["rules"][0]["domain"]
