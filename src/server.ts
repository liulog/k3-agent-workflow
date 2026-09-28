import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import { timingSafeEqual, randomBytes } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync, unlinkSync } from "node:fs";
import { join, resolve } from "node:path";
import { Engine } from "./engine.ts";
import { DemoWorker } from "./demo-worker.ts";
import { ApiError, workflowName } from "./types.ts";
import type { Stage, Worker } from "./types.ts";

async function body(req: IncomingMessage): Promise<unknown> {
  req.setEncoding("utf8");
  let data = "";
  for await (const chunk of req) {
    data += chunk.toString();
    if (Buffer.byteLength(data) > 100000) throw new ApiError(413, "Request too large");
  }
  try { return JSON.parse(data); } catch { throw new ApiError(400, "Invalid JSON"); }
}
function json(res: ServerResponse, status: number, value: unknown) {
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify(value));
}
function lock(root: string): () => void {
  const path = join(root, "daemon.lock");
  // Conservative: a crash leaves a lock requiring operator inspection/removal.
  try { writeFileSync(path, JSON.stringify({ pid: process.pid, startedAt: new Date().toISOString() }), { flag: "wx", mode: 0o600 }); }
  catch { throw new Error(`State directory is locked: ${path}. Check the recorded process before removing a stale lock.`); }
  return () => unlinkSync(path);
}
export async function startServer(options: { root: string; port?: number; token?: string; workers?: Record<Stage, Worker> }) {
  const root = resolve(options.root);
  mkdirSync(root, { recursive: true, mode: 0o700 });
  const unlock = lock(root);
  let engine: Engine;
  try { engine = new Engine(root, options.workers ?? { build: new DemoWorker(), test: new DemoWorker() }); }
  catch (error) { unlock(); throw error; }
  let token = options.token;
  if (!token) {
    const tokenPath = join(root, "token");
    try { token = readFileSync(tokenPath, "utf8").trim(); }
    catch (error: any) {
      if (error.code !== "ENOENT") { engine.store.close(); unlock(); throw error; }
      token = randomBytes(32).toString("hex");
      writeFileSync(tokenPath, token, { mode: 0o600, flag: "wx" });
    }
  }
  if (token.length < 16) { engine.store.close(); unlock(); throw new Error("Token must contain at least 16 characters"); }
  const secret = Buffer.from(`Bearer ${token}`);
  const streams = new Map<string, { owner: string; response: ServerResponse; stop: () => void }>();
  const server = createServer(async (req, res) => {
    try {
      // No browser origins / no CORS; local token auth is required for every route.
      if (req.headers.origin) throw new ApiError(403, "Browser origins are not allowed");
      const auth = Buffer.from(req.headers.authorization ?? "");
      if (auth.length !== secret.length || !timingSafeEqual(auth, secret)) throw new ApiError(401, "Unauthorized");
      const url = new URL(req.url ?? "/", "http://127.0.0.1");
      if (url.pathname === "/health" && req.method === "GET") return json(res, 200, { ok: true, mode: "simulation-only" });
      const parts = url.pathname.split("/").filter(Boolean);
      if (parts[0] !== "workflows" || parts.length < 2 || parts.length > 4) throw new ApiError(404, "Unknown route");
      const workflow = workflowName(parts[1]);
      const action = parts[2], id = parts[3];
      const owner = req.headers["x-workflow-owner"];
      if (req.method !== "GET" && streams.has(workflow) && streams.get(workflow)!.owner !== owner) throw new ApiError(409, "Workflow is controlled by another attached client");
      if (!action && req.method === "GET") {
        return json(res, 200, { ...engine.store.info(workflow), experiments: engine.store.all(workflow) });
      }
      if (action === "events" && req.method === "GET") {
        if (typeof owner !== "string" || !/^[\w.-]{1,150}$/.test(owner)) throw new ApiError(400, "x-workflow-owner required");
        if (streams.has(workflow)) throw new ApiError(409, "Workflow already has an event subscriber");
        const after = Number(url.searchParams.get("after") ?? req.headers["last-event-id"] ?? 0);
        if (!Number.isSafeInteger(after) || after < 0) throw new ApiError(400, "Invalid event cursor");
        let cursor = after;
        res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "Connection": "keep-alive" });
        res.write(": connected\n\n");
        const pump = () => {
          for (const event of engine.store.events(workflow, cursor)) {
            cursor = event.id;
            if (!res.write(`id: ${event.id}\ndata: ${JSON.stringify(event)}\n\n`)) break;
          }
        };
        const timer = setInterval(() => {
          if (res.writableLength > 1024 * 1024) return res.destroy();
          if (!res.writableNeedDrain) pump();
        }, 50);
        const heartbeat = setInterval(() => {
          if (!res.writableNeedDrain) res.write(": heartbeat\n\n");
        }, 5000);
        const stop = () => { clearInterval(timer); clearInterval(heartbeat); streams.delete(workflow); };
        streams.set(workflow, { owner, response: res, stop });
        res.on("close", stop);
        pump();
        return;
      }
      if (action === "experiments" && req.method === "POST" && !id) return json(res, 202, engine.submit(workflow, await body(req)));
      if (action === "experiments" && req.method === "GET" && id) return json(res, 200, engine.get(workflow, id));
      if (action === "cancel" && req.method === "POST" && id) return json(res, 202, engine.cancel(workflow, id));
      if ((action === "pause" || action === "resume") && req.method === "POST" && !id) {
        engine.pause(workflow, action === "pause");
        return json(res, 200, engine.store.info(workflow));
      }
      throw new ApiError(404, "Unknown route");
    } catch (error) {
      if (res.headersSent) res.destroy();
      else json(res, error instanceof ApiError ? error.status : 500, { error: error instanceof ApiError ? error.message : "Internal server error" });
      if (!(error instanceof ApiError)) console.error(error);
    }
  });
  server.requestTimeout = 10000;
  try {
    await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(options.port ?? 43127, "127.0.0.1", resolve); });
  } catch (error) { engine.store.close(); unlock(); throw error; }
  engine.start();
  const address = server.address() as { port: number };
  let closing = false;
  return {
    engine, token, url: `http://127.0.0.1:${address.port}`,
    async close() {
      if (closing) return;
      closing = true;
      for (const stream of [...streams.values()]) { stream.stop(); stream.response.end(); }
      const closed = new Promise<void>(resolve => server.close(() => resolve()));
      server.closeAllConnections();
      await closed;
      await engine.close();
      unlock();
    },
  };
}
