import { spawn } from "node:child_process";
import { openSync, closeSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Worker, Job } from "./types.ts";

// Fixed, operator-authorized commands. No model-generated shell is executed.
export class RealWorker implements Worker {
  linuxRepo: string;
  k3Root: string;
  constructor(linuxRepo: string, k3Root: string) { this.linuxRepo = linuxRepo; this.k3Root = k3Root; }
  async run(job: Job) {
    job.signal.throwIfAborted();
    const spec = { stage: job.stage, id: job.experiment.id, sourceHash: job.experiment.sourceHash,
      directory: job.directory, linuxRepo: this.linuxRepo, k3Root: this.k3Root,
      artifact: job.experiment.artifact };
    const path = join(job.directory, "execution.json");
    writeFileSync(path, JSON.stringify(spec, null, 2), { mode: 0o600 });
    const fd = openSync(join(job.directory, "worker.log"), "wx", 0o600);
    const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1" };
    for (const key of Object.keys(env)) if (/TOKEN|SECRET|PASSWORD|API_KEY|APIKEY|SSHPASS/i.test(key)) delete (env as NodeJS.ProcessEnv)[key];
    try {
      await new Promise<void>((resolve, reject) => {
        const child = spawn("python3", [fileURLToPath(new URL("../scripts/real-stage.py", import.meta.url)), path], {
          cwd: job.directory, stdio: ["ignore", fd, fd], detached: true,
          env,
        });
        let error: Error | undefined;
        const stop = () => { if (child.pid) { try { process.kill(-child.pid, "SIGTERM"); } catch {} } };
        // Never equate killing an SSH client with safely cancelling a hardware task.
        if (job.stage === "build") job.signal.addEventListener("abort", stop, { once: true });
        child.on("error", e => { error = e; });
        child.on("close", code => {
          job.signal.removeEventListener("abort", stop);
          if (job.stage === "build" && child.pid) { try { process.kill(-child.pid, "SIGKILL"); } catch {} }
          if (error || code !== 0) {
            let detail = `Real ${job.stage} exited ${code}; inspect ${job.directory}/worker.log`;
            try { detail = JSON.parse(readFileSync(join(job.directory, "failure.json"), "utf8")).error; } catch {}
            reject(error ?? new Error(detail));
          } else resolve();
        });
      });
    } finally { closeSync(fd); }
  }
}
