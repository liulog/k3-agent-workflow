// Offline model/executor fixtures: no Pi, compiler, SSH or hardware is started.
import test from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { AgentWorker, ROLE_MODELS, THINKING } from "../src/agent-worker.ts";
import { gateFiles, prepareCandidate, releaseCandidate, applyCandidate } from "../src/release-candidate.ts";
import { hash } from "../src/engine.ts";
import { temp } from "./helpers.ts";
function setup(root: string) {
  const linux = join(root, "linux"), out = join(root, "run");
  const files = [...gateFiles, "arch/riscv/include/asm/haoc/iee-asm.h", "arch/riscv/include/asm/haoc/iee-csrrsi.h", "arch/riscv/include/asm/barrier.h", "arch/riscv/kernel/haoc/iee/iee-init.c", "arch/riscv/kernel/haoc/iee/iee-mmu-csrrsi-fast.c", ".config"];
  for (const path of files) {
    mkdirSync(dirname(join(linux, path)), { recursive: true });
    writeFileSync(join(linux, path), gateFiles.includes(path) ? "\tamoswap.w.aq t2, t2, (t6)\n\tamoswap.w.rl zero, zero, (t5)\n" : path === ".config" ? "CONFIG_IEE=y\nCONFIG_IEE_GATE_CSRRSI=y\nCONFIG_PTP=y\nCONFIG_IEE_GATE_CSRRSI_FAST=y\nCONFIG_IEE_SIP=y\nCONFIG_CREDP=y\n" : "fixture\n");
  }
  mkdirSync(out);
  const candidate = prepareCandidate(linux, out);
  writeFileSync(join(candidate, "approval.json"), JSON.stringify({ contextHash: hash(readFileSync(join(candidate, "context.json"))), rationale: "Synthetic test fixture, not a real optimization review" }));
  const directory = join(out, "build"); mkdirSync(directory);
  const job: any = { stage: "build", directory, signal: new AbortController().signal,
    experiment: { id: "fixture", directory: out, sourceHash: "fixture", request: { profile: "linux-k3-real", key: "fixture" } } };
  return { linux, candidate, job };
}
test("release-only transform is bounded and idempotent; acquire AMO is preserved", () => {
  const text = "\tamoswap.w.aq t2, t2, (t6)\n\tamoswap.w.rl zero, zero, (t5) # release\n";
  const changed = releaseCandidate(text);
  assert.equal(changed.replacements, 1);
  assert.ok(changed.content.includes("amoswap.w.aq t2, t2, (t6)"));
  assert.ok(changed.content.includes("fence           rw, w\n\tsw              zero, 0(t5)"));
  assert.equal(releaseCandidate(changed.content).replacements, 0);
  assert.equal(releaseCandidate("amoswap.w.rl t0, zero, (t5)").replacements, 0);
});
test("candidate refuses changed source and preserves the original snapshot", async t => {
  const { linux, candidate } = setup(await temp(t));
  const path = join(linux, gateFiles[0]); writeFileSync(path, "concurrent user edit");
  assert.throws(() => applyCandidate(candidate), /Source changed/);
  assert.equal(readFileSync(path, "utf8"), "concurrent user edit");
  assert.match(readFileSync(join(candidate, "before", gateFiles[0]), "utf8"), /amoswap.w.rl/);
});
for (const mode of ["ok", "wrong-model", "wrong-thinking", "no-dispatch", "summary-error"]) {
  test(`Luna delegation ${mode}: runtime, dispatch and physical result stay distinct`, async t => {
    const { linux, candidate, job } = setup(await temp(t));
    let calls = 0, phase = 0, stopped = false;
    class FakeClient {
      options: any;
      constructor(options: any) { this.options = options; assert.ok(options.args.includes(THINKING)); }
      onEvent() {} async start() {} async setAutoRetry() {} async abort() {}
      async stop() { stopped = true; }
      async getState() { return { model: { provider: "openai-codex", id: mode === "wrong-model" ? "gpt-6-astra" : "gpt-6-luna" }, thinkingLevel: mode === "wrong-thinking" ? "high" : "medium" }; }
      async promptAndWait() {
        const input = JSON.parse(readFileSync(this.options.env.K3_STAGE_INPUT, "utf8"));
        if (++phase === 1 && mode !== "no-dispatch") writeFileSync(join(dirname(this.options.env.K3_STAGE_INPUT), "dispatch.json"), JSON.stringify({ nonce: input.nonce, stage: input.stage, experimentId: input.experimentId }));
        const message = phase === 2 && mode === "summary-error" ? { role: "assistant", stopReason: "error", errorMessage: "fixture quota error" } : { role: "assistant", stopReason: "stop", content: [{ type: "text", text: "Simulated Agent review; no model or hardware used" }] };
        return [{ type: "message_end", message }, { type: "agent_settled" }];
      }
    }
    const worker = new AgentWorker({ stage: "build", cliPath: "unused", RpcClient: FakeClient, candidate, k3Root: "unused", fixed: { async run() {
      calls++; writeFileSync(join(job.directory, "result.json"), JSON.stringify({ simulated: true, fixture: true }));
    } } });
    if (["wrong-model", "wrong-thinking", "no-dispatch"].includes(mode)) {
      await assert.rejects(worker.run(job)); assert.equal(calls, 0);
    } else {
      await worker.run(job); assert.equal(calls, 1);
      assert.ok(readFileSync(join(linux, gateFiles[0]), "utf8").includes("fence           rw, w"));
      if (mode === "summary-error") assert.equal(JSON.parse(readFileSync(join(job.directory, "agent/review-error.json"), "utf8")).physicalExecutionSucceeded, true);
    }
    assert.equal(stopped, true);
  });
}
test("requested role models and thinking are exact, not fuzzy aliases", () => {
  assert.deepEqual(ROLE_MODELS, { main: "openai-codex/gpt-6.1-sol", build: "openai-codex/gpt-6-luna", test: "openai-codex/gpt-6-luna" });
  assert.equal(THINKING, "medium");
});
