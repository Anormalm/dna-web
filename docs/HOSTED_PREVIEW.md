# Hosted HTTPS preview

The hosted site serves the existing production build. An outbound collector
agent connects the hosted API to the worker machine over HTTPS, so visitors do
not need its SSH route or VPN. Model weights, response banks, SQLite databases,
and GitHub OAuth credentials stay on the worker machine.

## Architecture

1. `hosting/sites/bridge.js` serves the built frontend and queues allowed API
   requests in the site's private R2 bucket.
2. `hosting/sites/collector-agent.mjs` polls over HTTPS, claims requests, forwards
   them to the loopback preview gateway, and returns the actual responses.
3. `server/preview.ts` serves only `dist/`, GitHub admin routes, and DistDNA
   catalog/identification APIs. The existing backend enforces the GitHub ID
   allowlist, session cookies, OAuth state, and mutation CSRF checks.

The hosted preview excludes legacy community submissions and proposal records.
Admin updates use the authenticated snapshot API every five seconds. The same
frontend falls back to polling when an event stream fails or its initial update
arrives after eight seconds. Requests stop on logout/unmount; expired sessions
return to sign-in. Connection failures show delayed/reconnecting status.

## Build and deployment

Build the desired frontend branch with `npm run build`, then run:

```sh
python scripts/build_sites_preview.py dist /path/to/site-checkout
```

This embeds the compiled assets in `worker/index.js`, omitting hidden files and
source maps. Use the native Sites project workflow to deploy that checkout with
an R2 binding named `BUCKET`. Keep the site private to its owner. Configure
`DNA_BRIDGE_KEY` as a server-side runtime secret equal to this private site's
service access token; never put it in frontend assets, git, or command arguments.

Start the existing backend with its private admin environment on port 3002 and
run `node --import tsx server/preview.ts`. The gateway listens on loopback port
5180. Optional gateway settings: `PUBLIC_PREVIEW_PORT`, `PUBLIC_PREVIEW_DIST`,
and `PUBLIC_PREVIEW_BACKEND` (loopback HTTP only). The current collector agent
always targets port 5180.

Run `node hosting/sites/collector-agent.mjs --daemon` and supply a single JSON
line on its hidden stdin containing `origin` (the HTTPS site origin) and `token`
(the private site's service access token). Use a secure credential mechanism to
supply it; avoid shell command history. Credentials remain in process memory.
`DNA_BRIDGE_LOG` selects the daemon log path; it defaults to
`/tmp/distdna-collector-bridge.log`. Record the returned PID for shutdown.

Set `DNA_ADMIN_ORIGIN` to the hosted HTTPS origin and `DNA_ADMIN_ALLOW_HTTP=false`,
then restart the backend. In the existing GitHub OAuth App, set the homepage to
that origin and the callback to `https://YOUR-SITE/api/admin/github/callback`.
Visitors first authenticate to the private Site, then sign in with GitHub for
admin access. No GitHub client secret or access token enters the hosted frontend.

## Operational scope

The hostname is stable, but live APIs require the backend, gateway, and bridge
agent to remain running. The agent reconnects after transient network failures;
after a machine restart, an operator must restart it and supply its token again.
Stop the agent to disconnect the collector, or disable the Site to remove access.
Opening the preview starts no generation jobs and changes no collection budget.

Requests carry only allowlisted headers. The private bucket temporarily holds
API request/response data, including OAuth callback codes and session cookies;
normal requests are deleted after completion. Abandoned requests expire after
60 seconds and are removed on a subsequent agent poll. Heartbeats older than
30 seconds produce an explicit offline response. The bridge waits up to 47.5
seconds for a result and limits uploads to 2 MB and backend responses to 3 MB.
It handles up to three requests per poll; this is a single-admin preview, not a
high-throughput production API.

## Validation

Run `npm run test:hosted-preview`, `npm run test:admin`,
`npm run typecheck:admin`, and `npm run typecheck:distdna`.
Bridge checks cover private routes, offline behavior, single-claim delivery,
OAuth redirects and multiple cookies, and request/result cleanup. Browser checks
should exercise failed event streams, repeated polling updates, logout, and
session expiry. After deployment, verify the catalog and a saved identification
query against the local backend, and complete a real GitHub sign-in.
