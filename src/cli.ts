import { resolve } from "node:path";
import { startServer } from "./server.ts";
import { piDemoWorker } from "./rpc-worker.ts";

const [command, ...args] = process.argv.slice(2);
if (command !== "serve" || args.some(arg => arg.startsWith("--") && !["--state", "--port", "--rpc-demo-model"].includes(arg))) {
  console.error("Usage: node src/cli.ts serve [--state .workflow] [--port 43127] [--rpc-demo-model provider/model]\nDefault: model-free simulation. RPC demo consumes model tokens, never compiles or accesses hardware.");
  process.exitCode = 1;
} else {
  const get = (key: string, fallback?: string) => { const i = args.indexOf(key); if (i < 0) return fallback; if (!args[i + 1] || args[i + 1].startsWith("--")) throw new Error(`Missing ${key} value`); return args[i + 1]; };
  const root = resolve(get("--state", ".workflow")!);
  const model = get("--rpc-demo-model");
  const port = Number(get("--port", "43127"));
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error("Invalid port");
  const app = await startServer({ root, port, workers: model ? { build: piDemoWorker(model), test: piDemoWorker(model) } : undefined });
  console.log(`k3-agent-workflow: ${app.url}\nMode: ${model ? "Pi RPC simulation" : "model-free simulation"}\nToken file: ${root}/token\nNo compilation, no board access. Ctrl+C to stop.`);
  const close = async () => { await app.close(); };
  process.once("SIGINT", close);
  process.once("SIGTERM", close);
}
