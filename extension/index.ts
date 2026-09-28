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
    id: Type.Object({ id: Type.String({ description: "Experiment ID returned by workflow_submit" }) }),
    empty: Type.Object({}),
  });
}
