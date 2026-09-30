import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync, lstatSync, realpathSync } from "node:fs";
import { join, resolve, sep } from "node:path";
import { Store } from "./store.ts";
import { ApiError, terminal, validateSubmit, workflowName } from "./types.ts";
import type { Experiment, Stage, Worker, PlanningOptions, ExecutionOptions } from "./types.ts";
import { snapshotPlanning, verifyPlanning, validatePlan, validateEvidence } from "./planning.ts";

export const hash = (value: string | Buffer) => createHash("sha256").update(value).digest("hex");
export class Engine {
  store: Store;
  root: string;
  workers: Record<Stage, Worker>;
  planningOptions?: PlanningOptions;
  executionOptions?: ExecutionOptions;
  active?: { id: string; controller: AbortController; promise: Promise<void> };
  stopping = false;
  timer?: ReturnType<typeof setInterval>;
  constructor(root: string, workers: Record<Stage, Worker>, planningOptions?: PlanningOptions, executionOptions?: ExecutionOptions) {
    this.root = resolve(root);
    mkdirSync(this.root, { recursive: true, mode: 0o700 });
    this.workers = workers;
    this.planningOptions = planningOptions;
    this.executionOptions = executionOptions;
    this.store = new Store(join(this.root, "state.sqlite"));
    // Never blindly replay jobs whose side effects may have started before a crash.
    this.store.transaction(() => {
      for (const exp of this.store.all()) {
        if (exp.status === "running" || (exp.request.profile === "linux-k3-real" && exp.status === "queued")) {
          exp.status = "needs_attention";
          exp.error = "Daemon restarted; interrupted/real work is not automatically retried";
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
      if (request.profile === "linux-k3-real" && (!this.executionOptions || this.store.all().some(e => e.request.profile === "linux-k3-real"))) throw new ApiError(409, "Real execution requires an explicitly authorized one-shot server; only one real experiment is allowed");
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
    if (this.active?.id === id) {
      if (exp.request.profile === "linux-k3-real" && exp.stage === "test") throw new ApiError(409, "Hardware stage cannot be cancelled by killing local processes; inspect and stop the owned remote runner explicitly");
      this.active.controller.abort(new Error("Cancellation requested"));
      return { ...exp, cancellationRequested: true };
    }
    exp.status = "cancelled";
    this.store.transaction(() => this.change(exp, "experiment.cancelled"));
    return exp;
  }
  tick() {
    if (this.stopping || this.active) return;
    // FIFO by experiment: its queued Test stays ahead of later Builds unless paused.
    const exp = this.store.all().find(e => e.status === "queued" && !this.store.info(e.workflow).paused);
    if (!exp) return;
    if (exp.request.profile === "linux-k3-plan" && !this.planningOptions) {
      exp.status = "needs_attention"; exp.error = "Planning workers are not configured after restart";
      this.store.transaction(() => this.change(exp, "experiment.needs_attention"));
      return;
    }
    exp.status = "running";
    this.store.transaction(() => this.change(exp, `${exp.stage}.started`));
    const controller = new AbortController();
    const promise = this.execute(exp, controller.signal).finally(() => { this.active = undefined; });
    this.active = { id: exp.id, controller, promise };
  }
  private checkedFile(directory: string, name: string, maxBytes = 1024 * 1024): Buffer {
    const path = join(directory, name);
    const stat = lstatSync(path);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > maxBytes
      || !realpathSync(path).startsWith(realpathSync(directory) + sep)) throw new Error(`Invalid output file: ${name}`);
    return readFileSync(path);
  }
  private verifySource(exp: Experiment) {
    if (hash(this.checkedFile(exp.directory, exp.request.profile === "demo" ? "candidate.s" : "task.json")) !== exp.sourceHash) throw new Error("Source snapshot was modified");
    if (exp.request.profile === "linux-k3-plan") verifyPlanning(exp);
  }
  private verifyArtifact(exp: Experiment) {
    const name = exp.request.profile === "demo" ? "artifact.txt" : exp.request.profile === "linux-k3-real" ? "Image" : "result.json";
    if (!exp.artifact || hash(this.checkedFile(join(exp.directory, "build"), name, name === "Image" ? 512 * 1024 * 1024 : 1024 * 1024)) !== exp.artifact.sha256) throw new Error("Artifact hash mismatch");
  }
  private async execute(exp: Experiment, signal: AbortSignal) {
    const stage = exp.stage;
    const directory = join(exp.directory, stage);
    try {
      mkdirSync(directory, { recursive: true, mode: 0o700 });
      this.verifySource(exp);
      if (stage === "test") this.verifyArtifact(exp);
      const workers = exp.request.profile === "linux-k3-real" ? this.executionOptions!.workers : exp.request.profile === "linux-k3-plan" ? this.planningOptions!.workers : this.workers;
      await workers[stage].run({ experiment: exp, stage, directory, signal });
      signal.throwIfAborted();
      this.verifySource(exp);
      if (stage === "test") this.verifyArtifact(exp);
      const resultBytes = this.checkedFile(directory, "result.json");
      const result = JSON.parse(resultBytes.toString());
      if (exp.request.profile === "linux-k3-real") {
        if (result.mode !== "real" || result.simulated !== false || result.kind !== stage || result.experimentId !== exp.id || result.sourceHash !== exp.sourceHash) throw new Error("Invalid real execution identity");
        if (stage === "build") {
          const sha256 = hash(this.checkedFile(directory, "Image", 512 * 1024 * 1024));
          if (result.artifactHash !== sha256 || result.buildExecuted !== true || result.configUnchanged !== true || result.sourceUnchanged !== true) throw new Error("Real build identity/configuration check failed");
          exp.artifact = { path: join(directory, "Image"), sha256 };
        } else {
          const required = ["status", "exitCode", "scores", "anomalies", "rawResults", "imageIdentity", "cleanup"];
          if (result.artifactHash !== exp.artifact!.sha256 || result.benchmark !== "unixbench" || result.boardAccessed !== true || !required.every(k => result.checks?.[k] === true)) throw new Error("Incomplete or failed real benchmark evidence");
          exp.result = { mode: "real", simulated: false, buildExecuted: true, boardAccessed: true, benchmark: "unixbench", artifactHash: exp.artifact!.sha256, evidence: join(directory, "result.json"), checks: result.checks };
        }
      } else if (exp.request.profile === "linux-k3-plan") {
        const report = validatePlan(result, exp, stage);
        validateEvidence(JSON.parse(this.checkedFile(directory, "read-evidence.json").toString()), { experiment: exp, stage, directory, signal });
        if (stage === "build") {
          exp.artifact = { path: join(directory, "result.json"), sha256: hash(resultBytes) };
        } else {
          const build = JSON.parse(this.checkedFile(join(exp.directory, "build"), "result.json").toString());
          exp.result = { mode: "plan-only", planningCompleted: true, buildExecuted: false, boardAccessed: false,
            ...(build.simulated === true || report.simulated === true ? { simulated: true as const } : {}),
            buildPlan: exp.artifact!, testPlan: { path: join(directory, "result.json"), sha256: hash(resultBytes) },
            summary: report.summary, blockers: [...new Set([...build.blockers, ...report.blockers])] };
        }
      } else {
        if (result.simulated !== true || result.kind !== stage) throw new Error("Invalid result: must explicitly be a simulated stage result");
        if (stage === "build") {
          if (result.sourceHash !== exp.sourceHash || result.artifact !== "artifact.txt") throw new Error("Build result identity mismatch");
          exp.artifact = { path: join(directory, "artifact.txt"), sha256: hash(this.checkedFile(directory, "artifact.txt")) };
        } else {
          if (result.artifactHash !== exp.artifact!.sha256 || typeof result.correctness !== "boolean" || result.unit !== "synthetic-cycles"
            || !Array.isArray(result.samples) || result.samples.length < 3 || result.samples.length > 10000
            || !result.samples.every((x: unknown) => typeof x === "number" && Number.isFinite(x) && x > 0)) throw new Error("Invalid test metrics or artifact identity");
          exp.result = { simulated: true, correctness: result.correctness, samples: result.samples, unit: result.unit };
          if (!result.correctness) throw new Error("Simulated correctness check failed");
        }
      }
      // Both profiles share the same two-stage state machine; only result contracts differ.
      exp.stage = "test";
      exp.status = stage === "build" ? "queued" : "succeeded";
      this.store.transaction(() => this.change(exp, stage === "build" ? "build.succeeded" : "experiment.succeeded"));
    } catch (error) {
      exp.status = exp.request.profile === "linux-k3-real" && stage === "test" ? "needs_attention" : signal.aborted ? "cancelled" : "failed";
      exp.error = String(error);
      this.store.transaction(() => this.change(exp, `experiment.${exp.status}`));
    }
  }
  async close() {
    this.stopping = true;
    clearInterval(this.timer);
    const active = this.active;
    const current = active && this.store.get(active.id);
    // A local abort cannot establish remote board safety. Wait for the owned test.
    if (!(current?.request.profile === "linux-k3-real" && current.stage === "test")) active?.controller.abort(new Error("Daemon shutting down"));
    await active?.promise;
    this.store.close();
  }
}
