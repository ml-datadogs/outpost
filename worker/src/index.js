/**
 * Outpost subscription Worker.
 *
 * Serves token-gated subscriptions that Surge and Happ poll:
 *   GET /<SUB_TOKEN>/surge          -> Surge-format proxy list (Hysteria2 + Trojan)
 *   GET /<SUB_TOKEN>/happ           -> base64 share-link bundle (Hysteria2 + Trojan + Reality)
 *   GET /<SUB_TOKEN>/fallback       -> tier-3 public configs mirror (untrusted exits!)
 *   GET /<SUB_TOKEN>/fallback-white -> same, whitelist-regime variant
 *
 * The rendered bodies live in a KV namespace (binding: SUBS, keys matching the
 * path kinds), written by CI: `outpost render` for surge/happ, `outpost fallback`
 * for the fallback bundles (see docs/fallbacks.md). The path token is matched
 * against the SUB_TOKEN secret because surge/happ contain live proxy credentials.
 */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);

    if (parts.length !== 2) {
      return new Response("not found\n", { status: 404 });
    }
    const [token, kind] = parts;

    if (!env.SUB_TOKEN || token !== env.SUB_TOKEN) {
      return new Response("forbidden\n", { status: 403 });
    }
    if (!["surge", "happ", "fallback", "fallback-white"].includes(kind)) {
      return new Response("not found\n", { status: 404 });
    }

    const body = await env.SUBS.get(kind);
    if (body === null) {
      return new Response("no subscription published yet\n", { status: 404 });
    }

    const headers = {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "no-store",
    };
    if (kind === "surge") {
      headers["content-disposition"] = 'inline; filename="outpost.conf"';
    }
    return new Response(body, { headers });
  },
};
