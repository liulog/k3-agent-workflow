import { Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { registerWorkflow } from "./workflow.ts";

export default function (pi: ExtensionAPI) {
  registerWorkflow(pi, {
    submit: Type.Object({
      key: Type.String({ description: "Stable idempotency key; reuse only for an identical candidate", minLength: 1, maxLength: 100 }),
      candidate: Type.String({ description: "Exact candidate assembly text to snapshot (demo only)", minLength: 1, maxLength: 65536 }),
      hypothesis: Type.String({ maxLength: 2000 }),
    }),
    plan: Type.Object({
      key: Type.String({ description: "Stable idempotency key for this plan-only task", minLength: 1, maxLength: 100 }),
      task: Type.String({ description: "Describe a proposed linux-riscv-gate build and k3-auto benchmark handoff. This tool only plans; never executes.", minLength: 1, maxLength: 8000 }),
      benchmark: Type.Union([Type.Literal("unixbench"), Type.Literal("lmbench")]),
    }),
    id: Type.Object({ id: Type.String({ description: "Experiment ID returned by workflow_submit" }) }),
    empty: Type.Object({}),
  });
}
