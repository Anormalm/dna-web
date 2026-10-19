import express from 'express';
import rateLimit from 'express-rate-limit';
import { AdminAuth, adminConfig, cookie, setCookie, sessionCookie, validMutation, type AdminConfig } from './auth.js';
import { Telemetry } from './telemetry.js';
import { Operations, AdminOperationError } from './operations.js';

export function createAdminRouter(config: AdminConfig | null = adminConfig(), fetcher: typeof fetch = fetch, intervalMs = 2500) {
    const router = express.Router();
    const auth = config ? new AdminAuth(config, fetcher) : null;
    const initialized = auth?.init();
    void initialized?.catch(() => {}); // Requests report initialization failures without leaking details.
    const telemetry = new Telemetry(config?.runDir);
    const operations = new Operations(config);
    const connections = new Map<express.Response, NodeJS.Timeout>();
    const wrap = (fn: (req: express.Request, res: express.Response) => Promise<any>) =>
        (req: express.Request, res: express.Response, next: express.NextFunction) => { void fn(req, res).catch(next); };
    router.use((_req, res, next) => { res.set({ 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff' }); next(); });
    router.get('/session', wrap(async (req, res) => {
        await initialized;
        const session = await auth?.session(cookie(req, sessionCookie));
        res.json(session ? { configured: true, authenticated: true, user: { id: session.user_id, login: session.login }, csrf: session.csrf, expiresAt: session.expires_at } : { configured: !!auth, authenticated: false });
    }));
    router.get('/github/start', rateLimit({ windowMs: 600_000, max: 20, standardHeaders: true, legacyHeaders: false }), wrap(async (_req, res) => {
        if (!auth || !config) return res.status(503).json({ error: 'GitHub administrator login is not configured.' });
        await initialized;
        const start = await auth.begin();
        setCookie(res, config, 'dna_admin_oauth', start.browser, 600_000);
        res.redirect(start.url);
    }));
    router.get('/github/callback', wrap(async (req, res) => {
        if (!auth || !config) return res.status(503).json({ error: 'Administrator login is unavailable.' });
        await initialized;
        setCookie(res, config, 'dna_admin_oauth', '', 0);
        try {
            const bearer = await auth.finish(String(req.query.state || ''), cookie(req, 'dna_admin_oauth'), String(req.query.code || ''));
            setCookie(res, config, sessionCookie, bearer, 8 * 3600_000);
            return res.redirect(303, config.origin + '/admin');
        } catch { return res.redirect(303, config.origin + '/admin?signin=failed'); }
    }));
    router.use((req, res, next) => {
        void (async () => {
            await initialized;
            const session = await auth?.session(cookie(req, sessionCookie));
            if (!session || !config) return res.status(401).json({ error: 'Administrator sign-in required.' });
            res.locals.admin = session;
            if (!['GET', 'HEAD'].includes(req.method) && !validMutation(req, config, session)) return res.status(403).json({ error: 'Invalid request origin or CSRF token.' });
            next();
        })().catch(next);
    });
    router.post('/logout', wrap(async (req, res) => {
        await auth!.logout(cookie(req, sessionCookie), res.locals.admin.user_id);
        setCookie(res, config!, sessionCookie, '', 0);
        res.json({ ok: true });
    }));
    router.post('/estimate', wrap(async (req, res) => { res.json(await operations.call('estimate', req.body, res.locals.admin.user_id)); }));
    router.get('/snapshot', wrap(async (_req, res) => { res.json(await telemetry.snapshot()); }));
    router.get('/events', wrap(async (req, res) => {
        if (connections.size >= 8) return res.status(429).json({ error: 'Too many live admin connections.' });
        res.set({ 'Content-Type': 'text/event-stream', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no' });
        res.flushHeaders(); res.write('retry: 3000\n\n');
        let sending = false;
        const send = async () => {
            if (sending || res.destroyed) return;
            sending = true;
            try {
                if (!await auth!.session(cookie(req, sessionCookie))) { res.write('event: expired\ndata: {}\n\n'); res.end(); return; }
                const snapshot = await telemetry.snapshot();
                // Full snapshots make reconnection independent of an in-memory event history.
                if (res.writableLength > 2 * 1024 * 1024) { res.end(); return; }
                if (!res.destroyed) res.write(`event: snapshot\ndata: ${JSON.stringify(snapshot)}\n\n`);
            } catch { res.end(); } finally { sending = false; }
        };
        const timer = setInterval(() => { void send(); }, intervalMs);
        connections.set(res, timer);
        res.on('close', () => { clearInterval(timer); connections.delete(res); });
        await send();
    }));
    router.use((_error: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
        if (!res.headersSent) res.status(_error instanceof AdminOperationError ? _error.status : 503).json({ error: _error instanceof AdminOperationError ? _error.message : 'Administrator service is temporarily unavailable.' });
    });
    return { router, close: async () => { for (const [res, timer] of connections) { clearInterval(timer); res.end(); } await initialized; await telemetry.close(); await auth?.close(); } };
}
