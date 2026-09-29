import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync, lstatSync, realpathSync } from "node:fs";
import { join, resolve, sep } from "node:path";
import { Store } from "./store.ts";
import { ApiError, terminal, validateSubmit, workflowName } from "./types.ts";
import type { Experiment, Stage, Worker, PlanningOptions } from "./types.ts";
import { snapshotPlanning, verifyPlanning, validatePlan, validateEvidence } from "./planning.ts";

export const hash = (value: string | Buffer) => createHash("sha256").update(value).digest("hex");
export class Engine {
  store: Store;
  root: string;
  workers: Record<Stage, Worker>;
  planningOptions?: PlanningOptions;
  active = new Map<Stage, { id: string; controller: AbortController; promise: Promise<void> }>();
  stopping = false;
  timer?: ReturnType<typeof setInterval>;
  constructor(root: string, workers: Record<Stage, Worker>, planningOptions?: PlanningOptions) {
    this.root = resolve(root);
    mkdirSync(this.root, { recursive: true, mode: 0o700 });
    this.workers = workers;
    this.planningOptions = planningOptions;
    this.store = new Store(join(this.root, "state.sqlite"));
    // Never blindly replay jobs whose side effects may have started before a crash.
    this.store.transaction(() => {
      for (const exp of this.store.all()) {
        if (exp.status === "running") {
          exp.status = "needs_attention";
          exp.error = "Daemon restarted during execution; not automatically retried";
          this.change(exp, "experiment.needs_attention");
        }
      }
    });
  }
  start() { if (!this.timer) this.timer = setInterval(() => this.tick(), 25); }
  submit(workflow: string, input: unknown): Experiment {
    workflowName(workflow);
    const request = validateSubmit(input);
    return this.store.transaction(() => {
      const duplicate = this.store.all(workflow).find(exp => exp.request.key === request.key);
      if (duplicate) {
        if (JSON.stringify(duplicate.request) !== JSON.stringify(request)) throw new ApiError(409, "Idempotency key reused with different input");
        return duplicate;
      }
      if (request.profile === "linux-k3-plan" && !this.planningOptions) throw new ApiError(409, "linux-k3-plan is not configured; start serve with --plan-config");
      const info = this.store.info(workflow);
      if (this.store.all(workflow).length >= info.budget) throw new ApiError(409, "Experiment budget exhausted (5 per workflow)");
      const id = `exp-${randomUUID()}`;
      const directory = join(this.root, "runs", id);
      mkdirSync(directory, { recursive: true, mode: 0o700 });
      const source = request.profile === "demo" ? request.candidate : JSON.stringify(request);
      const exp: Experiment = { id, workflow, request, sourceHash: hash(source), directory,
        status: "queued", stage: "build", createdAt: new Date().toISOString() };
      // A planning request snapshots selected documents, never the live repository or secrets.
      if (request.profile === "linux-k3-plan") exp.planning = snapshotPlanning(directory, this.planningOptions!.linuxRepo, this.planningOptions!.k3Root);
      writeFileSync(join(directory, request.profile === "demo" ? "candidate.s" : "task.json"), source, { mode: 0o400 });
      writeFileSync(join(directory, "manifest.json"), JSON.stringify(exp, null, 2), { mode: 0o400 });
      this.change(exp, "experiment.queued");
      return exp;
    });
  }
  change(exp: Experiment, type: string) {
    this.store.save(exp);
    this.store.event(exp.workflow, type, { status: exp.status, stage: exp.stage, profile: exp.request.profile, error: exp.error, result: exp.result, artifact: exp.artifact }, exp.id);
  }
  get(workflow: string, id: string): Experiment {
    const exp = this.store.get(id);
    if (!exp || exp.workflow !== workflow) throw new ApiError(404, "Experiment not found");
    return exp;
  }
  result(workflow: string, id: string) {
    const exp = this.get(workflow, id);
    if (exp.request.profile !== "linux-k3-plan") return exp;
    const plans: Record<string, unknown> = {};
    if (exp.artifact) {
      this.verifyArtifact(exp);
      plans.build = JSON.parse(this.checkedFile(join(exp.directory, "build"), "result.json").toString());
    }
    if (exp.result && "mode" in exp.result && exp.result.mode === "plan-only") {
      const bytes = this.checkedFile(join(exp.directory, "test"), "result.json");
      if (hash(bytes) !== exp.result.testPlan.sha256) throw new Error("Test plan hash mismatch");
      plans.test = JSON.parse(bytes.toString());
    }
    return { ...exp, plans };
  }
  pause(workflow: string, paused: boolean) {
    this.store.transaction(() => {
      this.store.pause(workflow, paused);
      this.store.event(workflow, paused ? "workflow.paused" : "workflow.resumed", { paused });
    });
  }
  cancel(workflow: string, id: string) {
    const exp = this.get(workflow, id);
    if (terminal(exp.status)) return exp;
    const active = [...this.active.values()].find(job => job.id === id);
    if (active) {
      active.controller.abort(new Error("Cancellation requested"));
      return { ...exp, cancellationRequested: true };
    }
    exp.status = "cancelled";
    this.store.transaction(() => this.change(exp, "experiment.cancelled"));
    return exp;
  }
  tick() {
    if (this.stopping) return;
    for (const stage of ["build", "test"] as const) {
      if (this.active.has(stage)) continue;
      const exp = this.store.all().find(e => e.status === "queued" && e.stage === stage && !this.store.info(e.workflow).paused);
      if (exp?.request.profile === "linux-k3-plan" && !this.planningOptions) {
        exp.status = "needs_attention"; exp.error = "Planning workers are not configured after restart";
        this.store.transaction(() => this.change(exp, "experiment.needs_attention"));
        continue;
      }
      if (!exp) continue;
      exp.status = "running";
      this.store.transaction(() => this.change(exp, `${stage}.started`));
      const controller = new AbortController();
      const promise = this.execute(exp, controller.signal).finally(() => this.active.delete(stage));
      this.active.set(stage, { id: exp.id, controller, promise });
    }
  }
  private checkedFile(directory: string, name: string): Buffer {
    const path = join(directory, name);
    const stat = lstatSync(path);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 1024 * 1024
      || !realpathSync(path).startsWith(realpathSync(directory) + sep)) throw new Error(`Invalid output file: ${name}`);
    return readFileSync(path);
  }
  private verifySource(exp: Experiment) {
    if (hash(this.checkedFile(exp.directory, exp.request.profile === "demo" ? "candidate.s" : "task.json")) !== exp.sourceHash) throw new Error("Source snapshot was modified");
    if (exp.request.profile === "linux-k3-plan") verifyPlanning(exp);
  }
  private verifyArtifact(exp: Experiment) {
    if (!exp.artifact || hash(this.checkedFile(join(exp.directory, "build"), exp.request.profile === "demo" ? "artifact.txt" : "result.json")) !== exp.artifact.sha256) throw new Error("Artifact hash mismatch");
  }
  private async execute(exp: Experiment, signal: AbortSignal) {
    const stage = exp.stage;
    const directory = join(exp.directory, stage);
    try {
      mkdirSync(directory, { recursive: true, mode: 0o700 });
      this.verifySource(exp);
      if (stage === "test") this.verifyArtifact(exp);
      const workers = exp.request.profile === "linux-k3-plan" ? this.planningOptions!.workers : this.workers;
      await workers[stage].run({ experiment: exp, stage, directory, signal });
      signal.throwIfAborted();
      this.verifySource(exp);
      const result = JSON.parse(this.checkedFile(directory, "result.json").toString());
      if (exp.request.profile === "linux-k3-plan") {
        const report = validatePlan(result, exp, stage);
        validateEvidence(JSON.parse(this.checkedFile(directory, "read-evidence.json").toString()), { experiment: exp, stage, directory, signal });
        if (stage === "build") {
          exp.artifact = { path: join(directory, "result.json"), sha256: hash(this.checkedFile(directory, "result.json")) };
          exp.stage = "test"; exp.status = "queued";
          this.store.transaction(() => this.change(exp, "build.succeeded"));
        } else {
          this.verifyArtifact(exp);
          const build = JSON.parse(this.checkedFile(join(exp.directory, "build"), "result.json").toString());
          exp.result = { mode: "plan-only", planningCompleted: true, buildExecuted: false, boardAccessed: false,
            ...(build.simulated === true || report.simulated === true ? { simulated: true as const } : {}),
            buildPlan: exp.artifact!, testPlan: { path: join(directory, "result.json"), sha256: hash(this.checkedFile(directory, "result.json")) },
            summary: report.summary, blockers: [...new Set([...build.blockers, ...report.blockers])] };
          exp.status = "succeeded";
          this.store.transaction(() => this.change(exp, "experiment.succeeded"));
        }
        return;
      }
      if (result.simulated !== true || result.kind !== stage) throw new Error("Invalid result: must explicitly be a simulated stage result");
      if (stage === "build") {
        if (result.sourceHash !== exp.sourceHash || result.artifact !== "artifact.txt") throw new Error("Build result identity mismatch");
        exp.artifact = { path: join(directory, "artifact.txt"), sha256: hash(this.checkedFile(directory, "artifact.txt")) };
        exp.stage = "test";
        exp.status = "queued";
        this.store.transaction(() => this.change(exp, "build.succeeded"));
      } else {
        this.verifyArtifact(exp);
        if (result.artifactHash !== exp.artifact!.sha256 || typeof result.correctness !== "boolean" || result.unit !== "synthetic-cycles"
          || !Array.isArray(result.samples) || result.samples.length < 3 || result.samples.length > 10000
          || !result.samples.every((x: unknown) => typeof x === "number" && Number.isFinite(x) && x > 0)) throw new Error("Invalid test metrics or artifact identity");
        exp.result = { simulated: true, correctness: result.correctness, samples: result.samples, unit: result.unit };
        if (!result.correctness) throw new Error("Simulated correctness check failed");
        exp.status = "succeeded";
        this.store.transaction(() => this.change(exp, "experiment.succeeded"));
      }
    } catch (error) {
      exp.status = signal.aborted ? "cancelled" : "failed";
      exp.error = String(error);
      this.store.transaction(() => this.change(exp, `experiment.${exp.status}`));
    }
  }
  async close() {
    this.stopping = true;
    clearInterval(this.timer);
    for (const active of this.active.values()) active.controller.abort(new Error("Daemon shutting down (non-executing task cancellation)"));
    await Promise.all([...this.active.values()].map(a => a.promise));
    this.store.close();
  }
}
