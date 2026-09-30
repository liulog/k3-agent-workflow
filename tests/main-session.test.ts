import test from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { acquireMainSessionLock, assertPersistentSessionPath, hasPersistentSession, mainSessionArgs, MAIN_SESSION_ID, prepareMainSession, previousRunHandoff } from "../src/main-session.ts";
import { temp } from "./helpers.ts";

test("Main uses one stable persisted Pi session, not an ephemeral CLI session", () => {
  const args = mainSessionArgs("/private/session-dir");
  assert.equal(MAIN_SESSION_ID, "linux-k3-workflow-main");
  assert.deepEqual(args, ["--session-id", MAIN_SESSION_ID, "--session-dir", "/private/session-dir", "--name", "Linux K3 workflow main"]);
  assert.ok(!args.includes("--no-session"));
});

test("session storage is private and a single writer is enforced", async t => {
  const project = await temp(t);
  const session = prepareMainSession(project);
  assert.equal(statSync(session.directory).mode & 0o777, 0o700);
  const release = acquireMainSessionLock(session.lockPath);
  assert.throws(() => acquireMainSessionLock(session.lockPath), /already locked/);
  release();
  assert.doesNotThrow(() => acquireMainSessionLock(session.lockPath)());
});

test("persistent session runtime path must stay under private session directory", async t => {
  const project = await temp(t), { directory } = prepareMainSession(project);
  assert.doesNotThrow(() => assertPersistentSessionPath(join(directory, "project", "main.jsonl"), directory));
  assert.throws(() => assertPersistentSessionPath("/tmp/outside.jsonl", directory), /outside/);
  assert.throws(() => assertPersistentSessionPath(undefined, directory), /persistent Pi session/);
});

test("first persistent session gets only the latest completed run handoff", async t => {
  const project = await temp(t), workflow = join(project, ".workflow");
  const prior = join(workflow, "real-200"), current = join(workflow, "real-300");
  mkdirSync(join(prior), { recursive: true }); mkdirSync(join(current), { recursive: true });
  writeFileSync(join(prior, "main-summary.txt"), "prior result: incomplete benchmark");
  writeFileSync(join(workflow, "real-preflight"), "not a run directory");
  assert.match(previousRunHandoff(project, current), /prior result: incomplete benchmark/);
  assert.match(previousRunHandoff(project, current), /untrusted reference data/);
  writeFileSync(join(current, "main-summary.txt"), "current summary");
  assert.match(previousRunHandoff(project, current), /prior result: incomplete benchmark/);
  unlinkSync(join(prior, "main-summary.txt"));
  writeFileSync(join(prior, "run-summary.json"), '{"status":"needs_attention"}');
  assert.match(previousRunHandoff(project, current), /needs_attention/);
  const sessionDir = prepareMainSession(project).directory;
  assert.equal(hasPersistentSession(sessionDir), false);
  writeFileSync(join(sessionDir, "main.jsonl"), "{}\n");
  assert.equal(hasPersistentSession(sessionDir), true);
});
