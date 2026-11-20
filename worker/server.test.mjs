import { strict as assert } from "node:assert";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import worker from "./src/index.js";

const entrypoint = fileURLToPath(new URL("./server.mjs", import.meta.url));
const token = "a".repeat(64);
const feed = Buffer.from("socks://test@proxy.example:1080#Test").toString("base64");
const providerId = "Test1234";

async function start(t, body = feed) {
  const directory = await mkdtemp(join(tmpdir(), "outpost-server-"));
  t.after(() => rm(directory, { recursive: true }));
  await Promise.all([
    writeFile(join(directory, "token"), token),
    writeFile(join(directory, "happ"), body),
  ]);
  const child = spawn(process.execPath, [entrypoint], {
    env: { ...process.env, OUTPOST_SECRET_DIR: directory, PORT: "0", HAPP_PROVIDER_ID: providerId },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const exited = once(child, "close");
  t.after(async () => {
    child.kill();
    await exited;
  });
  const logs = [];
  child.stdout.on("data", (chunk) => logs.push(chunk.toString()));
  child.stderr.on("data", (chunk) => logs.push(chunk.toString()));
  return { child, exited, logs };
}

test("serves only the private Happ feed without logging its URL", async (t) => {
  const { child, exited, logs } = await start(t);
  const port = await Promise.race([
    once(child.stdout, "data").then(([line]) => Number(line.toString().trim().split(" ").at(-1))),
    exited.then(() => assert.fail(logs.join(""))),
  ]);
  const origin = `http://127.0.0.1:${port}`;
  const response = await fetch(`${origin}/${token}/happ`);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("profile-title"), "Outpost");
  assert.equal(response.headers.get("providerid"), providerId);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(await response.text(), feed);

  const head = await fetch(`${origin}/${token}/happ`, { method: "HEAD" });
  assert.equal(head.headers.get("profile-title"), "Outpost");
  assert.equal(head.headers.get("providerid"), providerId);
  assert.equal(await head.text(), "");
  for (const [path, status, method] of [
    ["/wrong/happ", 403, "GET"],
    [`/${token}/fallback`, 404, "GET"],
    [`/${token}/happ`, 405, "POST"],
  ]) {
    const rejected = await fetch(origin + path, { method });
    assert.equal(rejected.status, status);
    assert.equal(rejected.headers.get("profile-title"), null);
    assert.equal(rejected.headers.get("providerid"), null);
    assert.ok(!(await rejected.text()).includes(feed));
  }
  child.kill();
  await exited;
  assert.ok(!logs.join("").includes(token));
  assert.ok(!logs.join("").includes(feed));
});

test("omits the Provider ID for unconfigured Happ feeds and other clients", async () => {
  const env = { SUB_TOKEN: token, SUBS: { async get() { return feed; } } };
  for (const [route, id] of [["happ", undefined], ["happ", "invalid id"], ["surge", providerId]]) {
    const response = await worker.fetch(new Request(`https://test.local/${token}/${route}`), {
      ...env,
      HAPP_PROVIDER_ID: id,
    });
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("providerid"), null);
  }
});

test("does not start with an empty feed", async (t) => {
  const { exited, logs } = await start(t, "");
  assert.equal((await exited)[0], 1);
  assert.match(logs.join(""), /nonempty Happ feed/);
});
