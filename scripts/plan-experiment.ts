// One bounded build-plan → k3-auto-test-plan experiment. NEVER compiles or accesses hardware.
// Default: fake Pi subprocesses. Explicit --real-models is required to call model APIs.
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { loadPlanningConfig, inspectPlanningConfig } from "../src/planning-config.ts";
import { PlanWorker } from "../src/plan-worker.ts";
import { startServer } from "../src/server.ts";
import { Client } from "../src/client.ts";

const args = process.argv.slice(2), flags = new Map<string, string>();
let real = false;
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--real-models") { real = true; continue; }
  if (!["--config", "--state"].includes(args[i]) || flags.has(args[i]) || !args[i + 1] || args[i + 1].startsWith("--")) throw new Error("Usage: node scripts/plan-experiment.ts --config FILE [--state DIR] [--real-models]");
  flags.set(args[i], args[++i]);
}
const path = flags.get("--config");
if (!path) throw new Error("--config is required; default mode uses fake Pi, --real-models opts into model API calls");
const { config, options } = loadPlanningConfig(path);
inspectPlanningConfig(config);
if (!real) {
  const fixture = fileURLToPath(new URL("../tests/fixtures/fake-plan-pi.ts", import.meta.url));
  const fake = { command: process.execPath, prefixArgs: [fixture, "ok"], model: "fake/luna", timeoutMs: 10000 };
  options.workers = { build: new PlanWorker({ ...fake, stage: "build" }), test: new PlanWorker({ ...fake, stage: "test" }) };
}
const root = resolve(flags.get("--state") ?? `.workflow/plan-experiment-${Date.now()}`);
const app = await startServer({ root, port: 0, planning: options });
const client = new Client(app.url, app.token, "plan-experiment");
const abort = new AbortController();
const deadline = setTimeout(() => abort.abort(), (real ? config.timeoutMs! : 10000) * 2 + 30000);
let watch: Promise<void> | undefined;
try {
  const workflow = `linux-k3-${Date.now()}`;
  console.log(`Mode: ${real ? "REAL Luna model calls, PLAN ONLY" : "FAKE Pi protocol rehearsal"}\nState: ${root}\nNo compiler, k3ctl, SSH, serial, power or benchmark commands will execute.`);
  const exp = await client.request(`/workflows/${workflow}/experiments`, "POST", {
    profile: "linux-k3-plan", key: "linux-k3-read-only-v1", benchmark: "unixbench",
    task: "请为 linux-riscv-gate 编译 RISC-V 内核 Image 制定计划，再交接给使用 k3-auto skills 的测试 Agent 制定 UnixBench 计划。优先保留现有配置，不擅自 clean 或修改配置；说明构建入口、工具链、产物身份、板测前置条件及完成判据。仅验证合作链路，不执行任何命令、不编译、不连接或操作开发板。未知项列为 blockers。",
  });
  console.log(`Submitted ${exp.id}`);
  let terminal = false;
  watch = client.watch(workflow, 0, abort.signal, event => {
    console.log(`${event.id}: ${event.type}`);
    if (["experiment.succeeded", "experiment.failed", "experiment.cancelled", "experiment.needs_attention"].includes(event.type)) { terminal = true; abort.abort(); }
  }, state => { if (state !== "connected") console.error(state); });
  await watch;
  const result = await client.request(`/workflows/${workflow}/experiments/${exp.id}`);
  mkdirSync(root, { recursive: true });
  writeFileSync(join(root, "run-summary.json"), JSON.stringify({ realModels: real, simulated: !real, executionMode: "plan-only", result }, null, 2), { mode: 0o600 });
  if (!terminal || result.status !== "succeeded") throw new Error(`Planning experiment ${result.status}: ${result.error ?? "timed out"}; evidence: ${result.directory}`);
  console.log(JSON.stringify({ id: result.id, ...result.result }, null, 2));
  console.log(`PASS: Build plan → guarded k3-auto skill reads → Test plan. Compilation/board readiness is NOT verified. Full reports: ${root}/run-summary.json`);
} finally { clearTimeout(deadline); abort.abort(); await watch; await app.close(); }
