# GitHub administrator access and live status

Visit `/admin` to sign in and view the enrollment queue. This page reports model
types, response counts, worker status, reported API charges and unresolved cost
reservations. It refreshes through server-sent events every 2.5 seconds and shows
when updates are delayed. Public navigation remains unchanged.

This first admin PR is read-only for enrollment and spending. Budget forecasts,
saved configuration and model discovery/job controls belong to subsequent PRs.
The displayed API controller limit comes from the running collector; opening the
dashboard never raises it. Local GPU costs are separate from API charges.

## Configuration

1. Register a [GitHub OAuth App](https://github.com/settings/applications/new).
   Set its homepage to your website origin and authorization callback to
   `https://YOUR-HOST/api/admin/github/callback`.
2. Copy `admin.env.example` to a private file outside the checkout. Set the client
   ID and secret, exact website origin, and comma-separated **numeric GitHub user
   IDs** allowed to administer the site. GitHub usernames alone are not accepted.
   An ID is available at `https://api.github.com/users/YOUR-USERNAME`.
3. Set `DNA_ADMIN_STATE_DIR` to a private persistent directory for sessions and
   audit records. Set `DNA_ADMIN_RUN_DIR` to an existing full-catalog enrollment
   run containing `queue.sqlite` and, for chat runs, `chat-spend.sqlite` and
   `chat-status.json`. The dashboard opens collection databases read-only.
4. Load the private file into the **backend** environment, for example with
   `node --env-file=/private/admin.env --import tsx server/index.ts` on Node 22.
   Never prefix these settings with `VITE_`, put them in frontend configuration,
   or commit the private file. Restrict its permissions to the server account.

An HTTPS origin is required. Private local previews can explicitly opt into HTTP
using `DNA_ADMIN_ALLOW_HTTP=true`; the OAuth App callback must match that origin.
Use a separate OAuth App for a preview with a different callback.

## Proxy and sessions

Serve frontend and `/api/admin` under the same origin. Forward the path unchanged,
preserve cookies and the request `Origin`, and disable proxy buffering for
`/api/admin/events`. Allow an idle timeout longer than the 2.5-second heartbeat.
Vite's development proxy can forward `/api` to the backend for local testing.

OAuth requests use PKCE and a single-use state bound to an HttpOnly browser
cookie. Sign-in requests no repository permissions. GitHub access tokens are
used only to fetch the account identity and are not persisted. Sessions expire
after eight hours, are stored as hashes in a private SQLite database, and are
revoked by logout. Mutations require the configured origin and a session CSRF
token. Every stream rechecks authorization; a revoked or expired session closes
the stream. Removing an account ID from configuration takes effect on restart.

The server fails closed when OAuth settings are missing. It caps live streams at
eight, shares a short telemetry cache, and disconnects slow consumers. Unavailable
telemetry displays an error rather than fabricated zero usage. Provider error
bodies, worker paths, credentials and raw response records are excluded.

## Validation

Run `npm run test:admin`, `npm run typecheck:admin`,
`npm run typecheck:distdna`, and `npm run build`. The scoped admin check covers
both the server and page; the base branch has unrelated legacy frontend
type errors addressed in the existing public-UI PR. Tests use an injected fake GitHub
response, temporary SQLite files, and a real HTTP event stream. There is no
production test-login endpoint. A real OAuth round-trip requires the configured
administrator to authorize the app in their own browser.
