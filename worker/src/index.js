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
function colorProfileHeader(value) {
  if (typeof value !== "string") return;
  if (value.trim() === "resetcolors") return "resetcolors";

  try {
    const profile = JSON.parse(value);
    if (!profile || typeof profile !== "object" || Array.isArray(profile)) return;
    const json = JSON.stringify(profile);
    if (/^[\x20-\x7E]+$/.test(json)) return json;
  } catch {
    // A misconfigured theme should not prevent clients from downloading the feed.
  }
}

function routingHeader(value) {
  if (typeof value !== "string") return;
  if (value.trim() === "happ://routing/off") return "happ://routing/off";

  try {
    const profile = JSON.parse(value);
    if (typeof profile?.Name !== "string" || !profile.Name.trim()) return;
    for (const key of ["DirectSites", "DirectIp", "ProxySites", "ProxyIp", "BlockSites", "BlockIp"]) {
      const rules = profile[key];
      if (rules !== undefined && !(Array.isArray(rules) && rules.every(rule => typeof rule === "string"))) return;
    }
    const bytes = new TextEncoder().encode(JSON.stringify(profile));
    const binary = Array.from(bytes, byte => String.fromCharCode(byte)).join("");
    return "happ://routing/onadd/" + btoa(binary);
  } catch {
    // Keep the server list available if the routing configuration is invalid.
  }
}

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
    if (kind === "happ") {
      headers["profile-title"] = "Outpost";
      if (request.method === "GET" || request.method === "HEAD") {
        const routing = routingHeader(env.HAPP_ROUTING_PROFILE);
        if (routing) headers.routing = routing;
      }
      if (typeof env.HAPP_PROVIDER_ID === "string" && /^[A-Za-z0-9]{8}$/.test(env.HAPP_PROVIDER_ID)) {
        headers.providerid = env.HAPP_PROVIDER_ID;
        const profile = colorProfileHeader(env.HAPP_COLOR_PROFILE);
        if (profile) headers["color-profile"] = profile;
      }
    }
    return new Response(body, { headers });
  },
};
