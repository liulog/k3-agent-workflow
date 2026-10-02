import test from "node:test";
import assert from "node:assert/strict";
import { startServer, SSE_EVENT_POLL_INTERVAL_MS } from "../src/server.ts";
import { Client } from "../src/client.ts";
import { temp, until, request, cleanup } from "./helpers.ts";

test("HTTP authentication, origin rejection, validation, singleton state lock", async t => {
  assert.equal(SSE_EVENT_POLL_INTERVAL_MS, 10_000);
  const root = await temp(t), app = await startServer({ root, port: 0 });
  cleanup(t, () => app.close());
  assert.equal((await fetch(app.url + "/health")).status, 401);
  assert.equal((await fetch(app.url + "/health", { headers: { authorization: `Bearer ${app.token}`, origin: "http://evil.test" } })).status, 403);
  await assert.rejects(startServer({ root, port: 0 }), /locked/);
  const c = new Client(app.url, app.token, "test");
  await assert.rejects(c.request("/workflows/test/experiments", "POST", {}), /400/);
  assert.equal((await c.request("/health")).mode, "simulation-only");
});
test("SSE replay, exclusive attachment, owner checks, reconnect cursor", async t => {
  const app = await startServer({ root: await temp(t), port: 0 });
  cleanup(t, () => app.close());
  const c = new Client(app.url, app.token, "owner-a"), other = new Client(app.url, app.token, "owner-b");
  const exp = await c.request("/workflows/test/experiments", "POST", request());
  const controller = new AbortController(), events: any[] = [];
  let connected = false;
  const watch = c.watch("test", 0, controller.signal, e => { events.push(e); }, state => { connected = state === "connected"; });
  cleanup(t, async () => { controller.abort(); await watch; });
  await until(() => connected);
  await assert.rejects(other.request("/workflows/test/pause", "POST"), /409/);
  await until(() => events.some(e => e.type === "experiment.succeeded"), 15_000);
  const cursor = events.at(-1).id;
  assert.equal((await c.request(`/workflows/test/experiments/${exp.id}`)).result.simulated, true);
  controller.abort(); await watch;
  await until(() => events.length === 5);
  // Allow server's disconnect callback to release the subscriber slot.
  await new Promise(resolve => setTimeout(resolve, 50));
  const next = new AbortController(), received: any[] = [];
  const replay = c.watch("test", cursor - 1, next.signal, e => { received.push(e); next.abort(); });
  const deadline = setTimeout(() => next.abort(), 2000);
  await replay; clearTimeout(deadline);
  assert.equal(received.length, 1);
  assert.equal(received[0].id, cursor);
});
test("workflow scoping rejects access to another workflow's experiment", async t => {
  const app = await startServer({ root: await temp(t), port: 0 });
  cleanup(t, () => app.close());
  const c = new Client(app.url, app.token, "owner");
  const exp = await c.request("/workflows/a/experiments", "POST", request());
  await assert.rejects(c.request(`/workflows/b/experiments/${exp.id}`), /404/);
});
