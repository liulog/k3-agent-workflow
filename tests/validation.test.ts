import test from "node:test";
import assert from "node:assert/strict";
import { chmodSync, writeFileSync, unlinkSync, symlinkSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { Engine } from "../src/engine.ts";
import { DemoWorker } from "../src/demo-worker.ts";
import { terminal } from "../src/types.ts";
import { temp, until, request, cleanup } from "./helpers.ts";

test("changed source snapshot is rejected before dispatch", async t => {
  const engine = new Engine(await temp(t), { build: new DemoWorker(1), test: new DemoWorker(1) });
  cleanup(t, () => engine.close());
  const exp = engine.submit("asm", request());
  const source = join(exp.directory, "candidate.s");
  chmodSync(source, 0o600); writeFileSync(source, "different source"); engine.start();
  await until(() => terminal(engine.store.get(exp.id)!.status));
  assert.match(engine.store.get(exp.id)!.error!, /snapshot was modified/);
});
for (const scenario of ["symlink", "identity", "samples", "correctness"]) {
  test(`invalid ${scenario} output never passes validation`, async t => {
    const demo = new DemoWorker(1);
    const worker = { async run(job: any) {
      await demo.run(job);
      if (scenario === "symlink" && job.stage === "build") {
        const path = join(job.directory, "artifact.txt");
        unlinkSync(path); symlinkSync(join(job.experiment.directory, "candidate.s"), path);
      } else if (job.stage === "test") {
        const path = join(job.directory, "result.json"), result = JSON.parse(readFileSync(path, "utf8"));
        if (scenario === "identity") result.artifactHash = "wrong";
        if (scenario === "samples") result.samples = [0, -1, "fast"];
        if (scenario === "correctness") result.correctness = false;
        writeFileSync(path, JSON.stringify(result));
      }
    } };
    const engine = new Engine(await temp(t), { build: worker, test: worker });
    cleanup(t, () => engine.close());
    const exp = engine.submit("asm", request()); engine.start();
    await until(() => terminal(engine.store.get(exp.id)!.status));
    assert.equal(engine.store.get(exp.id)!.status, "failed");
  });
}
