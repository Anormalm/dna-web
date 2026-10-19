/** Production-build preview gateway for a temporary public HTTPS tunnel.
 * Run with: node --import tsx server/preview.ts
 * Keeps the collector and its filesystem private; serves only dist and explicit API routes.
 */
import express from 'express';
import { request as httpRequest } from 'node:http';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export function createPreviewApp(dist = resolve('dist'), backend = 'http://127.0.0.1:3002') {
    const destination = new URL(backend);
    if (destination.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(destination.hostname) || destination.username || destination.password || destination.search || destination.pathname !== '/') {
        throw new Error('Preview backend must be a loopback HTTP origin');
    }
    const app = express();
    app.disable('x-powered-by');
    app.use((_req, res, next) => { res.set({ 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY' }); next(); });
    const allowed = new Set([
        'GET /api/admin/session', 'GET /api/admin/github/start', 'GET /api/admin/github/callback',
        'GET /api/admin/snapshot', 'POST /api/admin/logout',
        'GET /api/v2/catalog', 'GET /api/v2/health', 'POST /api/v2/identify',
    ]);
    app.use('/api', (req, res) => {
        res.set('Cache-Control', 'no-store');
        const path = '/api' + req.path;
        if (path === '/api/admin/events') return res.status(503).json({ error: 'This preview uses authenticated snapshot polling.' });
        if (!allowed.has(`${req.method} ${path}`)) return res.status(403).json({ error: 'This endpoint is unavailable in the hosted preview. Community submissions remain disabled.' });
        // Do not accept forwarded identity or client IP headers from the tunnel.
        // OAuth authorization and CSRF are checked by the existing backend.
        const headers: Record<string, string> = {};
        for (const key of ['accept', 'content-type', 'content-length', 'cookie', 'origin', 'user-agent', 'x-csrf-token']) {
            const value = req.headers[key]; if (typeof value === 'string') headers[key] = value;
        }
        const upstream = httpRequest(destination, { path: req.originalUrl, method: req.method, headers }, response => {
            const responseHeaders = { ...response.headers };
            for (const name of ['connection', 'keep-alive', 'transfer-encoding', 'access-control-allow-origin', 'access-control-allow-credentials']) delete responseHeaders[name];
            res.writeHead(response.statusCode || 502, responseHeaders); response.pipe(res);
        });
        upstream.setTimeout(70_000, () => upstream.destroy(new Error('Preview request timed out')));
        upstream.on('error', () => { if (!res.headersSent) res.status(502).json({ error: 'Preview backend unavailable. Please retry.' }); else res.destroy(); });
        res.on('close', () => upstream.destroy());
        req.pipe(upstream);
    });
    app.use(express.static(dist, { dotfiles: 'deny', index: false }));
    app.get('*', (_req, res) => { res.set('Cache-Control', 'no-store'); res.sendFile(resolve(dist, 'index.html')); });
    return app;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    const port = Number(process.env.PUBLIC_PREVIEW_PORT || 5180);
    createPreviewApp(resolve(process.env.PUBLIC_PREVIEW_DIST || 'dist'), process.env.PUBLIC_PREVIEW_BACKEND || 'http://127.0.0.1:3002')
        .listen(port, '127.0.0.1', () => console.log(`Hosted preview gateway on 127.0.0.1:${port}`));
}
