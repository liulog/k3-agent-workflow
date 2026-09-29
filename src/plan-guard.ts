import { readFileSync, lstatSync, realpathSync } from "node:fs";
import { resolve, isAbsolute, relative } from "node:path";
import { digest } from "./planning.ts";

export const GUARD_ID = "k3-plan-only/v1";
export type ReadPolicy = { mode: "plan-only"; files: Array<{ path: string; sha256: string }> };
export type ReadReceipt = { guard: typeof GUARD_ID; path: string; sha256: string; startLine: number; endLine: number; totalLines: number };
export function loadPolicy(path: string | undefined): ReadPolicy {
  if (!path) throw new Error("K3_PLAN_POLICY is required; refusing unguarded reads");
  const p = JSON.parse(readFileSync(path, "utf8"));
  if (p.mode !== "plan-only" || !Array.isArray(p.files) || !p.files.length || !p.files.every((f: any) =>
    typeof f.path === "string" && isAbsolute(f.path) && /^[a-f0-9]{64}$/.test(f.sha256))) throw new Error("Invalid plan read policy");
  return p;
}
export function readSnapshot(policy: ReadPolicy, cwd: string, input: { path: string; offset?: number; limit?: number }) {
  const path = resolve(cwd, input.path);
  const expected = policy.files.find(f => f.path === path);
  if (!expected) throw new Error(`PLAN-ONLY: path is outside the snapshot allowlist. Use an exact allowed path (relative to cwd): ${policy.files.map(f => relative(cwd, f.path)).join(", ")}`);
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink() || realpathSync(path) !== path || stat.size > 256 * 1024) throw new Error("PLAN-ONLY: unsafe snapshot file");
  const bytes = readFileSync(path);
  if (digest(bytes) !== expected.sha256) throw new Error("PLAN-ONLY: snapshot hash changed");
  const lines = bytes.toString("utf8").split("\n");
  const start = input.offset ?? 1, limit = input.limit ?? 2000;
  if (!Number.isInteger(start) || start < 1 || start > lines.length || !Number.isInteger(limit) || limit < 1 || limit > 2000) throw new Error("Invalid read offset/limit");
  const selected: string[] = []; let length = 0;
  for (let i = start - 1; i < Math.min(lines.length, start - 1 + limit); i++) {
    const size = Buffer.byteLength(lines[i]) + 1;
    if (length + size > 32000) break;
    selected.push(lines[i]); length += size;
  }
  if (!selected.length) throw new Error("Snapshot line exceeds read limit");
  const end = start + selected.length - 1;
  const receipt: ReadReceipt = { guard: GUARD_ID, path, sha256: expected.sha256, startLine: start, endLine: end, totalLines: lines.length };
  return { content: [{ type: "text" as const, text: selected.join("\n") + (end < lines.length ? `\n[Continue reading this file with offset=${end + 1}]` : "") }], details: receipt };
}
export function completeReads(receipts: ReadReceipt[], files: ReadPolicy["files"]): string[] {
  return files.filter(file => {
    const ranges = receipts.filter(r => r.guard === GUARD_ID && r.path === file.path && r.sha256 === file.sha256).sort((a, b) => a.startLine - b.startLine);
    let next = 1;
    const total = ranges[0]?.totalLines;
    if (!Number.isInteger(total) || total! < 1) return false;
    for (const r of ranges) {
      if (r.totalLines !== total || !Number.isInteger(r.startLine) || !Number.isInteger(r.endLine) || r.startLine < 1 || r.endLine < r.startLine || r.endLine > total! || r.startLine > next) return false;
      next = Math.max(next, r.endLine + 1);
    }
    return next > total!;
  }).map(f => f.path);
}
