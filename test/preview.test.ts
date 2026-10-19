import { afterAll, beforeAll, expect, it } from 'vitest';
import express from 'express';
import request from 'supertest';
import { once } from 'node:events';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Server } from 'node:http';
import { createPreviewApp } from '../server/preview';

let server: Server, directory: string, app: ReturnType<typeof createPreviewApp>;
beforeAll(async () => {
    directory = await mkdtemp(join(tmpdir(), 'dna-preview-'));
    await writeFile(join(directory, 'index.html'), '<h1>DNA</h1>');
    await writeFile(join(directory, '.env'), 'private-secret');
    const backend = express().use(express.json()).all('*', (req, res) => {
        res.cookie('dna_admin_session', 'example', { httpOnly: true, secure: true, path: '/api/admin', sameSite: 'lax' });
        if (req.path.endsWith('/callback')) return res.redirect(303, 'https://preview.example/admin');
        res.json({ path: req.path, query: req.query, cookie: req.headers.cookie, origin: req.headers.origin, csrf: req.headers['x-csrf-token'], forwarded: req.headers['x-forwarded-for'], body: req.body });
    });
    server = backend.listen(0, '127.0.0.1'); await once(server, 'listening');
    app = createPreviewApp(directory, `http://127.0.0.1:${(server.address() as any).port}`);
});
afterAll(async () => { server.closeAllConnections(); await new Promise<void>(r => server.close(() => r())); await rm(directory, { recursive: true, force: true }); });

it('serves the built application and excludes private files and unsupported APIs', async () => {
    expect((await request(app).get('/admin')).text).toContain('<h1>DNA</h1>');
    expect((await request(app).get('/.env')).text).not.toContain('private-secret');
    for (const path of ['/api/submit', '/api/proposals', '/api/verify', '/api/admin/arbitrary']) await request(app).get(path).expect(403);
    await request(app).post('/api/submit').send({ model: 'do-not-enroll' }).expect(403);
    await request(app).get('/api/admin/events').expect(503);
    expect(() => createPreviewApp(directory, 'https://external.example')).toThrow('loopback');
});
it('preserves OAuth redirects, secure cookies, CSRF and exact request bodies through the proxy', async () => {
    const response = await request(app).post('/api/admin/logout').set('Cookie', 'dna_admin_session=example').set('Origin', 'https://preview.example').set('X-CSRF-Token', 'csrf').set('X-Forwarded-For', 'spoofed').send({ value: 1 }).expect(200);
    expect(response.body).toMatchObject({ cookie: 'dna_admin_session=example', origin: 'https://preview.example', csrf: 'csrf', body: { value: 1 } });
    expect(response.body.forwarded).toBeUndefined();
    expect(response.headers['set-cookie'][0]).toContain('Secure');
    const redirect = await request(app).get('/api/admin/github/callback?state=example&code=example').expect(303);
    expect(redirect.headers.location).toBe('https://preview.example/admin');
    const catalog = await request(app).get('/api/v2/catalog').expect(200);
    expect(catalog.body.path).toBe('/api/v2/catalog');
    const score = await request(app).post('/api/v2/identify').send({ method: 'rbf', queryId: 'heldout-01' }).expect(200);
    expect(score.body.body).toEqual({ method: 'rbf', queryId: 'heldout-01' });
});
