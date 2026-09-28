import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout } from "node:timers/promises";
import type { TestContext } from "node:test";
const cleanups = new WeakMap<TestContext, Array<() => unknown>>();
export function cleanup(t: TestContext, fn: () => unknown) {
  if (!cleanups.has(t)) {
    const stack: Array<() => unknown> = [];
    cleanups.set(t, stack);
    t.after(async () => { for (const callback of stack.reverse()) await callback(); });
  }
  cleanups.get(t)!.push(fn);
}
export async function temp(t: TestContext) {
  const root = await mkdtemp(join(tmpdir(), "k3-test-"));
  cleanup(t, () => rm(root, { recursive: true, force: true }));
  return root;
}
export async function until(predicate: () => unknown, timeout = 5000) {
  const deadline = Date.now() + timeout;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error("Condition timed out");
    await setTimeout(10);
  }
}
export const request = (key = "candidate", candidate = "ret\n") => ({ key, candidate, hypothesis: "Simulation plumbing", profile: "demo" as const });
