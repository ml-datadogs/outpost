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


def test_ru_nodes_excluded_from_render(inventory, ru_node):
    inventory.upsert(ru_node)
    out = render_surge(inventory)
    assert ru_node.ip not in out
    links = render_happ_links(inventory)
    assert all(ru_node.ip not in link for link in links)
