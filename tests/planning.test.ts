import test from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync, readFileSync, chmodSync, symlinkSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { PlanWorker } from "../src/plan-worker.ts";
import { Engine } from "../src/engine.ts";
import { DemoWorker } from "../src/demo-worker.ts";
import { SKILL_NAMES, digest } from "../src/planning.ts";
import { loadPolicy, readSnapshot, completeReads } from "../src/plan-guard.ts";
import { loadPlanningConfig, inspectPlanningConfig } from "../src/planning-config.ts";
import { startServer } from "../src/server.ts";
import { Client } from "../src/client.ts";
import { terminal, validateSubmit } from "../src/types.ts";
import { cleanup, temp, until } from "./helpers.ts";
const fixture = fileURLToPath(new URL("./fixtures/fake-plan-pi.ts", import.meta.url));
const request = { profile: "linux-k3-plan", key: "linux-001", task: "Plan compiling linux-riscv-gate and hand off to k3-auto; do not execute", benchmark: "unixbench" };
function inputs(root: string) {
  const linuxRepo = join(root, "linux-riscv-gate"), k3Root = join(root, "k3-auto");
  const put = (path: string, text: string) => { mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, text); };
  put(join(linuxRepo, "Makefile"), "VERSION = 6\n# fixture, NOT an actual build\n");
  put(join(linuxRepo, "scripts/build_kernel.sh"), "#!/bin/sh\nexit 99 # must never execute\n");
  put(join(k3Root, "README.md"), "Fixture k3-auto docs\n");
  put(join(k3Root, "AUTOLINK_K3_BENCHMARK_GUIDE.md"), "One RUN_ID per boot. status.txt=success\n");
  for (const name of SKILL_NAMES) put(join(k3Root, "skills", name, "SKILL.md"), `---\nname: ${name}\ndescription: Fixture planning reference\n---\n# ${name}\nOnly a proposed command, never executed.\n`);
  put(join(k3Root, "config/jump.toml"), "THIS PRIVATE FILE MUST NEVER BE SNAPSHOTTED");
  return { linuxRepo, k3Root };
}
function workers(mode = "ok") {
  const options = { model: "fake/luna", command: process.execPath, prefixArgs: [fixture, mode], timeoutMs: 5000 };
  return { build: new PlanWorker({ ...options, stage: "build" }), test: new PlanWorker({ ...options, stage: "test" }) };
}
async function setup(t: Parameters<typeof temp>[0], mode = "ok") {
  const root = await temp(t), source = inputs(root);
  const engine = new Engine(join(root, "state"), { build: new DemoWorker(1), test: new DemoWorker(1) }, { ...source, workers: workers(mode) });
  cleanup(t, () => engine.close());
  return { root, source, engine };
}
test("plan profile is explicit, validated and disabled by default", async t => {
  const engine = new Engine(await temp(t), { build: new DemoWorker(), test: new DemoWorker() }); cleanup(t, () => engine.close());
  assert.throws(() => engine.submit("asm", request), /not configured/);
  assert.throws(() => validateSubmit({ ...request, benchmark: "arbitrary-command" }), /benchmark/);
  assert.throws(() => validateSubmit({ ...request, task: "" }), /task/);
});
test("two guarded RPC workers read snapshots/skills and hand off a plan hash, not firmware", async t => {
  const { engine, source } = await setup(t);
  const exp = engine.submit("asm", request);
  assert.equal(engine.submit("asm", request).id, exp.id);
  assert.throws(() => engine.submit("asm", { ...request, task: "different" }), /different input/);
  // Source edits after acknowledgement must not change the planning inputs.
  writeFileSync(join(source.linuxRepo, "scripts/build_kernel.sh"), "changed after snapshot");
  assert.ok(!exp.planning!.files.some(f => f.origin.includes("jump.toml")));
  engine.start(); await until(() => terminal(engine.store.get(exp.id)!.status), 10000);
  const done = engine.result("asm", exp.id) as any;
  assert.equal(done.status, "succeeded", done.error);
  assert.equal(done.result.mode, "plan-only");
  assert.equal(done.result.simulated, true);
  assert.equal(done.result.buildExecuted, false); assert.equal(done.result.boardAccessed, false);
  assert.equal(done.result.samples, undefined);
  assert.equal(done.plans.test.buildPlanHash, done.artifact.sha256);
  assert.deepEqual(done.plans.test.skillsUsed, [...SKILL_NAMES]);
  for (const stage of ["build", "test"]) {
    const launch = JSON.parse(readFileSync(join(exp.directory, stage, "launch.json"), "utf8"));
    assert.deepEqual(launch.allowedTools, ["read"]);
    assert.deepEqual(launch.skillNames, stage === "test" ? [...SKILL_NAMES] : []);
    const evidence = JSON.parse(readFileSync(join(exp.directory, stage, "read-evidence.json"), "utf8"));
    assert.ok(evidence.successfulReads.length >= (stage === "test" ? 6 : 2));
  }
});
for (const mode of ["no-reads", "partial", "unguarded", "forbidden", "bad-safety", "wrong-hash", "missing-skills", "bad-json", "too-many-errors"]) {
  test(`planning fails closed for ${mode}`, async t => {
    const { engine } = await setup(t, mode);
    const exp = engine.submit("asm", request); engine.start();
    await until(() => terminal(engine.store.get(exp.id)!.status), 10000);
    const done = engine.store.get(exp.id)!;
    assert.equal(done.status, "failed");
    assert.equal(done.result, undefined);
    if (!["wrong-hash", "missing-skills"].includes(mode)) assert.ok(!engine.store.events("asm", 0).some(e => e.type === "test.started"));
  });
}
test("rejected typo can be corrected without weakening the read allowlist", async t => {
  const { engine } = await setup(t, "retry-read");
  const exp = engine.submit("asm", request); engine.start();
  await until(() => terminal(engine.store.get(exp.id)!.status), 10000);
  assert.equal(engine.store.get(exp.id)!.status, "succeeded");
  const evidence = JSON.parse(readFileSync(join(exp.directory, "test", "read-evidence.json"), "utf8"));
  assert.equal(evidence.deniedReads.length, 1);
  assert.ok(!evidence.successfulReads.some((path: string) => path.includes("misspelled")));
});
test("planning document mutation is caught before dispatch", async t => {
  const { engine } = await setup(t);
  const exp = engine.submit("asm", request), file = exp.planning!.files[0];
  chmodSync(file.path, 0o600); writeFileSync(file.path, "tampered");
  engine.start(); await until(() => terminal(engine.store.get(exp.id)!.status));
  assert.match(engine.store.get(exp.id)!.error!, /Planning input changed/);
});
test("guard restricts paths, rejects symlinks/tampering, and tracks full paginated reads", async t => {
  const root = await temp(t), path = join(root, "input.md");
  writeFileSync(path, "line\n".repeat(2100));
  const policy = { mode: "plan-only" as const, files: [{ path, sha256: digest(readFileSync(path)) }] };
  const first = readSnapshot(policy, root, { path });
  assert.deepEqual(completeReads([first.details], policy.files), []);
  const second = readSnapshot(policy, root, { path, offset: first.details.endLine + 1 });
  assert.deepEqual(completeReads([first.details, second.details], policy.files), [path]);
  assert.throws(() => readSnapshot(policy, root, { path: "/etc/passwd" }), /allowlist/);
  assert.throws(() => readSnapshot(policy, root, { path: "../input.md" }), /allowlist/);
  assert.throws(() => readSnapshot(policy, root, { path, offset: 0 }), /offset/);
  assert.throws(() => loadPolicy(undefined), /required/);
  const link = join(root, "link"); symlinkSync(path, link);
  assert.throws(() => readSnapshot({ mode: "plan-only", files: [{ path: link, sha256: policy.files[0].sha256 }] }, root, { path: link }), /unsafe/);
  writeFileSync(path, "changed"); assert.throws(() => readSnapshot(policy, root, { path }), /hash changed/);
});
test("static config check reads docs only and rejects execution overrides", async t => {
  const root = await temp(t); inputs(root);
  const path = join(root, "planning.json");
  const config = { mode: "plan-only", linuxRepo: "./linux-riscv-gate", k3AutoRoot: "./k3-auto", build: { model: "example/luna" }, test: { model: "example/luna" } };
  writeFileSync(path, JSON.stringify(config));
  const loaded = loadPlanningConfig(path), info = inspectPlanningConfig(loaded.config);
  assert.equal(info.modelsContacted, false); assert.equal(info.executionEnabled, false);
  assert.deepEqual(info.test.skills, [...SKILL_NAMES]);
  writeFileSync(path, JSON.stringify({ ...config, mode: "execute" })); assert.throws(() => loadPlanningConfig(path), /plan-only/);
  writeFileSync(path, JSON.stringify({ ...config, test: { model: "example/luna", tools: "bash" } })); assert.throws(() => loadPlanningConfig(path), /overrides/);
});
test("HTTP/SSE completes plan-only task and returns both plans", async t => {
  const root = await temp(t), source = inputs(root);
  const app = await startServer({ root: join(root, "state"), port: 0, planning: { ...source, workers: workers() } }); cleanup(t, () => app.close());
  const client = new Client(app.url, app.token, "planning-test");
  assert.ok((await client.request("/health")).profiles.includes("linux-k3-plan"));
  const exp = await client.request("/workflows/asm/experiments", "POST", request);
  const abort = new AbortController(), received: any[] = [];
  const watch = client.watch("asm", 0, abort.signal, event => { received.push(event); if (event.type === "experiment.succeeded") abort.abort(); });
  const deadline = setTimeout(() => abort.abort(), 10000);
  cleanup(t, async () => { clearTimeout(deadline); abort.abort(); await watch; });
  await watch;
  assert.equal(received.at(-1).type, "experiment.succeeded");
  assert.equal(received.at(-1).data.profile, "linux-k3-plan");
  const result = await client.request(`/workflows/asm/experiments/${exp.id}`);
  assert.equal(result.plans.test.buildPlanHash, result.artifact.sha256);
});
test("restart without configured planning workers quarantines queued plan instead of using demo", async t => {
  const root = await temp(t), source = inputs(root), demo = { build: new DemoWorker(), test: new DemoWorker() };
  const before = new Engine(join(root, "state"), demo, { ...source, workers: workers() });
  const exp = before.submit("asm", request); await before.close();
  const after = new Engine(join(root, "state"), demo); cleanup(t, () => after.close());
  after.tick(); assert.equal(after.store.get(exp.id)!.status, "needs_attention");
});
