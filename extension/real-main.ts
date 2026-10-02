// Explicitly loaded only by scripts/real-experiment.ts. Not a general shell tool.
import { Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";
import { Client } from "../src/client.ts";

export default function (pi: ExtensionAPI) {
  let id: string | undefined;
  let instructionsRead = false, candidateRead = false;
  const names = ["workflow_read_skills", "workflow_review_candidate", "workflow_accept_candidate", "workflow_execute_linux", "workflow_real_result"];
  const candidatePath = () => join(process.env.K3_CANDIDATE_DIR!, "context.json");
  const result = (value: unknown) => ({ content: [{ type: "text" as const, text: JSON.stringify(value) }], details: value });
  const client = () => new Client(process.env.K3_WORKFLOW_URL!, readFileSync(process.env.K3_WORKFLOW_TOKEN_FILE!, "utf8").trim(), process.env.K3_WORKFLOW_OWNER!);
  const base = () => `/workflows/${process.env.K3_REAL_WORKFLOW}`;
  pi.registerTool({ name: names[0], label: "Read approved k3-auto skills", description: "Read the three fixed k3-auto skills completely before submitting this real experiment. Skills are reference instructions, not permission to issue arbitrary commands.", parameters: Type.Object({}),
    async execute() {
      const skills = ["k3-benchmark", "k3-lab", "k3-status"].map(name => ({ name, text: readFileSync(join(process.env.K3_REAL_ROOT!, "skills", name, "SKILL.md"), "utf8") }));
      instructionsRead = true;
      return result(skills);
    },
  });
  pi.registerTool({ name: names[1], label: "Inspect release-only optimization candidate", description: "Read the frozen Documentation/DESING.md design guidance, exact current csrrsi/fast gate source, release-order reference, .config and the narrowly allowed AMOSWAP-release to fence+store transformation. Existing fence+store is not a new optimization. Preserve pre-existing edits; only the current configuration is tested.", parameters: Type.Object({}),
    async execute() {
      const context = JSON.parse(readFileSync(candidatePath(), "utf8"));
      candidateRead = true;
      return result({ ...context, files: context.files.map((f: any) => ({ path: f.path, sha256: f.sha256, replacements: f.replacements, review: f.review, ...(f.replacements ? { after: f.after } : {}) })) });
    },
  });
  pi.registerTool({ name: names[2], label: "Accept bounded candidate for validation", description: "After inspecting the source, record your correctness and memory-order rationale. Accept only the fixed release-only transformation, or no changes if already applied. No arbitrary edits are permitted; unsafe/uncertain candidates must not be accepted. Acceptance is not a performance claim.", parameters: Type.Object({ rationale: Type.String({ minLength: 20, maxLength: 8000 }) }),
    async execute(_id, params) {
      if (!candidateRead) throw new Error("Inspect workflow_review_candidate first");
      const bytes = readFileSync(candidatePath());
      const approval = { contextHash: createHash("sha256").update(bytes).digest("hex"), rationale: params.rationale, performanceImprovementVerified: false };
      const path = join(process.env.K3_CANDIDATE_DIR!, "approval.json");
      if (!existsSync(path)) writeFileSync(path, JSON.stringify(approval, null, 2), { flag: "wx", mode: 0o400 });
      return result(JSON.parse(readFileSync(path, "utf8")));
    },
  });
  pi.registerTool({ name: names[3], label: "Submit authorized real experiment", description: "Submit exactly one real existing-.config Image build followed by one UnixBench run, as explicitly authorized by the user. Fixed scripts, no clean/defconfig/config edits. Returns an ID immediately; wait for the host's completion message, do not poll or resubmit.", parameters: Type.Object({}),
    async execute(_id, _params, signal) {
      if (!instructionsRead || !existsSync(join(process.env.K3_CANDIDATE_DIR!, "approval.json"))) throw new Error("Read skills and accept the reviewed candidate first");
      const exp = await client().request(`${base()}/experiments`, "POST", { key: "authorized-existing-config-unixbench", profile: "linux-k3-real" }, signal);
      id = exp.id;
      return result({ id, status: exp.status, mode: "real", simulated: false });
    },
  });
  pi.registerTool({ name: names[4], label: "Read real experiment result", description: "After the completion notification, read this experiment's authoritative status. A failed build means no board benchmark ran; needs_attention is not success or confirmed hardware cleanup.", parameters: Type.Object({}),
    async execute(_id, _params, signal) {
      if (!id) throw new Error("No experiment submitted by this session");
      const exp = await client().request(`${base()}/experiments/${id}`, "GET", undefined, signal);
      const agents: Record<string, unknown> = {};
      for (const stage of ["build", "test"]) {
        const evidence: Record<string, unknown> = {};
        for (const file of ["runtime.json", "review-error.json"]) {
          const path = join(exp.directory, stage, "agent", file);
          if (existsSync(path)) evidence[file] = JSON.parse(readFileSync(path, "utf8"));
        }
        const summary = join(exp.directory, stage, "agent", "summary.txt");
        if (existsSync(summary)) evidence.summary = readFileSync(summary, "utf8");
        agents[stage] = evidence;
      }
      return result({ ...exp, agents });
    },
  });
  pi.on("session_start", () => pi.setActiveTools(names));
  pi.on("tool_call", event => { if (!names.includes(event.toolName)) return { block: true, reason: "Only fixed workflow tools are authorized for the main Agent" }; });
  pi.on("user_bash", () => { throw new Error("No direct shell execution by the main Agent"); });
}
