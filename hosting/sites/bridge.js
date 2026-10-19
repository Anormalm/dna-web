const allowed = new Set([
  'GET /api/admin/session', 'GET /api/admin/github/start', 'GET /api/admin/github/callback',
  'GET /api/admin/snapshot', 'GET /api/admin/controls', 'POST /api/admin/estimate', 'POST /api/admin/jobs', 'POST /api/admin/job-action', 'POST /api/admin/budget', 'POST /api/admin/collection-action', 'POST /api/admin/logout',
  'GET /api/v2/catalog', 'GET /api/v2/health', 'POST /api/v2/identify',
]);
const json = (data, status = 200) => Response.json(data, { status, headers: { 'cache-control': 'no-store' } });
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const validId = id => /^[0-9a-f-]{36}$/.test(id);
const encode = bytes => {
  let binary = ''; for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
  return btoa(binary);
};
const decode = value => Uint8Array.from(atob(value), c => c.charCodeAt(0));

function bridgeAuthorized(request, env) {
  const supplied = request.headers.get('x-dna-bridge-key') || '', expected = env.DNA_BRIDGE_KEY || '';
  if (!expected || supplied.length !== expected.length) return false;
  let difference = 0; for (let i = 0; i < expected.length; i++) difference |= supplied.charCodeAt(i) ^ expected.charCodeAt(i);
  return difference === 0;
}

export async function handler(request, env, ctx) {
  const url = new URL(request.url), path = url.pathname;
  if (path.startsWith('/__bridge/')) {
    if (!bridgeAuthorized(request, env)) return json({ error: 'Unauthorized' }, 401);
    if (!env.BUCKET) return json({ error: 'Bridge storage unavailable' }, 503);
    if (path === '/__bridge/poll' && request.method === 'POST') {
      await env.BUCKET.put('bridge-heartbeat', JSON.stringify({ at: Date.now() }));
      const listed = await env.BUCKET.list({ prefix: 'requests/', limit: 12 });
      const jobs = [];
      for (const item of listed.objects) {
        const stored = await env.BUCKET.get(item.key); if (!stored) continue;
        const job = await stored.json();
        if (job.expires < Date.now()) { await env.BUCKET.delete([item.key, 'results/' + job.id]); continue; }
        if (job.claimed) continue;
        const claimed = await env.BUCKET.put(item.key, JSON.stringify({ ...job, claimed: true }), { onlyIf: { etagMatches: stored.etag } });
        if (claimed) jobs.push(job);
        if (jobs.length === 3) break;
      }
      return json({ jobs });
    }
    if (path.startsWith('/__bridge/result/') && request.method === 'POST') {
      const id = path.slice('/__bridge/result/'.length);
      if (!validId(id)) return json({ error: 'Invalid result ID' }, 400);
      const queued = await env.BUCKET.get('requests/' + id);
      if (!queued) return json({ discarded: true });
      const job = await queued.json();
      if (!job.claimed || job.expires < Date.now()) return json({ discarded: true });
      const text = await request.text();
      if (text.length > 4 * 1024 * 1024) return json({ error: 'Response too large' }, 413);
      const result = JSON.parse(text);
      if (!Number.isInteger(result.status) || result.status < 200 || result.status > 599 || typeof result.body !== 'string' || !Array.isArray(result.headers)) return json({ error: 'Invalid response' }, 400);
      await env.BUCKET.put('results/' + id, text);
      return json({ ok: true });
    }
    return json({ error: 'Unknown bridge route' }, 404);
  }
  if (path.startsWith('/api/')) {
    if (path === '/api/admin/events') return json({ error: 'Hosted preview uses snapshot polling.' }, 503);
    if (!allowed.has(`${request.method} ${path}`)) return json({ error: 'Community submissions are disabled in this preview.' }, 403);
    if (!env.BUCKET) return json({ error: 'Collector bridge unavailable.' }, 503);
    const heartbeat = await env.BUCKET.get('bridge-heartbeat');
    if (!heartbeat || Date.now() - (await heartbeat.json()).at > 30_000) return json({ error: 'The collector connection is offline. Please retry shortly.' }, 503);
    const body = new Uint8Array(await request.arrayBuffer());
    if (body.length > 2 * 1024 * 1024) return json({ error: 'Hosted preview uploads are limited to 2 MB.' }, 413);
    const id = crypto.randomUUID(), headers = {};
    for (const name of ['accept', 'content-type', 'cookie', 'origin', 'x-csrf-token']) {
      const value = request.headers.get(name); if (value !== null) headers[name] = value;
    }
    await env.BUCKET.put('requests/' + id, JSON.stringify({ id, method: request.method, path: path + url.search, headers, body: encode(body), expires: Date.now() + 60_000 }));
    try {
      for (let i = 0; i < 95; i++) {
        const stored = await env.BUCKET.get('results/' + id);
        if (stored) {
          const result = await stored.json(), headers = new Headers({ 'cache-control': 'no-store', 'referrer-policy': 'no-referrer', 'x-content-type-options': 'nosniff' });
          for (const [key, value] of result.headers) if (['content-type', 'set-cookie', 'location', 'retry-after'].includes(key.toLowerCase())) headers.append(key, value);
          return new Response(result.status === 204 || result.status === 304 ? null : decode(result.body), { status: result.status, headers });
        }
        await pause(500);
      }
      return json({ error: 'Collector response timed out. Please retry.' }, 504);
    } finally { ctx.waitUntil(env.BUCKET.delete(['requests/' + id, 'results/' + id])); }
  }
  if (!['GET', 'HEAD'].includes(request.method)) return new Response('Method not allowed', { status: 405 });
  const asset = ASSETS[path] || (!path.split('/').pop().includes('.') ? ASSETS['/index.html'] : null);
  if (!asset) return new Response('Not found', { status: 404 });
  return new Response(request.method === 'HEAD' ? null : decode(asset.body), { headers: {
    'content-type': asset.type, 'cache-control': path.startsWith('/assets/') ? 'public, max-age=31536000, immutable' : 'no-store',
    'x-content-type-options': 'nosniff', 'referrer-policy': 'no-referrer',
  } });
}
export default { async fetch(request, env, ctx) {
  try { return await handler(request, env, ctx); }
  catch { return json({ error: 'Preview temporarily unavailable. Please retry.' }, 503); }
} };
