# Project rules

- Node.js 24+ runs TypeScript directly (erasable syntax only). No build step.
- Do not install dependencies or compile/build without explicit user authorization.
- `node --test tests/*.test.ts` and `node scripts/demo.ts` are hardware-free, model-free checks. They do not compile code.
- Never make demo output look like real benchmark results. All demo results must contain `simulated: true`.
- `linux-k3-plan` is a separate plan-only profile: no compiler, shell, k3ctl (even --dry-run), SSH, serial, power or benchmark execution. Plans contain `commandsExecuted: false` and `boardAccessed: false`; never interpret proposed command strings as authorization.
- `scripts/plan-experiment.ts` uses fake Pi by default; `--real-models` contacts model APIs only when explicitly authorized. `check-plan` performs static file checks only.
- Default demo/plan-only paths must never execute compilation or board operations. The user explicitly authorized one existing-.config Image build and one UnixBench run via `scripts/real-experiment.ts --execute`; this separate opt-in path uses fixed scripts, not arbitrary model shell commands. Never clean, run defconfig, edit .config, install dependencies, flash storage or automatically retry hardware tasks.
- Real test failures/interruption require attention. Killing local SSH is not board cancellation; preserve the remote lease until safe cleanup is verified. Do not steal a lease or stop another user's runner.
- Keep this a task-specific MVP, not a general workflow platform. Prefer deleting duplication to adding configuration, abstractions, dependencies or protocols.
- One global active Worker: process each unpaused experiment Build → Test in FIFO order; no cross-stage pipeline parallelism.
- Preserve source/artefact identity, idempotency, durable events, and exclusive worker execution. Simplification must retain cancellation cleanup, failure isolation and read-only boundaries; verify changes with offline tests.
- Never commit real API keys, passwords, private keys, local authentication/configuration files or raw experiment logs. Examples must use empty values or obvious placeholders. Review staged changes before pushing; ignore rules do not remove secrets from Git history.
- Changes to Pi integration must follow the installed Pi documentation.
