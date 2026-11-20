import { readFileSync } from "node:fs";
import { createServer } from "node:http";

import worker from "./src/index.js";

const secretDirectory = process.env.OUTPOST_SECRET_DIR ?? "/run/outpost";
const token = readFileSync(`${secretDirectory}/token`, "utf8").trim();
const feed = readFileSync(`${secretDirectory}/happ`, "utf8").trim();
if (!/^[a-f0-9]{64}$/.test(token) || !feed) {
  throw new Error("A 64-character hex token and a nonempty Happ feed are required");
}

const env = {
  HAPP_COLOR_PROFILE: process.env.HAPP_COLOR_PROFILE,
  HAPP_PROVIDER_ID: process.env.HAPP_PROVIDER_ID,
  SUB_TOKEN: token,
  SUBS: { async get(kind) { return kind === "happ" ? feed : null; } },
};

const server = createServer(async (request, response) => {
  try {
    const url = new URL(`http://outpost.local${request.url}`);
    if (!/^\/[^/]+\/happ$/.test(url.pathname)) {
      response.writeHead(404).end();
      return;
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      response.writeHead(405, { allow: "GET, HEAD" }).end();
      return;
    }
    const result = await worker.fetch(new Request(url, { method: request.method }), env);
    response.writeHead(result.status, Object.fromEntries(result.headers));
    response.end(request.method === "HEAD" ? undefined : await result.text());
  } catch {
    response.writeHead(500).end();
  }
});

server.listen(Number(process.env.PORT ?? 8080), "0.0.0.0", () => {
  console.log(`Outpost Happ server listening on port ${server.address().port}`);
});

process.on("SIGTERM", () => server.close());
