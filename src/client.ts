import { setTimeout } from "node:timers/promises";
import type { WorkflowEvent } from "./types.ts";

export class Client {
  url: string; token: string; owner: string;
  constructor(url: string, token: string, owner: string) {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" || parsed.hostname !== "127.0.0.1" || parsed.username || parsed.password || parsed.pathname !== "/") throw new Error("Only http://127.0.0.1:port is supported");
    this.url = parsed.origin; this.token = token.trim(); this.owner = owner;
  }
  headers() { return { authorization: `Bearer ${this.token}`, "x-workflow-owner": this.owner }; }
  async request(path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<any> {
    const deadline = AbortSignal.timeout(10000);
    const response = await fetch(this.url + path, { method, headers: { ...this.headers(), "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body), signal: signal ? AbortSignal.any([signal, deadline]) : deadline });
    const value = await response.json() as any;
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${value.error}`);
    return value;
  }
  async watch(workflow: string, after: number, signal: AbortSignal, onEvent: (event: WorkflowEvent) => void | Promise<void>, onState: (state: string) => void = () => {}) {
    let cursor = after;
    while (!signal.aborted) {
      const attempt = new AbortController();
      let watchdog: ReturnType<typeof globalThis.setTimeout> | undefined;
      const touch = () => { clearTimeout(watchdog); watchdog = globalThis.setTimeout(() => attempt.abort(), 15000); };
      try {
        touch();
        const response = await fetch(`${this.url}/workflows/${encodeURIComponent(workflow)}/events?after=${cursor}`, {
          headers: this.headers(), signal: AbortSignal.any([signal, attempt.signal]),
        });
        if (!response.ok || !response.body) throw new Error(`Event stream HTTP ${response.status}`);
        onState("connected");
        let buffer = "";
        const decoder = new TextDecoder();
        for await (const chunk of response.body) {
          touch();
          buffer += decoder.decode(chunk, { stream: true });
          if (buffer.length > 1024 * 1024) throw new Error("SSE frame too large");
          let boundary: number;
          while ((boundary = buffer.indexOf("\n\n")) >= 0) {
            const frame = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const data = frame.split("\n").filter(line => line.startsWith("data: ")).map(line => line.slice(6)).join("\n");
            if (!data) continue;
            const event = JSON.parse(data) as WorkflowEvent;
            if (!Number.isSafeInteger(event.id) || event.workflow !== workflow) throw new Error("Invalid event");
            if (event.id <= cursor) continue;
            await onEvent(event);
            cursor = event.id;
          }
        }
      } catch (error) {
        if (!signal.aborted) onState(`reconnecting: ${String(error)}`);
      } finally { clearTimeout(watchdog); attempt.abort(); }
      if (!signal.aborted) await setTimeout(500, undefined, { signal }).catch(() => {});
    }
  }
}
