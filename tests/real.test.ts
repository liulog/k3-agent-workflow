// Contract fixtures only: these tests never invoke RealWorker, make, Pi, SSH or a board.
import test from "node:test";
import assert from "node:assert/strict";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { Engine, hash } from "../src/engine.ts";
import { completedAssistantText } from "../src/rpc-worker.ts";
import { DemoWorker } from "../src/demo-worker.ts";
import type { Worker } from "../src/types.ts";
import { terminal } from "../src/types.ts";
import { temp, cleanup, until } from "./helpers.ts";
const request = { profile: "linux-k3-real", key: "authorized" };
const checks = { status: true, exitCode: true, scores: true, anomalies: true, rawResults: true, imageIdentity: true, cleanup: true };
function fixture(bad?: string): Worker {
  return { async run(job) {
    // Deliberately exercise the real-result validator with synthetic contract fixtures.
    const image = Buffer.from("TEST FIXTURE, NOT A KERNEL");
    const report: any = { mode: "real", simulated: false, kind: job.stage, experimentId: job.experiment.id, sourceHash: job.experiment.sourceHash,
      buildExecuted: true, configUnchanged: true, sourceUnchanged: true, boardAccessed: job.stage === "test", artifactHash: hash(image), benchmark: "unixbench", checks: { ...checks } };
    if (job.stage === "build") writeFileSync(join(job.directory, "Image"), image);
    if (job.stage === "test" && bad) report.checks[bad] = false;
    writeFileSync(join(job.directory, "result.json"), JSON.stringify(report));
  } };
}
const demo = () => ({ build: new DemoWorker(1), test: new DemoWorker(1) });
test("settled main Agent errors and empty summaries cannot be reported as success", () => {
  const end = (message: any) => [{ type: "message_end", message: { role: "assistant", ...message } }, { type: "agent_settled" }];
  assert.throws(() => completedAssistantText(end({ stopReason: "error", errorMessage: "The usage limit has been reached" })), /usage limit/);
  assert.throws(() => completedAssistantText(end({ stopReason: "stop", content: [] })), /no final text/);
  assert.throws(() => completedAssistantText([{ type: "agent_settled" }]), /missing assistant/);
  assert.equal(completedAssistantText(end({ stopReason: "stop", content: [{ type: "text", text: "Verified failure, not success" }] })), "Verified failure, not success");
});
test("real execution is disabled unless explicitly injected", async t => {
  const engine = new Engine(await temp(t), demo()); cleanup(t, () => engine.close());
  assert.throws(() => engine.submit("real", request), /explicitly authorized/);
});
test("authorized one-shot retains idempotency and rejects a second real experiment", async t => {
  const worker = fixture(), engine = new Engine(await temp(t), demo(), undefined, { workers: { build: worker, test: worker } });
  cleanup(t, () => engine.close());
  const exp = engine.submit("real", request);
  assert.equal(engine.submit("real", request).id, exp.id);
  assert.throws(() => engine.submit("other", { ...request, key: "second" }), /only one real experiment/);
  engine.start(); await until(() => terminal(engine.store.get(exp.id)!.status));
  assert.equal(engine.store.get(exp.id)!.status, "succeeded");
  assert.equal(engine.store.get(exp.id)!.result?.simulated, false);
});
for (const criterion of Object.keys(checks)) test(`missing real ${criterion} evidence requires attention, not success`, async t => {
  const worker = fixture(criterion), engine = new Engine(await temp(t), demo(), undefined, { workers: { build: worker, test: worker } });
  cleanup(t, () => engine.close()); const exp = engine.submit("real", request); engine.start();
  await until(() => terminal(engine.store.get(exp.id)!.status));
  assert.equal(engine.store.get(exp.id)!.status, "needs_attention");
});
test("real hardware cancellation refuses a blind local kill", async t => {
  const gate = Promise.withResolvers<void>(), worker = fixture();
  const engine = new Engine(await temp(t), demo(), undefined, { workers: { build: worker, test: { async run(job) { await gate.promise; await worker.run(job); } } } });
  cleanup(t, () => engine.close()); cleanup(t, () => gate.resolve());
  const exp = engine.submit("real", request); engine.start();
  await until(() => engine.store.get(exp.id)!.stage === "test" && engine.store.get(exp.id)!.status === "running");
  assert.throws(() => engine.cancel("real", exp.id), /cannot be cancelled/);
  gate.resolve(); await until(() => terminal(engine.store.get(exp.id)!.status));
});
test("queued real execution is quarantined on restart even with workers configured", async t => {
  const root = await temp(t), worker = fixture(), execution = { workers: { build: worker, test: worker } };
  const before = new Engine(root, demo(), undefined, execution), exp = before.submit("real", request);
  await before.close();
  const after = new Engine(root, demo(), undefined, execution); cleanup(t, () => after.close());
  assert.equal(after.store.get(exp.id)!.status, "needs_attention");
});
test("fixed build argv and strict UnixBench evidence checks (pure Python, no subprocess tasks)", () => {
  const script = fileURLToPath(new URL("./real_contracts.py", import.meta.url));
  execFileSync("python3", [script], { env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" }, stdio: "pipe" });
});
