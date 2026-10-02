import test from "node:test";
import assert from "node:assert/strict";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { Engine, SCHEDULER_POLL_INTERVAL_MS } from "../src/engine.ts";
import { DemoWorker } from "../src/demo-worker.ts";
import { terminal } from "../src/types.ts";
import type { Worker, Job } from "../src/types.ts";
import { temp, until, request, cleanup } from "./helpers.ts";

async function setup(t: Parameters<typeof temp>[0], worker: Worker = new DemoWorker(5)) {
  const engine = new Engine(await temp(t), { build: worker, test: worker });
  cleanup(t, () => engine.close());
  return engine;
}
test("scheduler uses a 10-second fallback and wakes promptly for new work", async t => {
  assert.equal(SCHEDULER_POLL_INTERVAL_MS, 10_000);
  const engine = await setup(t, new DemoWorker(1));
  engine.start();
  const exp = engine.submit("asm", request());
  assert.equal(exp.status, "queued");
  await until(() => terminal(engine.store.get(exp.id)!.status), 3000);
  assert.equal(engine.store.get(exp.id)!.status, "succeeded");
});
test("snapshot, dependent stages, validated result, atomic durable event history", async t => {
  const engine = await setup(t);
  const exp = engine.submit("asm", request());
  assert.equal(exp.status, "queued");
  engine.start();
  await until(() => terminal(engine.store.get(exp.id)!.status));
  const done = engine.store.get(exp.id)!;
  assert.equal(done.status, "succeeded");
  assert.equal(done.result?.simulated, true);
  assert.match(done.artifact!.sha256, /^[a-f0-9]{64}$/);
  assert.deepEqual(engine.store.events("asm", 0).map(e => e.type), ["experiment.queued", "build.started", "build.succeeded", "test.started", "experiment.succeeded"]);
});
test("idempotency returns same ID and rejects changed payload", async t => {
  const engine = await setup(t);
  const first = engine.submit("asm", request());
  assert.equal(engine.submit("asm", request()).id, first.id);
  assert.throws(() => engine.submit("asm", request("candidate", "nop")), /different input/);
});
test("budget is enforced and duplicates do not consume slots", async t => {
  const engine = await setup(t);
  for (let i = 0; i < 5; i++) engine.submit("asm", request(String(i)));
  assert.throws(() => engine.submit("asm", request("six")), /budget/);
  assert.ok(engine.submit("asm", request("0")));
});
test("invalid profiles and traversal-like workflow names rejected", async t => {
  const engine = await setup(t);
  assert.throws(() => engine.submit("../bad", request()), /Invalid workflow/);
  assert.throws(() => engine.submit("ok", { ...request(), profile: "hardware" }), /demo/);
});
test("build failure never dispatches a test", async t => {
  const stages: string[] = [];
  const engine = await setup(t, { async run(job) { stages.push(job.stage); throw new Error("Build unavailable"); } });
  const exp = engine.submit("asm", request()); engine.start();
  await until(() => terminal(engine.store.get(exp.id)!.status));
  assert.deepEqual(stages, ["build"]);
  assert.equal(engine.store.get(exp.id)!.status, "failed");
});
test("pause blocks dispatch; resume continues; queued cancellation is terminal", async t => {
  const engine = await setup(t);
  engine.pause("asm", true);
  const exp = engine.submit("asm", request());
  engine.tick();
  assert.equal(engine.active, undefined);
  assert.equal(engine.cancel("asm", exp.id).status, "cancelled");
  const next = engine.submit("asm", request("next"));
  engine.pause("asm", false); engine.start();
  await until(() => terminal(engine.store.get(next.id)!.status));
  assert.equal(engine.store.get(next.id)!.status, "succeeded");
});
test("running cancellation holds the only slot until worker cleanup completes", async t => {
  const gate = Promise.withResolvers<void>(), demo = new DemoWorker(1);
  const engine = await setup(t, { async run(job) {
    if (job.experiment.request.key === "slow") await gate.promise;
    await demo.run(job);
  } });
  cleanup(t, () => gate.resolve());
  const exp = engine.submit("asm", request("slow")); engine.tick();
  const next = engine.submit("other", request("next"));
  engine.cancel("asm", exp.id); engine.tick();
  assert.equal(engine.active?.id, exp.id);
  assert.equal(engine.store.get(next.id)!.status, "queued");
  gate.resolve();
  await until(() => !engine.active);
  assert.equal(engine.store.get(exp.id)!.status, "cancelled");
  engine.start();
  await until(() => terminal(engine.store.get(next.id)!.status));
  assert.equal(engine.store.get(next.id)!.status, "succeeded");
});
test("one global worker runs each experiment build then test, without stage overlap", async t => {
  let count = 0, maximum = 0;
  const order: string[] = [], demo = new DemoWorker(30);
  const engine = await setup(t, { async run(job: Job) {
    maximum = Math.max(maximum, ++count);
    order.push(`${job.experiment.workflow}:${job.stage}`);
    try { await demo.run(job); } finally { count--; }
  } });
  const ids = ["a", "b", "c"].map(w => engine.submit(w, request()).id);
  engine.start();
  await until(() => ids.every(id => terminal(engine.store.get(id)!.status)));
  assert.equal(maximum, 1);
  assert.deepEqual(order, ["a:build", "a:test", "b:build", "b:test", "c:build", "c:test"]);
  assert.ok(ids.every(id => engine.store.get(id)!.status === "succeeded"));
});
test("a paused test yields the slot; failed build does not block subsequent work", async t => {
  const order: string[] = [], demo = new DemoWorker(1);
  const engine = await setup(t, { async run(job) {
    order.push(`${job.experiment.workflow}:${job.stage}`);
    if (job.experiment.workflow === "bad") throw new Error("Expected build failure");
    await demo.run(job);
  } });
  const first = engine.submit("first", request());
  const bad = engine.submit("bad", request());
  const last = engine.submit("last", request());
  engine.tick();
  await until(() => !engine.active);
  assert.equal(engine.store.get(first.id)!.stage, "test");
  engine.pause("first", true); engine.start();
  await until(() => terminal(engine.store.get(last.id)!.status));
  assert.equal(engine.store.get(first.id)!.status, "queued");
  assert.equal(engine.store.get(bad.id)!.status, "failed");
  engine.pause("first", false);
  await until(() => terminal(engine.store.get(first.id)!.status));
  assert.deepEqual(order, ["first:build", "bad:build", "last:build", "last:test", "first:test"]);
});
test("bad result contracts and failed correctness do not become successes", async t => {
  const demo = new DemoWorker(1);
  const engine = await setup(t, { async run(job) {
    await demo.run(job);
    if (job.stage === "test") writeFileSync(join(job.directory, "result.json"), JSON.stringify({ simulated: false, kind: "test" }));
  } });
  const exp = engine.submit("asm", request()); engine.start();
  await until(() => terminal(engine.store.get(exp.id)!.status));
  assert.equal(engine.store.get(exp.id)!.status, "failed");
  assert.match(engine.store.get(exp.id)!.error!, /simulated/);
});
test("tampered source and artifact are rejected", async t => {
  const demo = new DemoWorker(1);
  const engine = await setup(t, { async run(job) {
    await demo.run(job);
    if (job.stage === "test") writeFileSync(job.experiment.artifact!.path, "tampered");
  } });
  const exp = engine.submit("asm", request()); engine.start();
  await until(() => terminal(engine.store.get(exp.id)!.status));
  assert.match(engine.store.get(exp.id)!.error!, /Artifact hash/);
});
test("recovery quarantines interrupted work, preserves queue and event cursor", async t => {
  const root = await temp(t);
  const workers = { build: new DemoWorker(1), test: new DemoWorker(1) };
  const before = new Engine(root, workers);
  const exp = before.submit("asm", request());
  exp.status = "running"; before.store.save(exp);
  const queued = before.submit("asm", request("next"));
  before.store.close(); // simulate crash without executing any process
  const after = new Engine(root, workers);
  cleanup(t, () => after.close());
  assert.equal(after.store.get(exp.id)!.status, "needs_attention");
  assert.equal(after.store.get(queued.id)!.status, "queued");
  assert.equal(after.store.events("asm", 2)[0].type, "experiment.needs_attention");
  after.start();
  await until(() => terminal(after.store.get(queued.id)!.status));
});
