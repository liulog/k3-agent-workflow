// Fake Pi for transport/contract testing. Does NOT invoke a model or execute proposed commands.
import { readFileSync } from "node:fs";
import { loadPolicy, readSnapshot } from "../../src/plan-guard.ts";
const mode = process.argv[2] ?? "ok";
const args = process.argv.slice(3);
const emit = (event: unknown) => process.stdout.write(JSON.stringify(event) + "\n");
let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", chunk => {
  buffer += chunk;
  const newline = buffer.indexOf("\n");
  if (newline < 0) return;
  const req = JSON.parse(buffer.slice(0, newline)); buffer = buffer.slice(newline + 1);
  emit({ type: "response", id: req.id, command: "prompt", success: true });
  try {
    if (args[args.indexOf("--tools") + 1] !== "read" || !args.includes("--no-extensions") || !args.includes("--extension")) throw new Error("Unsafe worker flags");
    const report = JSON.parse(req.message.split("\n").at(-1));
    if (args.filter(a => a === "--skill").length !== (report.kind === "test" ? 3 : 0)) throw new Error("Incorrect explicit skill loading");
    if (mode === "forbidden") {
      emit({ type: "tool_execution_start", toolCallId: "forbidden", toolName: "bash", args: { command: "DO NOT EXECUTE" } });
      return;
    }
    const policy = loadPolicy(process.env.K3_PLAN_POLICY);
    let call = 0;
    if (mode === "retry-read" || mode === "too-many-errors") {
      for (let n = 0; n < (mode === "retry-read" ? 1 : 4); n++) {
        const id = `typo-${n}`;
        emit({ type: "tool_execution_start", toolCallId: id, toolName: "read", args: { path: "../inputs/misspelled-file" } });
        emit({ type: "tool_execution_end", toolCallId: id, toolName: "read", result: { content: [{ type: "text", text: "PLAN-ONLY: denied" }] }, isError: true });
      }
    }
    if (mode !== "no-reads") for (const file of policy.files) {
      if (!req.message.includes(`REQUIRED_READ: ${file.path}`)) throw new Error("Missing required-read prompt");
      if (file.path.endsWith("build/result.json")) {
        const build = JSON.parse(readFileSync(file.path, "utf8"));
        if (build.kind !== "build" || build.mode !== "plan-only") throw new Error("No build plan handoff");
      }
      let offset = 1;
      do {
        const id = `read-${++call}`, input = { path: file.path, offset, limit: mode === "partial" ? 1 : 2000 };
        emit({ type: "tool_execution_start", toolCallId: id, toolName: "read", args: input });
        const result = readSnapshot(policy, process.cwd(), input);
        const next = result.details.endLine + 1, total = result.details.totalLines;
        if (mode === "unguarded") delete (result.details as any).guard;
        emit({ type: "tool_execution_end", toolCallId: id, toolName: "read", result, isError: false });
        offset = next;
        if (mode === "partial" || offset > total) break;
      } while (true);
    }
    report.simulated = true;
    report.summary = `FAKE RPC ${report.kind} plan — no model, compiler or board was used`;
    report.blockers = ["Real execution requires explicit authorization and toolchain/board readiness checks"];
    report.assumptions = ["Only selected documentation snapshots were inspected; this is a protocol test"];
    report.steps = [{ purpose: report.kind === "build" ? "Proposed kernel build, NOT executed" : "Proposed board benchmark, NOT executed",
      command: report.kind === "build" ? "make ARCH=riscv CROSS_COMPILE=<approved-prefix> Image" : "python3 scripts/k3ctl.py <benchmark> <Image> <RUN_ID> --campaign <campaign>", cwd: "<operator-approved repository path>" }];
    if (report.kind === "build") report.expectedImage = "arch/riscv/boot/Image (expected only, not produced)";
    else report.acceptanceChecks = ["One RUN_ID per boot", "status.txt=success and exit-code.txt=0", "Complete score sets, raw results and no unexpected anomalies", "Check actual power/serial state; do not infer safe idle from failed SSH"];
    if (mode === "bad-safety") report.commandsExecuted = true;
    if (mode === "wrong-hash" && report.kind === "test") report.buildPlanHash = "wrong";
    if (mode === "missing-skills" && report.kind === "test") report.skillsUsed = [];
    const output = mode === "bad-json" ? "not JSON" : JSON.stringify(report);
    emit({ type: "message_end", message: { role: "assistant", stopReason: "stop", content: [{ type: "text", text: output }] } });
    emit({ type: "agent_end" });
    emit({ type: "agent_settled" });
  } catch (error) {
    emit({ type: "message_end", message: { role: "assistant", stopReason: "error", errorMessage: String(error), content: [] } });
    emit({ type: "agent_settled" });
  }
});
