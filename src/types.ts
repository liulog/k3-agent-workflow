export type Stage = "build" | "test";
export type Status = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "needs_attention";
export type Submit = { key: string; candidate: string; hypothesis: string; profile: "demo" };
export type Experiment = {
  id: string; workflow: string; request: Submit; sourceHash: string; directory: string;
  status: Status; stage: Stage; createdAt: string; error?: string;
  artifact?: { path: string; sha256: string };
  result?: { simulated: true; correctness: boolean; samples: number[]; unit: "synthetic-cycles" };
};
export type WorkflowEvent = { id: number; workflow: string; type: string; experimentId?: string; data: unknown };
export type Job = { experiment: Experiment; stage: Stage; directory: string; signal: AbortSignal };
export interface Worker { run(job: Job): Promise<void> }
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
  const v = value as Submit;
  if (!v || v.profile !== "demo" || typeof v.key !== "string" || !/^[\w.-]{1,100}$/.test(v.key)
    || typeof v.candidate !== "string" || !v.candidate.length || Buffer.byteLength(v.candidate) > 65536
    || typeof v.hypothesis !== "string" || v.hypothesis.length > 2000) {
    throw new ApiError(400, "Expected key, candidate (1..65536 bytes), hypothesis (<=2000 chars), profile: demo");
  }
  return { key: v.key, candidate: v.candidate, hypothesis: v.hypothesis, profile: "demo" };
}
