// Actual JSONL child process for transport tests. No compiler, network or model.
import { writeFile } from "node:fs/promises";
const mode = process.argv[2] ?? "ok";
let buffer = "";
const emit = (event: unknown) => process.stdout.write(JSON.stringify(event) + "\n");
process.stdin.setEncoding("utf8");
process.stdin.on("data", async chunk => {
  buffer += chunk;
  const index = buffer.indexOf("\n");
  if (index < 0) return;
  const command = JSON.parse(buffer.slice(0, index)); buffer = "";
  if (mode === "hang") return;
  if (mode === "malformed") { process.stdout.write("not json\n"); return; }
  if (mode === "reject") { emit({ type: "response", id: command.id, success: false, error: "rejected" }); return; }
  emit({ type: "response", id: command.id, success: true });
  if (mode === "exit") { process.exit(0); }
  if (mode === "ok" || mode === "early-end") {
    if (mode === "early-end") emit({ type: "agent_end", willRetry: true });
    const contract = JSON.parse(command.message.split("exactly this JSON contract: ")[1].split("\n")[0]);
    if (contract.kind === "build") await writeFile("artifact.txt", "SIMULATED ARTIFACT\nUnicode separator: \u2028\n");
    await writeFile("result.json", JSON.stringify(contract));
  }
  emit({ type: "message_end", message: { role: "assistant", stopReason: mode === "error" ? "error" : "stop", content: [{ type: "text", text: "done\u2028still same JSON line" }] } });
  emit({ type: "agent_end" });
  setTimeout(() => emit({ type: "agent_settled" }), mode === "early-end" ? 100 : 10);
});
