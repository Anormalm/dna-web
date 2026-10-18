import { beforeAll, afterAll, describe, it, expect } from 'vitest';
import express from 'express';
import request from 'supertest';
import { readFileSync } from 'node:fs';
import { createDistDnaRouter } from '../server/distdna/router';
import { defaultDataDir } from '../server/distdna/repository';
const api = createDistDnaRouter(defaultDataDir, 15000);
const app = express();
app.use('/api/v2', api.router);
const read = (n: string) => JSON.parse(readFileSync(`${defaultDataDir}/${n}`, 'utf8'));
beforeAll(async () => {
    for (let i = 0; i < 100; i++) {
        const r = await request(app).get('/api/v2/health');
        if (r.status === 200) return;
        await new Promise((r) => setTimeout(r, 50));
    }
    throw new Error('Worker failed to start');
}, 10000);
afterAll(() => api.close());
describe('server DistDNA scoring', () => {
    it('serves versioned catalog metadata without sending the embedding bank', async () => {
        const r = await request(app).get('/api/v2/catalog');
        expect(r.status).toBe(200);
        expect(r.body.metadata.version).toBe('v2');
        expect(r.body.models).toHaveLength(4);
        expect(r.body.models.every((m: any) => m.signature === null && Number.isFinite(m.x))).toBe(
            true,
        );
        expect(JSON.stringify(r.body)).not.toContain('samples');
    });
    for (const method of ['rbf', 'linear'])
        it(`computes ${method} on the worker and matches the independent NumPy oracle`, async () => {
            const r = await request(app)
                .post('/api/v2/identify')
                .send({ method, queryId: 'heldout-01' });
            expect(r.status).toBe(200);
            expect(r.body.readout).toBe('nearest-reference');
            expect(r.body.candidates).toHaveLength(4);
            const golden = read('example-distances.json').find(
                (x: any) => x.method === method && x.queryId === 'heldout-01',
            );
            for (const candidate of r.body.candidates) {
                const best = golden.distances
                    .filter((x: any) => x.modelId === candidate.modelId)
                    .sort((a: any, b: any) => a.distance - b.distance)[0];
                expect(candidate.distance).toBeCloseTo(best.distance, 10);
                expect(candidate.referenceId).toBe(best.referenceId);
            }
            expect(r.body).not.toHaveProperty('confidence');
        });
    it('accepts compatible uploaded queries and rejects changed encoder metadata', async () => {
        const queries = read('example-queries.json');
        queries.queries = queries.queries.slice(0, 1);
        let r = await request(app)
            .post('/api/v2/identify')
            .send({ method: 'rbf', queryId: 'heldout-01', queries });
        expect(r.status).toBe(200);
        queries.protocol.encoder = 'different';
        r = await request(app)
            .post('/api/v2/identify')
            .send({ method: 'rbf', queryId: 'heldout-01', queries });
        expect(r.status).toBe(400);
        expect(r.body.error).toContain('encoder');
    });
    it('rejects unknown queries, malformed bodies and user-controlled enrollment', async () => {
        for (const body of [
            { method: 'svm', queryId: 'heldout-01' },
            { method: 'rbf', queryId: 'missing' },
            { method: 'rbf', queryId: 'heldout-01', gallery: {} },
            { method: 'rbf', queryId: 'heldout-01', path: '/etc/passwd' },
        ]) {
            const r = await request(app).post('/api/v2/identify').send(body);
            expect(r.status).toBe(400);
        }
        const bad = await request(app)
            .post('/api/v2/identify')
            .set('Content-Type', 'application/json')
            .send('{bad');
        expect(bad.status).toBe(400);
    });
    it('rejects enrollment/query ID reuse across the complete gallery', async () => {
        const queries = read('example-queries.json');
        queries.queries = queries.queries.slice(0, 1);
        queries.queries[0].id = read('example-gallery.json').references.at(-1).id;
        const r = await request(app)
            .post('/api/v2/identify')
            .send({ method: 'linear', queryId: queries.queries[0].id, queries });
        expect(r.status).toBe(400);
        expect(r.body.error).toContain('overlap');
    });
    it('bounds upload size before sending work to the scorer', async () => {
        const r = await request(app)
            .post('/api/v2/identify')
            .send({ method: 'rbf', queryId: 'heldout-01', queries: 'x'.repeat(16 * 1024 * 1024) });
        expect(r.status).toBe(413);
    });
});
