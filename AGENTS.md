# Project rules

- Node.js 24+ runs TypeScript directly (erasable syntax only). No build step.
- Do not install dependencies or compile/build without explicit user authorization.
- `node --test tests/*.test.ts` and `node scripts/demo.ts` are hardware-free, model-free checks. They do not compile code.
- Never make demo output look like real benchmark results. All demo results must contain `simulated: true`.
- `linux-k3-plan` is a separate plan-only profile: no compiler, shell, k3ctl (even --dry-run), SSH, serial, power or benchmark execution. Plans contain `commandsExecuted: false` and `boardAccessed: false`; never interpret proposed command strings as authorization.
- `scripts/plan-experiment.ts` uses fake Pi by default; `--real-models` contacts model APIs only when explicitly authorized. `check-plan` performs static file checks only.
- Default demo/plan-only paths must never execute compilation or board operations. `scripts/real-experiment.ts --execute` is a separate explicitly authorized one-shot path with fixed scripts, not arbitrary model shell commands. It never cleans, regenerates .config, installs dependencies, flashes storage, or automatically retries hardware tasks. Isolated matrix builds require their own explicit authorization; do not expand a prior build authorization to new configurations.
- Matrix communication retries are disabled by default. Only an explicit user-authorized `--communication-retries 1`, `--resume --retry-failed`, or watchdog `--authorize-power-cycle-retry` permits the fixed recovery mechanism: at most one durably reserved power-cycle retry per round, preserving old evidence and using a new RUN_ID. Five-minute read-only network waits do not reset the retry budget. Panic/reboot, authentication, frequency/identity/evidence failures and resource conflicts require attention, never automatic retry.
- Killing local SSH is not board cancellation. Before recovery, verify root-visible free serial, no host runner or board benchmark, and own/absent lease with no runner.pid. Unknown board state forbids power changes; hold/verify the lease through recovery. Never steal a lease or stop another user's runner. Do not restart an active matrix to deploy code changes.
- Keep this a task-specific MVP, not a general workflow platform. Prefer deleting duplication to adding configuration, abstractions, dependencies or protocols.
- One global active Worker: process each unpaused experiment Build → Test in FIFO order; no cross-stage pipeline parallelism.
- Preserve source/artefact identity, idempotency, durable events, and exclusive worker execution. Simplification must retain cancellation cleanup, failure isolation and read-only boundaries; verify changes with offline tests.
- Never commit real API keys, passwords, private keys, local authentication/configuration files or raw experiment logs. Examples must use empty values or obvious placeholders. Review staged changes before pushing; ignore rules do not remove secrets from Git history.
- Changes to Pi integration must follow the installed Pi documentation.
