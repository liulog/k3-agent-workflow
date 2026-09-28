# Project rules

- Node.js 24+ runs TypeScript directly (erasable syntax only). No build step.
- Do not install dependencies or compile/build without explicit user authorization.
- `node --test tests/*.test.ts` and `node scripts/demo.ts` are hardware-free, model-free checks. They do not compile code.
- Never make demo output look like real benchmark results. All demo results must contain `simulated: true`.
- No SSH, flashing, physical board operations or arbitrary command execution in this MVP.
- Preserve source/artefact identity, idempotency, durable events, and exclusive worker execution.
- Changes to Pi integration must follow the installed Pi documentation.
