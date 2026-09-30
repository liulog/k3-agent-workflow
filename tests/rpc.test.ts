import test from "node:test";
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { RpcWorker } from "../src/rpc-worker.ts";
import { Engine } from "../src/engine.ts";
import { terminal } from "../src/types.ts";
import { temp, until, request, cleanup } from "./helpers.ts";
const fixture = fileURLToPath(new URL("./fixtures/fake-pi.ts", import.meta.url));
function worker(mode: string) { return new RpcWorker({ command: process.execPath, args: [fixture, mode], timeoutMs: 1500 }); }
for (const mode of ["ok", "early-end"]) {
  test(`RPC ${mode}: JSONL subprocess + both stages + artifact validation`, async t => {
    const w = worker(mode), engine = new Engine(await temp(t), { build: w, test: w });
    cleanup(t, () => engine.close());
    const exp = engine.submit("rpc", request()); engine.start();
    await until(() => terminal(engine.store.get(exp.id)!.status));
    assert.equal(engine.store.get(exp.id)!.status, "succeeded", engine.store.get(exp.id)!.error);
  });
}
for (const mode of ["reject", "malformed", "error", "exit", "hang"]) {
  test(`RPC ${mode} is not considered success`, async t => {
    const w = worker(mode), engine = new Engine(await temp(t), { build: w, test: w });
    cleanup(t, () => engine.close());
    const exp = engine.submit("rpc", request()); engine.start();
    await until(() => terminal(engine.store.get(exp.id)!.status));
    assert.equal(engine.store.get(exp.id)!.status, "failed");
  });
}
test("RPC missing executable rejects instead of hanging", async t => {
  const root = await temp(t); await mkdir(join(root, "job"));
  const engine = new Engine(root, { build: worker("ok"), test: worker("ok") });
  cleanup(t, () => engine.close());
  const exp = engine.submit("rpc", request());
  await assert.rejects(new RpcWorker({ command: "/nonexistent/k3-pi", args: [] }).run({ experiment: exp, stage: "build", directory: join(root, "job"), signal: new AbortController().signal }), /ENOENT/);
});
test("RPC cancellation terminates the child", async t => {
  const w = worker("hang"), engine = new Engine(await temp(t), { build: w, test: w });
  cleanup(t, () => engine.close());
  const exp = engine.submit("rpc", request()); engine.tick();
  engine.cancel("rpc", exp.id);
  await until(() => !engine.active);
  assert.equal(engine.store.get(exp.id)!.status, "cancelled");
});
