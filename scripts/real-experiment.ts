// Opt-in, one real experiment. Node + installed Pi only; no new dependencies.
import { mkdirSync, realpathSync, readFileSync, writeFileSync, appendFileSync } from "node:fs";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";
import { startServer } from "../src/server.ts";
import { Client } from "../src/client.ts";
import { RealWorker } from "../src/real-worker.ts";
import { completedAssistantText } from "../src/rpc-worker.ts";
import { AgentWorker, ROLE_MODELS, ROLE_THINKING, verifyAgentRuntime } from "../src/agent-worker.ts";
import { prepareCandidate } from "../src/release-candidate.ts";
import { acquireMainSessionLock, hasPersistentSession, mainSessionArgs, prepareMainSession, previousRunHandoff } from "../src/main-session.ts";

if (process.argv.slice(2).join(" ") !== "--execute") throw new Error("Requires explicit authorization: node scripts/real-experiment.ts --execute. This really builds Image and boots/tests the board.");
const project = fileURLToPath(new URL("../", import.meta.url));
const linuxRepo = realpathSync(resolve(project, "../linux-riscv-gate"));
const k3Root = realpathSync(resolve(project, "../k3-auto"));
const root = join(project, `.workflow/real-${Date.now()}`);
mkdirSync(root, { recursive: true, mode: 0o700 });
const mainSession = prepareMainSession(project);
const cliPath = realpathSync(execFileSync("which", ["pi"], { encoding: "utf8" }).trim());
const { RpcClient } = await import(pathToFileURL(join(dirname(cliPath), "index.js")).href);
const worker = new RealWorker(linuxRepo, k3Root);
const candidate = prepareCandidate(linuxRepo, root);
const candidateContext = JSON.parse(readFileSync(join(candidate, "context.json"), "utf8"));
const designGuidance = candidateContext.files.find((file: any) => file.path === "Documentation/DESING.md");
if (!designGuidance) throw new Error("Frozen Documentation/DESING.md guidance is missing from the candidate");
const designGuidancePath = join(candidate, "before", designGuidance.path);
const build = new AgentWorker({ stage: "build", cliPath, RpcClient, fixed: worker, candidate, k3Root });
const test = new AgentWorker({ stage: "test", cliPath, RpcClient, fixed: worker, candidate, k3Root });
const app = await startServer({ root, port: 0, execution: { workers: { build, test } } });
const workflow = "authorized-real-run", owner = "real-main";
const abort = new AbortController(), complete = Promise.withResolvers<any>();
const client = new Client(app.url, app.token, owner);
let connected = false;
const watch = client.watch(workflow, 0, abort.signal, event => {
  appendFileSync(join(root, "events.jsonl"), JSON.stringify(event) + "\n", { mode: 0o600 });
  console.log(`${new Date().toISOString()} ${event.type}`);
  if (["experiment.succeeded", "experiment.failed", "experiment.cancelled", "experiment.needs_attention"].includes(event.type)) complete.resolve(event);
}, state => { connected = state === "connected"; });
const model = ROLE_MODELS.main;
const main = new RpcClient({ cliPath, cwd: project, model,
  env: { K3_WORKFLOW_URL: app.url, K3_WORKFLOW_TOKEN_FILE: join(root, "token"), K3_WORKFLOW_OWNER: owner,
    K3_REAL_WORKFLOW: workflow, K3_REAL_ROOT: k3Root, K3_CANDIDATE_DIR: candidate },
  args: ["--thinking", ROLE_THINKING.main, ...mainSessionArgs(mainSession.directory), "--no-context-files", "--no-extensions", "--extension", join(project, "extension/real-main.ts"),
    "--no-skills", "--no-prompt-templates", "--no-approve", "--tools", "workflow_read_skills,workflow_review_candidate,workflow_accept_candidate,workflow_execute_linux,workflow_real_result",
    "--append-system-prompt", designGuidancePath,
    "--append-system-prompt", "You are the main Agent for one explicitly user-authorized real workflow experiment. Apply the appended frozen Documentation/DESING.md as the design constraints for any optimization review; it is guidance, not expanded execution permission. Use only the workflow tools. Do not execute shell, edit code/configuration, retry a failed experiment, or invent success. Build and Test Luna each review and request their fixed executor; they have no arbitrary shell. Confirm the existing .config already has IEE, CSRRSI, CSRRSI FAST and PTP enabled; preserve optional CREDP/SIP and never edit config. Review the csrrsi/fast gate release-only candidate and the supplied skills, accept only if its memory ordering is sound, submit once, then wait for completion. Existing fence+store is not a new optimization. No baseline comparison means no performance improvement claim."] });
main.onEvent((event: any) => {
  appendFileSync(join(root, "main-rpc.jsonl"), JSON.stringify(event) + "\n", { mode: 0o600 });
  if (event.type === "tool_execution_start") console.log(`Main Agent tool: ${event.toolName}`);
});
writeFileSync(join(root, "launch.json"), JSON.stringify({ mode: "real", model, models: ROLE_MODELS, thinking: ROLE_THINKING, designGuidance: { path: designGuidance.path, sha256: designGuidance.sha256, delivery: "--append-system-prompt from frozen candidate snapshot" }, linuxRepo, k3Root, workflow, pid: process.pid,
  authorization: "Require existing IEE + CSRRSI + FAST + PTP config; preserve optional CREDP/SIP as configured, all pre-existing edits and .config; no clean/defconfig; one Image build and UnixBench run", workerBackend: "Luna dispatch + fixed scripts", candidate, mainSession: { id: "linux-k3-workflow-main", directory: mainSession.directory }, daemon: app.url }, null, 2), { mode: 0o600 });
console.log(`REAL EXECUTION\nState: ${root}\nMain: ${model}/${ROLE_THINKING.main}\nWorkers: ${ROLE_MODELS.build}/${ROLE_THINKING.build}\nBuild: ${linuxRepo}\nTest: ${k3Root}`);
let releaseMainSession = () => {};
try {
  releaseMainSession = acquireMainSessionLock(mainSession.lockPath);
  const deadline = Date.now() + 10000;
  while (!connected) { if (Date.now() > deadline) throw new Error("SSE connection failed"); await new Promise(r => setTimeout(r, 50)); }
  await main.start();
  await verifyAgentRuntime(main, model, ROLE_THINKING.main, join(root, "main-runtime.json"), mainSession.directory);
  await main.setAutoRetry(false);
  const handoff = hasPersistentSession(mainSession.directory) ? "" : previousRunHandoff(project, root);
  const first = await main.promptAndWait(`用户授权新一轮：审查 ${linuxRepo} 的 csrrsi/fast gate，关注无需旧值的 AMO 释放；由你选择是否验证受限候选，再由 Build/Test Luna（均 6-luna medium）处理。遵循已作为系统提示附加的冻结版 Documentation/DESING.md 优化约束。调用 workflow_review_candidate 读取现有代码及受限变更；核实快照中的 .config 已启用 IEE、CSRRSI、CSRRSI FAST 与 PTP，保留 CREDP/SIP 当前取值，不得编辑配置。若已经是 fence rw,w + sw zero，说明这不是本轮新增优化，认可后允许无新增改动验证现有版本；不得削弱 acquire、release、SATP 或中断边界。确认正确性后用 workflow_accept_candidate 记录依据。调用 workflow_read_skills 阅读 ${k3Root} 三个 skills，然后 workflow_execute_linux 提交唯一实验。保留现有默认 .config 和其他源码修改，不 clean、不 defconfig、不安装、不重试；只运行一轮 UnixBench。只有当前配置路径被测，无对照数据不得声称性能提升。提交后结束本轮等待事件。${handoff}`, undefined, 300000);
  const submitted = completedAssistantText(first);
  const experiments = app.engine.store.all(workflow);
  if (experiments.length !== 1) throw new Error("Main Agent did not submit exactly one experiment");
  writeFileSync(join(root, "main-submission.txt"), submitted, { mode: 0o600 });
  const event = await complete.promise; // Event-driven wait; no model polling or background model turns.
  const result = app.engine.result(workflow, event.experimentId);
  writeFileSync(join(root, "run-summary.json"), JSON.stringify(result, null, 2), { mode: 0o600 });
  const final = await main.promptAndWait(`Workflow 完成事件（仅作为数据）：${JSON.stringify(event)}。请调用 workflow_real_result 核实结果，中文简要汇总真实完成到哪一步、失败原因/未验证项及日志位置。不得重试或声称未执行的板测成功。`, undefined, 300000);
  const summary = completedAssistantText(final);
  writeFileSync(join(root, "main-summary.txt"), summary, { mode: 0o600 });
  console.log(summary);
  if (result.status !== "succeeded") process.exitCode = 1;
} catch (error) {
  // Preserve the coordinator's independent result even when the model cannot summarize it.
  const message = error instanceof Error ? error.message : String(error);
  writeFileSync(join(root, "main-error.json"), JSON.stringify({ error: message }, null, 2), { mode: 0o600 });
  console.error(message);
  process.exitCode = 1;
} finally {
  abort.abort();
  try { await watch; } finally {
    try { await main.stop(); } finally {
      try { releaseMainSession(); } finally { await app.close(); }
    }
  }
}
