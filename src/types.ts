export type Stage = "build" | "test";
export type Status = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "needs_attention";
export type DemoSubmit = { key: string; candidate: string; hypothesis: string; profile: "demo" };
export type PlanSubmit = { key: string; task: string; benchmark: "unixbench" | "lmbench"; profile: "linux-k3-plan" };
export type RealSubmit = { key: string; profile: "linux-k3-real" };
export type Submit = DemoSubmit | PlanSubmit | RealSubmit;
export type RealResult = { mode: "real"; simulated: false; buildExecuted: true; boardAccessed: true; benchmark: "unixbench"; artifactHash: string; evidence: string; checks: Record<string, boolean> };
export type SnapshotFile = { path: string; sha256: string; origin: string };
export type PlanningContext = {
  linuxRepo: string; k3Root: string; files: SnapshotFile[];
  buildInputs: string[]; testInputs: string[]; skills: Array<{ name: string; path: string }>;
};
export type PlanReport = {
  mode: "plan-only"; kind: Stage; experimentId: string; sourceHash: string;
  commandsExecuted: false; boardAccessed: false; summary: string; simulated?: true;
  steps: Array<{ purpose: string; command: string; cwd: string }>;
  blockers: string[]; assumptions: string[];
  expectedImage?: string; buildPlanHash?: string; benchmark?: "unixbench" | "lmbench";
  skillsUsed?: string[]; acceptanceChecks?: string[];
};
export type PlanResult = {
  mode: "plan-only"; planningCompleted: true; buildExecuted: false; boardAccessed: false;
  buildPlan: { path: string; sha256: string }; testPlan: { path: string; sha256: string };
  summary: string; blockers: string[]; simulated?: true;
};
export type Experiment = {
  id: string; workflow: string; request: Submit; sourceHash: string; directory: string;
  status: Status; stage: Stage; createdAt: string; error?: string;
  planning?: PlanningContext;
  artifact?: { path: string; sha256: string };
  result?: { simulated: true; correctness: boolean; samples: number[]; unit: "synthetic-cycles" } | PlanResult | RealResult;
};
export type WorkflowEvent = { id: number; workflow: string; type: string; experimentId?: string; data: unknown };
export type Job = { experiment: Experiment; stage: Stage; directory: string; signal: AbortSignal };
export interface Worker { run(job: Job): Promise<void> }
export type ExecutionOptions = { workers: Record<Stage, Worker> };
export type PlanningOptions = { linuxRepo: string; k3Root: string; workers: Record<Stage, Worker> };
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}
export const terminal = (s: Status) => !["queued", "running"].includes(s);
export function workflowName(value: unknown): string {
  if (typeof value !== "string" || !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(value)) throw new ApiError(400, "Invalid workflow name");
  return value;
}
export function validateSubmit(value: unknown): Submit {
  const v = value as any;
  if (!v || typeof v.key !== "string" || !/^[\w.-]{1,100}$/.test(v.key)) throw new ApiError(400, "Invalid idempotency key");
  if (v.profile === "linux-k3-real") return { key: v.key, profile: "linux-k3-real" };
  if (v.profile === "linux-k3-plan") {
    if (typeof v.task !== "string" || !v.task.trim() || Buffer.byteLength(v.task) > 8000
      || !["unixbench", "lmbench"].includes(v.benchmark)) throw new ApiError(400, "Expected task (1..8000 bytes) and benchmark: unixbench|lmbench");
    return { key: v.key, task: v.task, benchmark: v.benchmark, profile: "linux-k3-plan" };
  }
  if (v.profile !== "demo" || typeof v.candidate !== "string" || !v.candidate.length || Buffer.byteLength(v.candidate) > 65536
    || typeof v.hypothesis !== "string" || v.hypothesis.length > 2000) {
    throw new ApiError(400, "Expected key, candidate (1..65536 bytes), hypothesis (<=2000 chars), profile: demo");
  }
  return { key: v.key, candidate: v.candidate, hypothesis: v.hypothesis, profile: "demo" };
}
