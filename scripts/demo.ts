import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { startServer } from "../src/server.ts";
import { Client } from "../src/client.ts";

const root = await mkdtemp(join(tmpdir(), "k3-workflow-demo-"));
const app = await startServer({ root, port: 0 });
const client = new Client(app.url, app.token, "demo-cli");
const abort = new AbortController();
const timeout = setTimeout(() => abort.abort(), 10000);
let watch: Promise<void> | undefined;
try {
  const request = { key: "first-candidate", candidate: ".text\n# demo only\nret\n", hypothesis: "Verify asynchronous plumbing, not performance", profile: "demo" };
  const submitted = await client.request("/workflows/demo/experiments", "POST", request);
  assert.equal(submitted.status, "queued");
  console.log(`Submitted ${submitted.id}; response returned before execution.`);
  let completed = false;
  watch = client.watch("demo", 0, abort.signal, event => {
    console.log(`${event.id}: ${event.type}`);
    if (event.type === "experiment.succeeded") { completed = true; abort.abort(); }
  });
  await watch;
  assert.ok(completed, "Expected a completion event before timeout");
  const result = await client.request(`/workflows/demo/experiments/${submitted.id}`);
  assert.equal(result.status, "succeeded");
  assert.equal(result.result.simulated, true);
  const duplicate = await client.request("/workflows/demo/experiments", "POST", request);
  assert.equal(duplicate.id, submitted.id);
  console.log("PASS: submit → simulated build → simulated test → SSE completion; duplicate submission reused the same experiment.");
  console.log("No compiler, model API or development board was used. Synthetic numbers are NOT benchmark measurements.");
} finally {
  clearTimeout(timeout); abort.abort(); await watch;
  await app.close(); await rm(root, { recursive: true, force: true });
}
