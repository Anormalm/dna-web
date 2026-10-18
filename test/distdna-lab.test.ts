import { vi, beforeAll, afterAll, it, expect } from 'vitest';
import express from 'express';
import request from 'supertest';
import { open } from 'sqlite';
import sqlite3 from 'sqlite3';
import { mkdirSync, rmSync } from 'node:fs';
import { dirname } from 'node:path';
vi.hoisted(() => {
    process.env.LAB_DB_PATH = `server/data/distdna-lab-test-${process.pid}.db`;
    process.env.TURNSTILE_SECRET_KEY = '';
});
vi.mock('dotenv', () => ({ config: () => ({}) }));
vi.mock('../server/services/email.js', () => ({
    sendVerificationEmail: vi.fn().mockResolvedValue(undefined),
}));
import { initDatabase, getDatabase, closeDatabase } from '../server/database';
import submission from '../server/services/submission';
import verification from '../server/services/verification';
import proposals from '../server/services/proposals';
import { sendVerificationEmail } from '../server/services/email';
const app = express();
app.use(express.json());
app.use('/api', submission, verification, proposals);
const path = process.env.LAB_DB_PATH!;
beforeAll(async () => {
    mkdirSync(dirname(path), { recursive: true });
    rmSync(path, { force: true });
    const db = await open({ filename: path, driver: sqlite3.Database });
    await db.exec(
        `CREATE TABLE proposals(id TEXT PRIMARY KEY,model_id TEXT,submitter_email TEXT,reason TEXT,votes INTEGER DEFAULT 0,status TEXT DEFAULT 'pending',created_at TEXT DEFAULT (datetime('now'))); INSERT INTO proposals(id,model_id,submitter_email) VALUES('old','test-org/existing','test@example.com');`,
    );
    await db.close();
    await initDatabase();
});
afterAll(async () => {
    await closeDatabase();
    for (const ext of ['', '-wal', '-shm']) rmSync(path + ext, { force: true });
});
it('migrates legacy proposals to v1 while keeping v2 queues separate', async () => {
    let r = await request(app).get('/api/proposals');
    expect(r.body.map((p: any) => p.id)).toEqual(['old']);
    expect(r.body[0].version).toBe('v1');
    expect(r.body[0].mode).toBe('raw');
    r = await request(app).get('/api/proposals?version=v2');
    expect(r.body).toEqual([]);
    r = await request(app).get('/api/proposals?version=wrong');
    expect(r.status).toBe(400);
});
it('submits and verifies a v2 request for a model already proposed in v1', async () => {
    const r = await request(app)
        .post('/api/submit')
        .send({
            email: 'test@example.com',
            captchaToken: 'test-only',
            payload: { type: 'proposal', modelId: 'test-org/existing', version: 'v2', mode: 'raw' },
        });
    expect(r.status).toBe(201);
    expect(sendVerificationEmail).toHaveBeenCalledOnce();
    const db = await getDatabase();
    const pending = await db.get('SELECT * FROM submission_queue ORDER BY id DESC LIMIT 1');
    expect(JSON.parse(pending.payload).version).toBe('v2');
    expect((await request(app).get(`/api/verify?token=${pending.verification_token}`)).status).toBe(
        200,
    );
    const v2 = await request(app).get('/api/proposals?version=v2');
    expect(v2.body).toHaveLength(1);
    expect(v2.body[0].modelId).toBe('test-org/existing');
    expect(v2.body[0].version).toBe('v2');
    expect((await request(app).get('/api/proposals')).body).toHaveLength(1);
});
it('rejects duplicate requests in the same namespace and unsupported v2 chat enrollment', async () => {
    const send = (payload: any) =>
        request(app)
            .post('/api/submit')
            .send({
                email: 'test@example.com',
                captchaToken: 'test-only',
                payload: {
                    type: 'proposal',
                    modelId: 'test-org/existing',
                    version: 'v2',
                    ...payload,
                },
            });
    expect((await send({})).status).toBe(409);
    expect((await send({ mode: 'chat' })).status).toBe(400);
    expect((await send({ version: 'v99' })).status).toBe(400);
});
it('recovers verified v2 submissions without confusing a v1 proposal', async () => {
    const db = await getDatabase();
    await db.run(
        "INSERT INTO proposals(id,model_id,submitter_email) VALUES('recover-old','test-org/recover','test@example.com')",
    );
    await db.run('INSERT INTO submission_queue(email,payload,is_verified) VALUES(?,?,1)', [
        'test@example.com',
        JSON.stringify({
            type: 'proposal',
            modelId: 'test-org/recover',
            version: 'v2',
            mode: 'raw',
        }),
    ]);
    await closeDatabase();
    await initDatabase();
    const r = await request(app).get('/api/proposals?version=v2');
    expect(r.body.some((p: any) => p.modelId === 'test-org/recover')).toBe(true);
});
