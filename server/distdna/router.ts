import express, { Router } from 'express';
import { Worker } from 'node:worker_threads';
import { dataDirectory } from './repository.js';
import type { Identification } from './engine.js';

export function createDistDnaRouter(directory = dataDirectory(), timeoutMs = 60_000) {
    const router = Router();
    let worker: Worker | null = null;
    let ready = false;
    let catalog: any = null;
    let sequence = 0;
    let closed = false;
    const pending = new Map<
        number,
        { resolve: (x: Identification) => void; reject: (e: Error) => void; timer: NodeJS.Timeout }
    >();
    const fail = (reason: string) => {
        ready = false;
        for (const p of pending.values()) {
            clearTimeout(p.timer);
            p.reject(new Error(reason));
        }
        pending.clear();
    };
    function start() {
        if (closed || worker) return;
        const instance = new Worker(new URL('./worker.mjs', import.meta.url), {
            workerData: { directory },
        });
        worker = instance;
        instance.on('message', (m) => {
            if (m.ready) {
                catalog = m.catalog;
                ready = true;
                return;
            }
            const p = pending.get(m.id);
            if (!p) return;
            clearTimeout(p.timer);
            pending.delete(m.id);
            if (m.error) p.reject(Object.assign(new Error(m.error), { status: 400 }));
            else p.resolve(m.result);
        });
        instance.on('error', (e) => {
            console.error('DistDNA worker:', e.message);
            fail('DistDNA data could not be loaded.');
        });
        instance.on('exit', () => {
            if (worker === instance) {
                worker = null;
                fail('DistDNA scorer restarted. Please retry.');
            }
        });
    }
    router.get('/catalog', (_req, res) => {
        start();
        if (catalog) res.set('Cache-Control', 'no-store').json(catalog);
        else
            res.status(503).json({
                error: 'DistDNA gallery unavailable or starting. Please retry.',
            });
    });
    router.get('/health', (_req, res) => {
        start();
        res.status(ready ? 200 : 503).json({ ready, queued: pending.size });
    });
    router.post('/identify', express.json({ limit: '16mb' }), async (req, res) => {
        start();
        if (!ready || !worker)
            return res
                .status(503)
                .json({ error: 'DistDNA scorer is starting. Please retry shortly.' });
        if (pending.size >= 3)
            return res.status(429).json({ error: 'Scorer busy. Please retry shortly.' });
        const input = req.body;
        if (
            !input ||
            !['rbf', 'linear'].includes(input.method) ||
            typeof input.queryId !== 'string' ||
            input.queryId.length > 1000
        )
            return res.status(400).json({ error: 'Specify method (rbf or linear) and queryId.' });
        // Enrollment, paths and readout are fixed by the server, never provided by a client.
        if (Object.keys(input).some((k) => !['method', 'queryId', 'queries'].includes(k)))
            return res
                .status(400)
                .json({ error: 'Only method, queryId and an optional query bank are accepted.' });
        try {
            const id = ++sequence;
            const result = await new Promise<Identification>((resolve, reject) => {
                const timer = setTimeout(() => {
                    fail('Scoring timed out. Please use a smaller query bank.');
                    void worker?.terminate();
                }, timeoutMs);
                pending.set(id, { resolve, reject, timer });
                worker!.postMessage({ id, input });
            });
            res.set('Cache-Control', 'no-store').json({
                ...result,
                readout: 'nearest-reference',
                dataRevision: catalog.metadata.dataRevision,
            });
        } catch (error) {
            res.status((error as Error & { status?: number }).status || 503).json({
                error: (error as Error).message,
            });
        }
    });
    router.use(
        (err: any, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
            res.status(err.type === 'entity.too.large' ? 413 : 400).json({
                error:
                    err.type === 'entity.too.large'
                        ? 'Query upload exceeds 16 MB.'
                        : 'Invalid JSON query request.',
            });
        },
    );
    start();
    return {
        router,
        close: async () => {
            closed = true;
            fail('Scorer stopped.');
            await worker?.terminate();
            worker = null;
        },
    };
}
