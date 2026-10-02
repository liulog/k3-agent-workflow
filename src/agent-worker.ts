import { mkdirSync, readFileSync, writeFileSync, appendFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";
import { completedAssistantText } from "./rpc-worker.ts";
import { applyCandidate } from "./release-candidate.ts";
import { hash } from "./engine.ts";
import { assertPersistentSessionPath } from "./main-session.ts";
import type { Job, Stage, Worker } from "./types.ts";
export const ROLE_MODELS = { main: "openai-codex/gpt-6.1-sol", build: "openai-codex/gpt-6-luna", test: "openai-codex/gpt-6-luna" };
export const ROLE_THINKING = { main: "high", build: "medium", test: "medium" } as const;
export async function verifyAgentRuntime(client: any, expected: string, expectedThinking: string, path: string, persistentSessionDir?: string) {
  const state = await client.getState();
  const actual = { provider: state.model?.provider, model: state.model?.id, thinking: state.thinkingLevel,
    sessionFile: state.sessionFile, sessionId: state.sessionId };
  writeFileSync(path, JSON.stringify(actual, null, 2), { mode: 0o600 });
  if (`${actual.provider}/${actual.model}` !== expected || actual.thinking !== expectedThinking) throw new Error(`Agent configuration mismatch: ${JSON.stringify(actual)}`);
  if (persistentSessionDir) assertPersistentSessionPath(actual.sessionFile, persistentSessionDir);
}

// A Luna session requests the fixed executor, then reviews its actual result.
// During compilation/benchmark the model is idle; there is no LLM polling.
export class AgentWorker implements Worker {
  constructorOptions: { stage: Stage; cliPath: string; RpcClient: any; fixed: Worker; candidate: string; k3Root: string };
  constructor(options: AgentWorker["constructorOptions"]) { this.constructorOptions = options; }
  async run(job: Job) {
    const options = this.constructorOptions;
    if (job.stage !== options.stage) throw new Error("Agent stage mismatch");
    job.signal.throwIfAborted();
    const directory = join(job.directory, "agent");
    mkdirSync(directory, { mode: 0o700 });
    const context = JSON.parse(readFileSync(join(options.candidate, "context.json"), "utf8"));
    const approval = JSON.parse(readFileSync(join(options.candidate, "approval.json"), "utf8"));
    const skills = job.stage === "test" ? ["k3-benchmark", "k3-lab", "k3-status"].map(name => {
      const text = readFileSync(join(options.k3Root, "skills", name, "SKILL.md"), "utf8");
      const path = join(directory, "skills", name, "SKILL.md");
      mkdirSync(join(directory, "skills", name), { recursive: true, mode: 0o700 });
      writeFileSync(path, text, { mode: 0o400 });
      return { name, path, text, sha256: hash(text) };
    }) : [];
    const input = { stage: job.stage, experimentId: job.experiment.id, nonce: randomUUID(),
      candidate: approval, configSettings: context.settings,
      changes: context.files.filter((f: any) => f.replacements),
      constraints: context.constraints, configHash: context.files.find((f: any) => f.path === ".config").sha256,
      ...(job.stage === "build" ? { sources: context.files } : { skills,
        build: JSON.parse(readFileSync(join(job.experiment.directory, "build", "result.json"), "utf8")), artifact: job.experiment.artifact }),
      execution: job.stage === "build" ? "Apply only the reviewed release-only transformation (possibly no changes), then fixed make Image with existing .config; no clean/defconfig/install." : "One owned TFTP boot and UnixBench run. The fixed executor locks each of the K3 board's online CPU policies to its performance-governor default frequency before UnixBench, verifies the board sysfs readback, checks the limits stayed locked, then restores the original board policies. The jump host CPU is never changed. Strict status/exit/1+16-copy scores/raw evidence/config+Build ID checks; stop if K3 lock or restore verification fails. Stop on preflight conflict; no SSH retry loop or test retries." };
    const inputPath = join(directory, "input.json");
    writeFileSync(inputPath, JSON.stringify(input, null, 2), { mode: 0o400 });
    const model = ROLE_MODELS[job.stage];
    const thinking = ROLE_THINKING[job.stage];
    const args = ["--no-session", "--thinking", thinking, "--no-extensions", "--extension", fileURLToPath(new URL("../extension/real-stage.ts", import.meta.url)),
      "--no-skills", "--no-prompt-templates", "--no-approve", "--tools", "stage_instructions,stage_request_execution,stage_result",
      "--append-system-prompt", `You are ${job.stage === "build" ? "Build" : "Test"} Luna. Read stage_instructions fully, review the approved candidate and constraints, then request this one fixed stage using stage_request_execution if sound. No arbitrary shell, file changes, polling or retries. Request returns before execution; finish your turn and await its result. If blocked, explain and do NOT request execution.`];
    for (const skill of skills) args.push("--skill", skill.path);
    const client = new options.RpcClient({ cliPath: options.cliPath, cwd: directory, model, args, env: { K3_STAGE_INPUT: inputPath } });
    client.onEvent((event: any) => appendFileSync(join(directory, "rpc.jsonl"), JSON.stringify(event) + "\n", { mode: 0o600 }));
    const abort = () => { void client.abort().catch(() => {}); };
    try {
      await client.start();
      await verifyAgentRuntime(client, model, thinking, join(directory, "runtime.json"));
      await client.setAutoRetry(false);
      job.signal.addEventListener("abort", abort, { once: true });
      const events = await client.promptAndWait("请阅读 stage_instructions，核对本阶段的候选、内存序/配置约束及固定执行步骤。有问题就说明阻塞；若认可则调用 stage_request_execution，结束本轮并等待实际执行结果。不要把已存在的 fence+sw 当成本轮新优化，不要声称未测试的配置已验证。", undefined, 300000);
      writeFileSync(join(directory, "dispatch-review.txt"), completedAssistantText(events), { mode: 0o600 });
      const requested = JSON.parse(readFileSync(join(directory, "dispatch.json"), "utf8"));
      if (requested.nonce !== input.nonce || requested.experimentId !== input.experimentId || requested.stage !== job.stage) throw new Error("Missing or mismatched Agent dispatch");
      job.signal.throwIfAborted();
      job.signal.removeEventListener("abort", abort);
      if (job.stage === "build") applyCandidate(options.candidate);
      console.log(`${model}/${thinking}: ${job.stage} authorized; starting fixed executor`);
      let executionError: unknown;
      try { await options.fixed.run(job); } catch (error) { executionError = error; }
      let report: unknown;
      try { report = JSON.parse(readFileSync(join(job.directory, "result.json"), "utf8")); } catch {}
      writeFileSync(join(directory, "outcome.json"), JSON.stringify({ executionSucceeded: !executionError, error: executionError ? String(executionError) : undefined, report }, null, 2), { mode: 0o400 });
      // Model review is separate from physical execution. Quota failure cannot erase its evidence.
      try {
        job.signal.throwIfAborted();
        job.signal.addEventListener("abort", abort, { once: true });
        const reviewed = await client.promptAndWait("固定执行器已返回。调用 stage_result 读取实际结果，再简要总结本阶段真正完成了什么及错误/未验证项。不要重试，不要调用 stage_request_execution。", undefined, 300000);
        writeFileSync(join(directory, "summary.txt"), completedAssistantText(reviewed), { mode: 0o600 });
      } catch (error) {
        writeFileSync(join(directory, "review-error.json"), JSON.stringify({ error: String(error), physicalExecutionSucceeded: !executionError }, null, 2), { mode: 0o600 });
      }
      if (executionError) throw executionError;
    } finally {
      job.signal.removeEventListener("abort", abort);
      await client.stop();
    }
  }
}
