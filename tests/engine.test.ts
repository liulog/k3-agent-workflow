import test from "node:test";
import assert from "node:assert/strict";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { Engine } from "../src/engine.ts";
import { DemoWorker } from "../src/demo-worker.ts";
import { terminal } from "../src/types.ts";
import type { Worker, Job } from "../src/types.ts";
import { temp, until, request, cleanup } from "./helpers.ts";

async function setup(t: Parameters<typeof temp>[0], worker: Worker = new DemoWorker(5)) {
  const engine = new Engine(await temp(t), { build: worker, test: worker });
  cleanup(t, () => engine.close());
  return engine;
}
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
  assert.equal(engine.active.size, 0);
  assert.equal(engine.cancel("asm", exp.id).status, "cancelled");
  const next = engine.submit("asm", request("next"));
  engine.pause("asm", false); engine.start();
  await until(() => terminal(engine.store.get(next.id)!.status));
  assert.equal(engine.store.get(next.id)!.status, "succeeded");
});
test("running cancellation waits for the worker to stop before releasing slot", async t => {
  const engine = await setup(t, new DemoWorker(1000));
  const exp = engine.submit("asm", request()); engine.tick();
  assert.equal(engine.active.size, 1);
  engine.cancel("asm", exp.id);
  await until(() => engine.active.size === 0);
  assert.equal(engine.store.get(exp.id)!.status, "cancelled");
});
test("one worker per stage; test resource is exclusive across workflows", async t => {
  const counts = { build: 0, test: 0 }, maxima = { build: 0, test: 0 };
  const demo = new DemoWorker(30);
  const engine = await setup(t, { async run(job: Job) {
    maxima[job.stage] = Math.max(maxima[job.stage], ++counts[job.stage]);
    try { await demo.run(job); } finally { counts[job.stage]--; }
  } });
  const ids = ["a", "b", "c"].map(w => engine.submit(w, request()).id);
  engine.start();
  await until(() => ids.every(id => terminal(engine.store.get(id)!.status)));
  assert.deepEqual(maxima, { build: 1, test: 1 });
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
