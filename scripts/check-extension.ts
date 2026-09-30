// Load the real extension entry with installed Pi peer exports, without a model,
// Pi session, package installation, compiler, or bundler. Node strips TS types.
import { registerHooks } from "node:module";
import { existsSync, realpathSync } from "node:fs";
import { delimiter, join } from "node:path";
import { pathToFileURL } from "node:url";
import assert from "node:assert/strict";

const cli = process.env.PI_CLI_PATH ?? (process.env.PATH ?? "").split(delimiter).map(p => join(p, "pi")).find(p => existsSync(p));
if (!cli) throw new Error("Installed Pi not found; set PI_CLI_PATH to its CLI file");
const piURL = pathToFileURL(realpathSync(cli)).href;
const hooks = registerHooks({ resolve(specifier, context, nextResolve) {
  if (["@earendil-works/pi-ai", "@earendil-works/pi-coding-agent"].includes(specifier)) {
    return nextResolve(specifier, { ...context, parentURL: piURL });
  }
  return nextResolve(specifier, context);
} });
try {
  const { default: extension } = await import("../extension/index.ts");
  const api = await import("@earendil-works/pi-coding-agent");
  const tools: any[] = [], commands: string[] = [], events: string[] = [];
  extension({ registerTool: (tool: any) => { api.defineTool(tool); tools.push(tool); },
    registerCommand: (name: string) => commands.push(name), on: (name: string) => events.push(name) } as any);
  assert.equal(tools.length, 5);
  assert.ok(tools.every(tool => tool.parameters.type === "object" && typeof tool.execute === "function"));
  assert.deepEqual(commands, ["workflow"]);
  assert.ok(events.includes("session_shutdown"));
  console.log("PASS: real extension entry loads against installed Pi exports; five schema-backed tools and /workflow registered. No session/model was started.");
  const { default: realMain } = await import("../extension/real-main.ts");
  const realTools: any[] = [];
  realMain({ registerTool: (tool: any) => { api.defineTool(tool); realTools.push(tool); }, on: () => {} } as any);
  assert.deepEqual(realTools.map(t => t.name), ["workflow_read_skills", "workflow_review_candidate", "workflow_accept_candidate", "workflow_execute_linux", "workflow_real_result"]);
  assert.equal(typeof api.RpcClient, "function");
  const { default: stageAgent } = await import("../extension/real-stage.ts");
  const stageTools: any[] = [];
  stageAgent({ registerTool: (tool: any) => { api.defineTool(tool); stageTools.push(tool); }, on: () => {} } as any);
  assert.deepEqual(stageTools.map(t => t.name), ["stage_instructions", "stage_request_execution", "stage_result"]);
  console.log("PASS: opt-in main/stage Agent extensions and installed RpcClient exports load; no task/model was started.");
} finally { hooks.deregister(); }
