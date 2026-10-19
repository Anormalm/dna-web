import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import { mkdir, chmod } from 'node:fs/promises';
import { join } from 'node:path';
import sqlite3 from 'sqlite3';
import { open, type Database } from 'sqlite';
import type { Request, Response } from 'express';

export type AdminConfig = {
    origin: string;
    clientId: string;
    clientSecret: string;
    githubIds: number[];
    stateDir: string;
    runDir?: string;
};
export type Session = { user_id: number; login: string; csrf: string; expires_at: number };
const hash = (s: string) => createHash('sha256').update(s).digest('hex');
const token = () => randomBytes(32).toString('base64url');
export const sessionCookie = 'dna_admin_session';

export function adminConfig(env = process.env): AdminConfig | null {
    if (!env.GITHUB_CLIENT_ID || !env.GITHUB_CLIENT_SECRET || !env.DNA_ADMIN_GITHUB_IDS || !env.DNA_ADMIN_ORIGIN) return null;
    const url = new URL(env.DNA_ADMIN_ORIGIN);
    if (url.origin !== env.DNA_ADMIN_ORIGIN || url.username || url.password ||
        !['http:', 'https:'].includes(url.protocol)) throw new Error('DNA_ADMIN_ORIGIN must be an exact HTTP(S) origin');
    if (url.protocol !== 'https:' && env.DNA_ADMIN_ALLOW_HTTP !== 'true') throw new Error('Admin login requires HTTPS; private development HTTP needs explicit opt-in');
    const ids = env.DNA_ADMIN_GITHUB_IDS.split(',').map(Number);
    if (!ids.length || ids.some(x => !Number.isSafeInteger(x) || x <= 0)) throw new Error('Configure numeric GitHub administrator IDs');
    return { origin: url.origin, clientId: env.GITHUB_CLIENT_ID, clientSecret: env.GITHUB_CLIENT_SECRET,
        githubIds: ids, stateDir: env.DNA_ADMIN_STATE_DIR || './server/data/admin', runDir: env.DNA_ADMIN_RUN_DIR };
}

export function cookie(req: Request, name: string) {
    const value = (req.headers.cookie || '').split(';').map(x => x.trim()).find(x => x.startsWith(name + '='));
    return value?.slice(name.length + 1) || '';
}
export function setCookie(res: Response, config: AdminConfig, name: string, value: string, age: number) {
    res.cookie(name, value, { httpOnly: true, secure: config.origin.startsWith('https:'), sameSite: 'lax', path: '/api/admin', maxAge: age });
}
export function validMutation(req: Request, config: AdminConfig, session: Session) {
    const value = req.get('X-CSRF-Token') || '';
    return req.get('Origin') === config.origin && Buffer.byteLength(value) === Buffer.byteLength(session.csrf) &&
        timingSafeEqual(Buffer.from(value), Buffer.from(session.csrf));
}

export class AdminAuth {
    private db!: Database;
    constructor(readonly config: AdminConfig, private fetcher: typeof fetch = fetch) {}
    async init() {
        await mkdir(this.config.stateDir, { recursive: true, mode: 0o700 });
        this.db = await open({ filename: join(this.config.stateDir, 'admin.sqlite'), driver: sqlite3.Database });
        await chmod(join(this.config.stateDir, 'admin.sqlite'), 0o600);
        await this.db.exec(`PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS oauth_states (state_hash TEXT PRIMARY KEY, browser_hash TEXT, verifier TEXT, expires_at INTEGER);
            CREATE TABLE IF NOT EXISTS admin_sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER, login TEXT, csrf TEXT, expires_at INTEGER);
            CREATE TABLE IF NOT EXISTS admin_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, time INTEGER, actor INTEGER, action TEXT);`);
        return this;
    }
    async begin() {
        const state = token(), browser = token(), verifier = token();
        await this.db.run('DELETE FROM oauth_states WHERE expires_at < ?', Date.now());
        await this.db.run('DELETE FROM admin_sessions WHERE expires_at < ?', Date.now());
        await this.db.run('INSERT INTO oauth_states VALUES (?,?,?,?)', hash(state), hash(browser), verifier, Date.now() + 600_000);
        const url = new URL('https://github.com/login/oauth/authorize');
        url.search = new URLSearchParams({ client_id: this.config.clientId, redirect_uri: this.config.origin + '/api/admin/github/callback',
            scope: '', state, code_challenge: createHash('sha256').update(verifier).digest('base64url'),
            code_challenge_method: 'S256', allow_signup: 'false' }).toString();
        return { url: url.toString(), browser };
    }
    async finish(state: string, browser: string, code: string) {
        if (!state || !browser || !code || state.length > 128 || browser.length > 128 || code.length > 1024) throw new Error('Invalid login');
        const saved = await this.db.get('DELETE FROM oauth_states WHERE state_hash=? AND browser_hash=? AND expires_at>=? RETURNING verifier', hash(state), hash(browser), Date.now());
        if (!saved) throw new Error('Expired or invalid login');
        const response = await this.fetcher('https://github.com/login/oauth/access_token', {
            method: 'POST', signal: AbortSignal.timeout(15_000), headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify({ client_id: this.config.clientId, client_secret: this.config.clientSecret, code,
                redirect_uri: this.config.origin + '/api/admin/github/callback', code_verifier: saved.verifier }) });
        if (!response.ok) throw new Error('GitHub sign-in unavailable');
        const exchanged = await response.json();
        if (typeof exchanged.access_token !== 'string' || !exchanged.access_token) throw new Error('GitHub sign-in unavailable');
        const profile = await this.fetcher('https://api.github.com/user', { signal: AbortSignal.timeout(15_000), headers: {
            Authorization: 'Bearer ' + exchanged.access_token, Accept: 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'DistDNA-Admin' } });
        if (!profile.ok) throw new Error('GitHub identity unavailable');
        const user = await profile.json();
        // Stable numeric identity, never a username alone or possession of a GitHub account.
        if (!this.config.githubIds.includes(user.id) || typeof user.login !== 'string') {
            await this.db.run('INSERT INTO admin_audit(time,actor,action) VALUES (?,?,?)', Date.now(), Number.isSafeInteger(user.id) ? user.id : null, 'login-denied');
            throw new Error('Administrator access required');
        }
        const bearer = token(), csrf = token(), expires = Date.now() + 8 * 3600_000;
        await this.db.run('INSERT INTO admin_sessions VALUES (?,?,?,?,?)', hash(bearer), user.id, user.login, csrf, expires);
        await this.db.run('INSERT INTO admin_audit(time,actor,action) VALUES (?,?,?)', Date.now(), user.id, 'login');
        // GitHub tokens are used only to establish identity, never stored or sent to the browser.
        return bearer;
    }
    async session(bearer: string): Promise<Session | null> {
        if (!bearer || bearer.length > 128) return null;
        const session = await this.db.get<Session>('SELECT user_id,login,csrf,expires_at FROM admin_sessions WHERE token_hash=? AND expires_at>?', hash(bearer), Date.now());
        return session && this.config.githubIds.includes(session.user_id) ? session : null;
    }
    async logout(bearer: string, actor: number) {
        await this.db.run('DELETE FROM admin_sessions WHERE token_hash=?', hash(bearer));
        await this.db.run('INSERT INTO admin_audit(time,actor,action) VALUES (?,?,?)', Date.now(), actor, 'logout');
    }
    async close() { await this.db?.close(); }
}
