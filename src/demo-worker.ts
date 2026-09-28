import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { setTimeout } from "node:timers/promises";
import type { Job, Worker } from "./types.ts";

// Deliberately no assembler/compiler, model API or hardware. Never a performance claim.
export class DemoWorker implements Worker {
  delay: number;
  constructor(delay = 80) { this.delay = delay; }
  async run(job: Job) {
    await setTimeout(this.delay, undefined, { signal: job.signal });
    const { experiment: exp, directory, stage } = job;
    const source = await readFile(join(exp.directory, "candidate.s"), "utf8");
    if (stage === "build") {
      if (source.includes("DEMO_BUILD_FAIL")) throw new Error("Requested simulated build failure");
      await writeFile(join(directory, "artifact.txt"), `SIMULATED ARTIFACT — NOT EXECUTABLE\n${source}`);
      await writeFile(join(directory, "result.json"), JSON.stringify({ simulated: true, kind: "build", sourceHash: exp.sourceHash, artifact: "artifact.txt" }));
    } else {
      if (source.includes("DEMO_TEST_FAIL")) throw new Error("Requested simulated test failure");
      await writeFile(join(directory, "result.json"), JSON.stringify({ simulated: true, kind: "test", artifactHash: exp.artifact?.sha256,
        correctness: !source.includes("DEMO_INCORRECT"), samples: [100, 101, 99, 100, 100], unit: "synthetic-cycles" }));
    }
    await writeFile(join(directory, "worker.log"), `Simulation only: ${stage} finished\n`);
  }
}
