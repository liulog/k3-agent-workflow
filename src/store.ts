import { DatabaseSync } from "node:sqlite";
import type { Experiment, WorkflowEvent } from "./types.ts";

export class Store {
  db: DatabaseSync;
  constructor(path: string) {
    this.db = new DatabaseSync(path);
    this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS experiments(id TEXT PRIMARY KEY, workflow TEXT NOT NULL, idem TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(workflow, idem));
      CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, workflow TEXT NOT NULL, type TEXT NOT NULL, experiment_id TEXT, body TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS workflows(name TEXT PRIMARY KEY, paused INTEGER NOT NULL DEFAULT 0, budget INTEGER NOT NULL DEFAULT 5);
    `);
  }
  transaction<T>(fn: () => T): T {
    this.db.exec("BEGIN IMMEDIATE");
    try { const result = fn(); this.db.exec("COMMIT"); return result; }
    catch (error) { this.db.exec("ROLLBACK"); throw error; }
  }
  ensure(name: string) { this.db.prepare("INSERT OR IGNORE INTO workflows(name) VALUES (?)").run(name); }
  info(name: string) {
    this.ensure(name);
    return this.db.prepare("SELECT name, paused, budget FROM workflows WHERE name=?").get(name) as { name: string; paused: number; budget: number };
  }
  pause(name: string, paused: boolean) { this.ensure(name); this.db.prepare("UPDATE workflows SET paused=? WHERE name=?").run(Number(paused), name); }
  all(workflow?: string): Experiment[] {
    const rows = workflow === undefined
      ? this.db.prepare("SELECT body FROM experiments ORDER BY rowid").all()
      : this.db.prepare("SELECT body FROM experiments WHERE workflow=? ORDER BY rowid").all(workflow);
    return rows.map(row => JSON.parse(row.body as string));
  }
  get(id: string): Experiment | undefined {
    const row = this.db.prepare("SELECT body FROM experiments WHERE id=?").get(id);
    return row ? JSON.parse(row.body as string) : undefined;
  }
  save(exp: Experiment) {
    this.db.prepare("INSERT INTO experiments VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET body=excluded.body")
      .run(exp.id, exp.workflow, exp.request.key, JSON.stringify(exp));
  }
  event(workflow: string, type: string, data: unknown, experimentId?: string): WorkflowEvent {
    const row = this.db.prepare("INSERT INTO events(workflow,type,experiment_id,body) VALUES (?,?,?,?)")
      .run(workflow, type, experimentId ?? null, JSON.stringify(data));
    return { id: Number(row.lastInsertRowid), workflow, type, experimentId, data };
  }
  events(workflow: string, after: number): WorkflowEvent[] {
    return this.db.prepare("SELECT * FROM events WHERE workflow=? AND id>? ORDER BY id LIMIT 100").all(workflow, after)
      .map(row => ({ id: Number(row.id), workflow: row.workflow as string, type: row.type as string,
        experimentId: row.experiment_id as string | undefined, data: JSON.parse(row.body as string) }));
  }
  close() { this.db.close(); }
}
