import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import sqlite3 from 'sqlite3';
import { open, type Database } from 'sqlite';

async function readJson(file: string) {
    try { return JSON.parse(await readFile(file, 'utf8')); } catch { return null; }
}
const number = (v: unknown): number | null => typeof v === 'number' && Number.isFinite(v) ? v : null;
const text = (v: unknown) => typeof v === 'string' ? v.slice(0, 300) : null;
export type ModelRow = {
    key: string; model: string; mode: 'raw' | 'chat'; state: string; family: string;
    parameters: number | null; weightBytes: number | null; gpuCount: number | null;
    provider: string | null; inputUsdPerMillion: number | null; outputUsdPerMillion: number | null;
    supportedParameters: string[]; updated: number; records: number | null; expected: number;
    elapsedSeconds: number | null; workerUpdated: number | null; workerAlive: boolean | null;
};
export class Telemetry {
    private queue?: Database;
    private spend?: Database;
    private cached: { at: number; value: any } | null = null;
    private pending: Promise<any> | null = null;
    constructor(readonly directory?: string) {}
    async snapshot() {
        if (this.cached && Date.now() - this.cached.at < 1500) return this.cached.value;
        if (!this.pending) this.pending = this.load().then(value => { this.cached = { at: Date.now(), value }; return value; }).finally(() => { this.pending = null; });
        return this.pending;
    }
    private async load() {
        if (!this.directory) return { available: false, observedAt: Date.now(), reason: 'No enrollment run is configured.', models: [] };
        try {
            this.queue ||= await open({ filename: join(this.directory, 'queue.sqlite'), driver: sqlite3.Database, mode: sqlite3.OPEN_READONLY });
            await this.queue.exec('PRAGMA busy_timeout=3000');
            const rows = await this.queue.all('SELECT key,model,mode,state,item,run,idx,pid,updated FROM jobs');
            const counts: Record<string, Record<string, number>> = { raw: {}, chat: {} };
            const models: ModelRow[] = [];
            for (const row of rows) {
                if (!['raw', 'chat'].includes(row.mode)) continue;
                counts[row.mode][row.state] = (counts[row.mode][row.state] || 0) + 1;
                let item: any = {}; try { item = JSON.parse(row.item || '{}'); } catch { /* missing discovery metadata */ }
                let status: any = null;
                if (row.run && ['running', 'chat_running', 'failed', 'chat_failed', 'ready', 'chat_prepared'].includes(row.state)) {
                    status = await readJson(join(row.run, 'models', String(row.idx || 0).padStart(3, '0'), 'status.json'));
                }
                let alive: boolean | null = null;
                if (['running', 'chat_running'].includes(row.state) && Number.isSafeInteger(row.pid) && row.pid > 0) {
                    try { process.kill(row.pid, 0); alive = true; } catch { alive = false; }
                }
                const pricing = item.pricing || {};
                const rate = (v: any) => v == null || v === '' ? null : number(Number(v) * 1e6);
                models.push({ key: row.key, model: row.model, mode: row.mode, state: row.state,
                    family: row.model.split('/')[0], parameters: number(item.parameters), weightBytes: number(item.weightBytes),
                    gpuCount: number(item.gpuCount), provider: text(item.tag),
                    inputUsdPerMillion: rate(pricing.prompt), outputUsdPerMillion: rate(pricing.completion),
                    supportedParameters: Array.isArray(item.supported_parameters) ? item.supported_parameters.filter((x: any) => typeof x === 'string') : [],
                    updated: row.updated * 1000, records: ['complete', 'chat_complete'].includes(row.state) ? 800 : number(status?.records),
                    expected: 800, elapsedSeconds: number(status?.elapsedSeconds), workerUpdated: status?.updated ? status.updated * 1000 : null, workerAlive: alive });
            }
            const raw = await readJson(join(this.directory, 'status.json'));
            const chat = await readJson(join(this.directory, 'chat-status.json'));
            let budget: any = { available: false };
            try {
                this.spend ||= await open({ filename: join(this.directory, 'chat-spend.sqlite'), driver: sqlite3.Database, mode: sqlite3.OPEN_READONLY });
                const b = await this.spend.get(`SELECT COALESCE(SUM(actual),0) spent,
                    COALESCE(SUM(CASE WHEN actual IS NULL THEN reserved ELSE 0 END),0) reserved, MAX(created) lastRequest FROM charges`);
                budget = { available: true, reportedUsd: b.spent, reservedUsd: b.reserved,
                    operationalCapUsd: number(chat?.spendingCeilingUsd), lastRequestAt: b.lastRequest ? b.lastRequest * 1000 : null };
            } catch { /* A raw-only run legitimately has no API ledger. */ }
            return { available: true, observedAt: Date.now(), counts, models, budget,
                controllers: { rawUpdatedAt: raw?.updated ? raw.updated * 1000 : null, chatUpdatedAt: chat?.updated ? chat.updated * 1000 : null } };
        } catch {
            // Filesystem paths, credentials and provider response bodies stay private.
            return { available: false, observedAt: Date.now(), reason: 'Enrollment telemetry is temporarily unavailable.', models: [] };
        }
    }
    async close() { await this.pending; await this.queue?.close(); await this.spend?.close(); }
}
