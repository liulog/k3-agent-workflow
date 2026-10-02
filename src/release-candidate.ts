// Narrow, idempotent candidate: release-only AMOSWAP -> fence + ordinary store.
// Never touch acquire AMOs, SATP, interrupt state, configuration or unrelated edits.
import { readFileSync, writeFileSync, mkdirSync, lstatSync } from "node:fs";
import { join, dirname } from "node:path";
import { hash } from "./engine.ts";
export const gateFiles = ["arch/riscv/kernel/haoc/iee/iee-gate-csrrsi.S", "arch/riscv/kernel/haoc/iee/iee-gate-csrrsi-fast.S"];
const references = ["Documentation/DESING.md", "arch/riscv/include/asm/haoc/iee-asm.h", "arch/riscv/include/asm/haoc/iee-csrrsi.h", "arch/riscv/include/asm/barrier.h", "arch/riscv/kernel/haoc/iee/iee-init.c", "arch/riscv/kernel/haoc/iee/iee-mmu-csrrsi-fast.c", ".config"];
export function releaseCandidate(text: string) {
  let replacements = 0;
  const content = text.replace(/^([ \t]*)amoswap\.w\.rl[ \t]+zero,[ \t]*zero,[ \t]*\((t[0-6])\)([^\r\n]*)$/gm,
    (_line, indent, owner, suffix) => { replacements++; return `${indent}fence           rw, w\n${indent}sw              zero, 0(${owner})${suffix}`; });
  return { content, replacements };
}
export function prepareCandidate(linuxRepo: string, root: string) {
  const directory = join(root, "candidate");
  mkdirSync(directory, { mode: 0o700 });
  const files = [...gateFiles, ...references].map(path => {
    const source = join(linuxRepo, path), stat = lstatSync(source);
    const limit = path === ".config" ? 2 * 1024 * 1024 : 256 * 1024;
    if (!stat.isFile() || stat.size > limit) throw new Error(`Unsafe candidate input: ${path}`);
    const content = readFileSync(source, "utf8");
    const destination = join(directory, "before", path);
    mkdirSync(dirname(destination), { recursive: true, mode: 0o700 });
    writeFileSync(destination, content, { mode: 0o400 });
    const candidate = gateFiles.includes(path) ? releaseCandidate(content) : { content, replacements: 0 };
    const review = path === ".config"
      ? content.split(/\r?\n/).filter(line => /^((CONFIG_(IEE|PTP|CREDP)|# CONFIG_(IEE|PTP|CREDP)))/.test(line)).join("\n")
      : content;
    return { path, sha256: hash(content), size: stat.size,
      ...(gateFiles.includes(path) ? { before: content, after: candidate.content } : {}), review,
      replacements: candidate.replacements };
  });
  const config = files.find(f => f.path === ".config")!;
  const settings = Object.fromEntries(config.review.split("\n").map((line: string) => {
    const match = line.match(/^(?:CONFIG_(\w+)=(\w+)|# CONFIG_(\w+) is not set)$/);
    return match ? [match[1] ?? match[3], match[2] ?? "n"] : [];
  }));
  const required = ["IEE", "IEE_GATE_CSRRSI", "PTP", "IEE_GATE_CSRRSI_FAST"];
  const missing = required.filter(name => settings[name] !== "y");
  if (missing.length) throw new Error(`Existing .config does not enable required experiment options: ${missing.join(", ")}; refusing to edit config or build`);
  const context = { linuxRepo, directory, settings, required, files,
    replacements: files.reduce((n, f) => n + f.replacements, 0),
    constraints: "Existing .config must already have IEE, IEE_GATE_CSRRSI, PTP and IEE_GATE_CSRRSI_FAST=y. Do not edit it. Preserve optional CREDP/IEE_SIP as currently configured. Only the recognized release-only zero/zero AMO may change. Preserve acquire AMOs, release ordering, SATP, interrupts and all pre-existing source edits. No baseline comparison means no performance-improvement claim." };
  writeFileSync(join(directory, "context.json"), JSON.stringify(context, null, 2), { mode: 0o400 });
  return directory;
}
export function applyCandidate(directory: string) {
  const context = JSON.parse(readFileSync(join(directory, "context.json"), "utf8"));
  const approval = JSON.parse(readFileSync(join(directory, "approval.json"), "utf8"));
  if (approval.contextHash !== hash(readFileSync(join(directory, "context.json")))) throw new Error("Candidate approval identity mismatch");
  // Validate every input before writing anything, preserving all pre-existing changes.
  for (const file of context.files) if (hash(readFileSync(join(context.linuxRepo, file.path))) !== file.sha256) throw new Error(`Source changed since review: ${file.path}`);
  for (const file of context.files) {
    if (!file.replacements) continue;
    if (!gateFiles.includes(file.path) || releaseCandidate(file.before).content !== file.after) throw new Error("Unexpected candidate transformation");
    writeFileSync(join(context.linuxRepo, file.path), file.after);
  }
  writeFileSync(join(directory, "applied.json"), JSON.stringify({ replacements: context.replacements, changedFiles: context.files.filter((f: any) => f.replacements).map((f: any) => f.path), existingEditsPreserved: true, configChanged: false }, null, 2), { flag: "wx", mode: 0o400 });
}
