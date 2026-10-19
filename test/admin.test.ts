import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import express from 'express';
import request from 'supertest';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm, writeFile, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import sqlite3 from 'sqlite3';
import { open, type Database } from 'sqlite';
import { adminConfig, validMutation, type AdminConfig } from '../server/admin/auth';
import { createAdminRouter } from '../server/admin/router';

let root: string, config: AdminConfig, queue: Database;
let api: ReturnType<typeof createAdminRouter>, app: ReturnType<typeof express>;
let identity: number;
let fetcher: ReturnType<typeof vi.fn>;
const cookies = (res: any) => (res.headers['set-cookie'] as string[]).map(s => s.split(';')[0]).join('; ');

beforeEach(async () => {
    root = await mkdtemp(join(tmpdir(), 'dna-admin-'));
    config = { origin: 'https://dna.example', clientId: 'test-client', clientSecret: 'test-secret', githubIds: [7], stateDir: join(root, 'auth'), runDir: join(root, 'run') };
    await mkdir(config.runDir!);
    queue = await open({ filename: join(config.runDir!, 'queue.sqlite'), driver: sqlite3.Database });
    await queue.exec('CREATE TABLE jobs (key TEXT,model TEXT,mode TEXT,state TEXT,item TEXT,run TEXT,idx INTEGER,pid INTEGER,updated REAL)');
    await queue.run('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)', 'raw:demo', 'org/demo', 'raw', 'running', JSON.stringify({ parameters: 1e9, gpuCount: 1, tag: 'local', secret: 'private-worker-token' }), join(root, 'worker'), 0, process.pid, Date.now() / 1000);
    await mkdir(join(root, 'worker/models/000'), { recursive: true });
    await writeFile(join(root, 'worker/models/000/status.json'), JSON.stringify({ records: 40, elapsedSeconds: 8, updated: Date.now() / 1000, error: 'private-response' }));
    await writeFile(join(config.runDir!, 'chat-status.json'), JSON.stringify({ spendingCeilingUsd: 75 }));
    const spend = await open({ filename: join(config.runDir!, 'chat-spend.sqlite'), driver: sqlite3.Database });
    await spend.exec('CREATE TABLE charges (actual REAL,reserved REAL,created REAL); INSERT INTO charges VALUES (2,3,1),(NULL,0.5,2)');
    await spend.close();
    identity = 7;
    fetcher = vi.fn(async (url: any, _init?: RequestInit) => new Response(JSON.stringify(String(url).endsWith('/access_token') ? { access_token: 'temporary-github-token' } : { id: identity, login: 'test-admin' }), { status: 200 }));
    api = createAdminRouter(config, fetcher as typeof fetch, 100);
    app = express(); app.use(express.json()); app.use('/api/admin', api.router);
});
afterEach(async () => { await api.close(); await queue.close(); await rm(root, { recursive: true, force: true }); });

async function login() {
    const start = await request(app).get('/api/admin/github/start').expect(302);
    const state = new URL(start.headers.location).searchParams.get('state');
    const callback = await request(app).get('/api/admin/github/callback').query({ state, code: 'test-code' }).set('Cookie', cookies(start)).expect(303);
    expect(callback.headers.location).toBe(config.origin + '/admin');
    const cookie = cookies(callback);
    const session = await request(app).get('/api/admin/session').set('Cookie', cookie).expect(200);
    return { cookie, session: session.body };
}

describe('GitHub administrator boundary', () => {
    it('fails closed without configuration and rejects anonymous telemetry', async () => {
        const disabled = createAdminRouter(null);
        const other = express().use('/api/admin', disabled.router);
        expect((await request(other).get('/api/admin/session')).body).toEqual({ configured: false, authenticated: false });
        await request(other).get('/api/admin/github/start').expect(503);
        await disabled.close();
        await request(app).get('/api/admin/snapshot').expect(401);
        await request(app).get('/api/admin/events').expect(401);
        expect(adminConfig({})).toBeNull();
        const env = { GITHUB_CLIENT_ID: 'a', GITHUB_CLIENT_SECRET: 'b', DNA_ADMIN_GITHUB_IDS: '7', DNA_ADMIN_ORIGIN: 'http://private.test' };
        expect(() => adminConfig(env)).toThrow('HTTPS');
        expect(adminConfig({ ...env, DNA_ADMIN_ALLOW_HTTP: 'true' })?.githubIds).toEqual([7]);
        expect(() => adminConfig({ ...env, DNA_ADMIN_ORIGIN: 'https://site.test/admin' })).toThrow('exact');
        expect(() => adminConfig({ ...env, DNA_ADMIN_ORIGIN: 'https://site.test', DNA_ADMIN_GITHUB_IDS: 'test-admin' })).toThrow('numeric');
    });

    it('binds OAuth to this browser and verifies PKCE, replay protection and private cookies', async () => {
        const start = await request(app).get('/api/admin/github/start').expect(302);
        const url = new URL(start.headers.location), state = url.searchParams.get('state');
        expect(url.origin + url.pathname).toBe('https://github.com/login/oauth/authorize');
        expect(url.searchParams.get('scope')).toBe('');
        expect(url.searchParams.get('redirect_uri')).toBe(config.origin + '/api/admin/github/callback');
        expect(url.searchParams.get('code_challenge_method')).toBe('S256');
        const failed = await request(app).get('/api/admin/github/callback').query({ state, code: 'test-code' }).set('Cookie', 'dna_admin_oauth=wrong');
        expect(failed.headers.location).toContain('signin=failed');
        expect(fetcher).not.toHaveBeenCalled();
        const callback = await request(app).get('/api/admin/github/callback').query({ state, code: 'test-code' }).set('Cookie', cookies(start));
        expect(callback.headers.location).toBe(config.origin + '/admin');
        const exchange = JSON.parse(fetcher.mock.calls[0][1].body);
        expect(createHash('sha256').update(exchange.code_verifier).digest('base64url')).toBe(url.searchParams.get('code_challenge'));
        const sessionCookie = (callback.headers['set-cookie'] as unknown as string[]).find((s: string) => s.startsWith('dna_admin_session='));
        expect(sessionCookie).toContain('HttpOnly'); expect(sessionCookie).toContain('Secure'); expect(sessionCookie).toContain('SameSite=Lax');
        const replay = await request(app).get('/api/admin/github/callback').query({ state, code: 'test-code' }).set('Cookie', cookies(start));
        expect(replay.headers.location).toContain('signin=failed');
        expect(fetcher).toHaveBeenCalledTimes(2);
        const bytes = await readFile(join(config.stateDir, 'admin.sqlite'));
        expect(bytes.includes(Buffer.from('temporary-github-token'))).toBe(false);
        expect(bytes.includes(Buffer.from('test-secret'))).toBe(false);
        expect((await stat(join(config.stateDir, 'admin.sqlite'))).mode & 0o777).toBe(0o600);
    });

    it('rejects another GitHub account even with the allowed username', async () => {
        identity = 8;
        const start = await request(app).get('/api/admin/github/start');
        const result = await request(app).get('/api/admin/github/callback').query({ state: new URL(start.headers.location).searchParams.get('state'), code: 'test-code' }).set('Cookie', cookies(start));
        expect(result.headers.location).toContain('signin=failed');
        expect((result.headers['set-cookie'] as unknown as string[]).some((s: string) => s.startsWith('dna_admin_session='))).toBe(false);
    });

    it('requires exact origin and CSRF for logout and revokes the session', async () => {
        const auth = await login();
        expect(auth.session.user.id).toBe(7);
        await request(app).post('/api/admin/logout').set('Cookie', auth.cookie).expect(403);
        await request(app).post('/api/admin/logout').set('Cookie', auth.cookie).set('Origin', 'https://other.example').set('X-CSRF-Token', auth.session.csrf).expect(403);
        await request(app).post('/api/admin/logout').set('Cookie', auth.cookie).set('Origin', config.origin).set('X-CSRF-Token', auth.session.csrf).expect(200);
        await request(app).get('/api/admin/snapshot').set('Cookie', auth.cookie).expect(401);
        const fake = { get: (h: string) => h === 'Origin' ? config.origin : 'é'.repeat(43) };
        expect(validMutation(fake as any, config, { csrf: 'a'.repeat(43) } as any)).toBe(false);
    });

    it('protects every collection mutation with login and CSRF', async () => {
        const auth = await login();
        for (const route of ['estimate','jobs','job-action','budget','collection-action']) {
            await request(app).post('/api/admin/'+route).send({}).expect(401);
            await request(app).post('/api/admin/'+route).set('Cookie',auth.cookie).send({}).expect(403);
        }
        await request(app).get('/api/admin/controls').expect(401);
    });

    it('rejects expired sessions and expired OAuth requests', async () => {
        const auth = await login();
        const start = await request(app).get('/api/admin/github/start');
        const db = await open({ filename: join(config.stateDir, 'admin.sqlite'), driver: sqlite3.Database });
        await db.exec('UPDATE admin_sessions SET expires_at=0; UPDATE oauth_states SET expires_at=0'); await db.close();
        await request(app).get('/api/admin/snapshot').set('Cookie', auth.cookie).expect(401);
        const expired = await request(app).get('/api/admin/github/callback').query({ state: new URL(start.headers.location).searchParams.get('state'), code: 'code' }).set('Cookie', cookies(start));
        expect(expired.headers.location).toContain('signin=failed');
    });
});

describe('live collection telemetry', () => {
    it('reads progress and distinguishes reported charges from unresolved reservations without exposing private worker data', async () => {
        const auth = await login();
        const result = await request(app).get('/api/admin/snapshot').set('Cookie', auth.cookie).expect(200);
        expect(result.body.models[0]).toMatchObject({ model: 'org/demo', records: 40, expected: 800, workerAlive: true, parameters: 1e9 });
        expect(result.body.budget).toMatchObject({ reportedUsd: 2, reservedUsd: 0.5, operationalCapUsd: 75 });
        for (const secret of ['private-worker-token', 'private-response', root, 'test-secret', 'temporary-github-token']) expect(result.text).not.toContain(secret);
        expect(result.headers['cache-control']).toBe('no-store');
        expect(await queue.get('SELECT COUNT(*) AS n FROM jobs')).toEqual({ n: 1 });
    });

    it('streams actual queue changes, resumes with a full snapshot, and disconnects revoked sessions', async () => {
        const auth = await login();
        const server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
        const port = (server.address() as any).port;
        const abort = new AbortController();
        const timeout = setTimeout(() => abort.abort(), 8000);
        try {
            const response = await fetch(`http://127.0.0.1:${port}/api/admin/events`, { headers: { Cookie: auth.cookie }, signal: abort.signal });
            expect(response.headers.get('content-type')).toContain('text/event-stream');
            const reader = response.body!.getReader(); let buffer = '';
            async function until(predicate: (s: string) => boolean) {
                while (!predicate(buffer)) { const chunk = await reader.read(); if (chunk.done) throw new Error('Stream ended early'); buffer += new TextDecoder().decode(chunk.value); }
            }
            await until(s => s.includes('"records":40'));
            await queue.run("UPDATE jobs SET state='complete'");
            await until(s => s.includes('"records":800'));
            await request(app).post('/api/admin/logout').set('Cookie', auth.cookie).set('Origin', config.origin).set('X-CSRF-Token', auth.session.csrf).expect(200);
            await until(s => s.includes('event: expired'));
            abort.abort();
            const again = await login();
            const latest = await request(app).get('/api/admin/snapshot').set('Cookie', again.cookie).expect(200);
            expect(latest.body.models[0].records).toBe(800);
        } finally { clearTimeout(timeout); abort.abort(); server.closeAllConnections(); await new Promise<void>(r => server.close(() => r())); }
    }, 10_000);
});
