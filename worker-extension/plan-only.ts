import { Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { loadPolicy, readSnapshot } from "../src/plan-guard.ts";

// Explicit worker-only extension. Never loaded into the interactive main Agent.
export default function (pi: ExtensionAPI) {
  const policy = loadPolicy(process.env.K3_PLAN_POLICY);
  pi.registerTool({
    name: "read", label: "read (plan-only snapshot)",
    description: "Read only approved immutable planning inputs. No original source tree, secrets, shell, writes, network or board operations. Follow continuation offsets until complete.",
    parameters: Type.Object({ path: Type.String(), offset: Type.Optional(Type.Integer({ minimum: 1 })), limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 2000 })) }),
    async execute(_id, params, signal, _update, ctx) {
      signal?.throwIfAborted();
      return readSnapshot(policy, ctx.cwd, params);
    },
  });
  pi.on("session_start", () => { pi.setActiveTools(["read"]); });
  pi.on("tool_call", event => {
    if (event.toolName !== "read") return { block: true, reason: "PLAN-ONLY: only snapshot read is permitted" };
  });
  pi.on("user_bash", () => { throw new Error("PLAN-ONLY: shell execution is forbidden"); });
}
