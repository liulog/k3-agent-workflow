import { readFileSync, realpathSync, mkdtempSync, rmSync } from "node:fs";
import { dirname, resolve, join } from "node:path";
import { tmpdir } from "node:os";
import { PlanWorker } from "./plan-worker.ts";
import { snapshotPlanning, SKILL_NAMES } from "./planning.ts";
import type { PlanningOptions } from "./types.ts";

export type PlanningConfig = { mode: "plan-only"; linuxRepo: string; k3AutoRoot: string; build: { model: string }; test: { model: string }; timeoutMs?: number };
export function loadPlanningConfig(path: string): { config: PlanningConfig; options: PlanningOptions } {
  const raw = readFileSync(path, "utf8");
  if (Buffer.byteLength(raw) > 16000) throw new Error("Planning configuration too large");
  const v = JSON.parse(raw);
  if (!v || v.mode !== "plan-only" || typeof v.linuxRepo !== "string" || typeof v.k3AutoRoot !== "string"
    || typeof v.build?.model !== "string" || typeof v.test?.model !== "string"
    || Object.keys(v).some(k => !["mode", "linuxRepo", "k3AutoRoot", "build", "test", "timeoutMs"].includes(k))
    || [v.build, v.test].some(role => Object.keys(role).some(k => k !== "model"))) throw new Error("Expected a plan-only configuration with linuxRepo, k3AutoRoot and build/test model; no execution/tool overrides supported");
  const timeoutMs = v.timeoutMs ?? 300000;
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1000 || timeoutMs > 1800000) throw new Error("timeoutMs must be 1000..1800000");
  const base = dirname(resolve(path));
  const config: PlanningConfig = { ...v, timeoutMs, linuxRepo: realpathSync(resolve(base, v.linuxRepo)), k3AutoRoot: realpathSync(resolve(base, v.k3AutoRoot)) };
  return { config, options: { linuxRepo: config.linuxRepo, k3Root: config.k3AutoRoot, workers: {
    build: new PlanWorker({ stage: "build", model: config.build.model, timeoutMs }),
    test: new PlanWorker({ stage: "test", model: config.test.model, timeoutMs }),
  } } };
}
export function inspectPlanningConfig(config: PlanningConfig) {
  const temp = mkdtempSync(join(tmpdir(), "k3-plan-check-"));
  try {
    const snapshot = snapshotPlanning(temp, config.linuxRepo, config.k3AutoRoot);
    return { mode: "plan-only", staticCheck: "passed", modelsContacted: false, modelAvailability: "not checked",
      build: { model: config.build.model, source: config.linuxRepo, inputCount: snapshot.buildInputs.length },
      test: { model: config.test.model, source: config.k3AutoRoot, inputCount: snapshot.testInputs.length, skills: [...SKILL_NAMES] },
      tools: ["guarded read"], executionEnabled: false, credentialsRead: false,
      files: snapshot.files.map(f => ({ path: f.origin, sha256: f.sha256 })) };
  } finally { rmSync(temp, { recursive: true, force: true }); }
}
