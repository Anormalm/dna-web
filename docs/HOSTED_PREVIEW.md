# Temporary HTTPS preview

The collector, SQLite databases and large fingerprint bank remain on the worker
machine. A tunnel makes the built website and selected APIs accessible through
HTTPS, without requiring the visitor's SSH route or VPN.

Build with `npm run build`, start the existing backend on port 3002 with the
private admin environment, then run `node --import tsx server/preview.ts`.
This gateway binds to loopback port 5180 and serves only `dist/`, the GitHub admin
routes and DistDNA catalog/identification APIs. Community submissions and the
legacy proposals database are excluded from this preview. The regular local
preview is unchanged. Optional gateway settings: `PUBLIC_PREVIEW_PORT`,
`PUBLIC_PREVIEW_DIST`, `PUBLIC_PREVIEW_BACKEND` (loopback HTTP only).

For a Cloudflare Quick Tunnel:

```sh
cloudflared tunnel --url http://127.0.0.1:5180 --protocol http2 --no-autoupdate
```

Set `DNA_ADMIN_ORIGIN` to the returned HTTPS origin and restart the backend. Update
the GitHub OAuth App homepage to that origin and callback to
`https://YOUR-PREVIEW-HOST/api/admin/github/callback`. Keep client credentials
server-side. HTTPS enables Secure session cookies; the existing GitHub ID
allowlist and CSRF checks still apply.

Quick Tunnels have a temporary hostname, no uptime guarantee, and no SSE support:
[Cloudflare documentation](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
The gateway rejects the event-stream route and the admin page falls back to the
same authenticated snapshot API every five seconds. It also falls back if a proxy
buffers the initial stream for eight seconds. Requests stop on logout/unmount;
expired sessions return to sign-in. Failures show delayed/reconnecting status.

This is a preview, not a permanent Vercel deployment. A durable deployment needs
a stable HTTPS origin and a persistent backend or database/collector bridge.
Stop the tunnel to remove public access. No weights, raw responses or credentials
are packaged in the frontend, and opening the preview starts no generation jobs.

Validation: `npx vitest run test/preview.test.ts test/admin.test.ts`,
`npm run typecheck:admin`, and `npm run typecheck:distdna`. Browser checks should
exercise an SSE failure, multiple polling updates, logout and session expiry.
