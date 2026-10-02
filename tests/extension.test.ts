import test from "node:test";
import assert from "node:assert/strict";
import { registerWorkflow } from "../extension/workflow.ts";
import { startServer } from "../src/server.ts";
import { DemoWorker } from "../src/demo-worker.ts";
import { temp, until, cleanup } from "./helpers.ts";

test("extension registers tools, subscribes, delivers follow-up, cleans up and defaults auto off", async t => {
  const app = await startServer({ root: await temp(t), port: 0, workers: { build: new DemoWorker(150), test: new DemoWorker(150) } });
  cleanup(t, () => app.close());
  const oldURL = process.env.K3_WORKFLOW_URL, oldToken = process.env.K3_WORKFLOW_TOKEN;
  process.env.K3_WORKFLOW_URL = app.url; process.env.K3_WORKFLOW_TOKEN = app.token;
  cleanup(t, () => {
    if (oldURL === undefined) delete process.env.K3_WORKFLOW_URL; else process.env.K3_WORKFLOW_URL = oldURL;
    if (oldToken === undefined) delete process.env.K3_WORKFLOW_TOKEN; else process.env.K3_WORKFLOW_TOKEN = oldToken;
  });
  const tools = new Map<string, any>(), hooks = new Map<string, any>();
  const messages: any[] = [], statuses: string[] = [], notifications: string[] = [], entries: any[] = [];
  let command: any;
  const pi = {
    registerTool(tool: any) { tools.set(tool.name, tool); }, registerCommand(_name: string, value: any) { command = value; },
    on(name: string, callback: any) { hooks.set(name, callback); },
    appendEntry(customType: string, data: unknown) { entries.push({ type: "custom", customType, data: structuredClone(data) }); },
    sendMessage(message: any, options: any) { messages.push({ message, options }); },
  };
  const ctx = { hasUI: true, ui: { setStatus(_key: string, text: string) { statuses.push(text ?? ""); }, notify(text: string) { notifications.push(text); } }, sessionManager: { getBranch: () => entries } };
  registerWorkflow(pi as any, { submit: {}, plan: {}, id: {}, empty: {} });
  cleanup(t, () => hooks.get("session_shutdown")());
  await hooks.get("session_start")({}, ctx);
  assert.equal(tools.size, 5);
  await assert.rejects(tools.get("workflow_submit").execute("x", {}), /Not attached/);
  await command.handler("attach assembly", ctx);
  await until(() => statuses.some(s => s.includes("connected")));
  const submit = (key: string) => tools.get("workflow_submit").execute("call", { key, candidate: "ret", hypothesis: "test" });
  await submit("first");
  await until(() => messages.length === 1, 15_000);
  assert.equal(messages[0].options.triggerTurn, false);
  assert.equal(messages[0].options.deliverAs, "followUp");
  const eventId = messages[0].message.details.eventId;
  assert.ok(entries.at(-1).data.cursor < eventId, "Queued messages must not advance durable delivery cursor");
  await hooks.get("message_end")({ message: { role: "custom", ...messages[0].message } });
  assert.equal(entries.at(-1).data.cursor, eventId);
  await command.handler("auto on", ctx);
  await submit("second");
  await until(() => messages.length === 2, 15_000);
  assert.equal(messages[1].options.triggerTurn, true);
  assert.equal(messages[1].options.deliverAs, "followUp");
  assert.ok(entries.some(e => e.data?.cursor > 0));
  await command.handler("detach", ctx);
  await assert.rejects(tools.get("workflow_status").execute("x", {}), /Not attached/);
  assert.equal(entries.at(-1).data, null);
});
