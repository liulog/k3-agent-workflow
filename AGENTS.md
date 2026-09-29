# Project rules

- Node.js 24+ runs TypeScript directly (erasable syntax only). No build step.
- Do not install dependencies or compile/build without explicit user authorization.
- `node --test tests/*.test.ts` and `node scripts/demo.ts` are hardware-free, model-free checks. They do not compile code.
- Never make demo output look like real benchmark results. All demo results must contain `simulated: true`.
- `linux-k3-plan` is a separate plan-only profile: no compiler, shell, k3ctl (even --dry-run), SSH, serial, power or benchmark execution. Plans contain `commandsExecuted: false` and `boardAccessed: false`; never interpret proposed command strings as authorization.
- `scripts/plan-experiment.ts` uses fake Pi by default; `--real-models` contacts model APIs only when explicitly authorized. `check-plan` performs static file checks only.
- No SSH, flashing, physical board operations or arbitrary command execution in this MVP.
- Preserve source/artefact identity, idempotency, durable events, and exclusive worker execution.
- Changes to Pi integration must follow the installed Pi documentation.
