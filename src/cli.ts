import { resolve } from "node:path";
import { startServer } from "./server.ts";
import { piDemoWorker } from "./rpc-worker.ts";
import { loadPlanningConfig, inspectPlanningConfig } from "./planning-config.ts";

const usage = "Usage:\n  node src/cli.ts serve [--state .workflow] [--port 43127] [--rpc-demo-model provider/model] [--plan-config planning.local.json]\n  node src/cli.ts check-plan --plan-config planning.local.json\nDefault: model-free simulation. linux-k3-plan: read-only planning, no compilation or board access.";
const [command, ...args] = process.argv.slice(2);
try {
  if (!["serve", "check-plan"].includes(command)) throw new Error(usage);
  const flags = new Map<string, string>();
  const allowed = command === "serve" ? ["--state", "--port", "--rpc-demo-model", "--plan-config"] : ["--plan-config"];
  for (let i = 0; i < args.length; i += 2) {
    if (!allowed.includes(args[i]) || flags.has(args[i]) || !args[i + 1] || args[i + 1].startsWith("--")) throw new Error(usage);
    flags.set(args[i], args[i + 1]);
  }
  const planPath = flags.get("--plan-config");
  const planning = planPath ? loadPlanningConfig(planPath) : undefined;
  if (command === "check-plan") {
    if (!planning) throw new Error("check-plan requires --plan-config");
    console.log(JSON.stringify(inspectPlanningConfig(planning.config), null, 2));
  } else {
    // Verify required local documents/skills before accepting planning work; no model calls.
    if (planning) inspectPlanningConfig(planning.config);
    const root = resolve(flags.get("--state") ?? ".workflow");
    const model = flags.get("--rpc-demo-model");
    const port = Number(flags.get("--port") ?? "43127");
    if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error("Invalid port");
    const app = await startServer({ root, port, workers: model ? { build: piDemoWorker(model), test: piDemoWorker(model) } : undefined, planning: planning?.options });
    console.log(`k3-agent-workflow: ${app.url}\nDemo backend: ${model ? "Pi RPC simulation" : "model-free simulation"}\nPlanning profile: ${planning ? "linux-k3-plan (Pi RPC, guarded read only)" : "disabled"}\nToken file: ${root}/token\nNo compilation, no board access. Ctrl+C to stop.`);
    const close = async () => { await app.close(); };
    process.once("SIGINT", close); process.once("SIGTERM", close);
  }
} catch (error) { console.error(String(error)); process.exitCode = 1; }
