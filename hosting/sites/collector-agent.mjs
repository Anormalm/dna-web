import http from 'node:http';
import { spawn } from 'node:child_process';
import { openSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
// Credentials arrive once on hidden stdin and remain in this process's memory.
if (process.stdin.isTTY) process.stdin.setRawMode(true);
process.stdout.write('Ready for private bridge configuration on stdin (input is hidden).\n');
const configuration = await new Promise(resolve => {
  let text = '';
  const receive = chunk => { text += chunk; if (text.includes('\n')) { process.stdin.off('data', receive); process.stdin.pause(); resolve(JSON.parse(text.trim())); } };
  process.stdin.setEncoding('utf8'); process.stdin.on('data', receive);
});
if (process.argv.includes('--daemon')) {
  const log = openSync(process.env.DNA_BRIDGE_LOG || '/tmp/distdna-collector-bridge.log', 'a', 0o600);
  const child = spawn(process.execPath, [fileURLToPath(import.meta.url)], { detached: true, stdio: ['pipe', log, log] });
  child.stdin.end(JSON.stringify(configuration) + '\n'); child.unref();
  console.log(JSON.stringify({ pid: child.pid })); process.exit(0);
}
const origin = new URL(configuration.origin).origin;
if (!origin.endsWith('.chatgpt.site') || !origin.startsWith('https://')) throw new Error('Invalid hosted origin');
const auth = { 'OAI-Sites-Authorization': 'Bearer ' + configuration.token, 'X-DNA-Bridge-Key': configuration.token, 'Content-Type': 'application/json' };
const allowed = new Set(['GET /api/admin/session','GET /api/admin/github/start','GET /api/admin/github/callback','GET /api/admin/snapshot','POST /api/admin/logout','GET /api/v2/catalog','GET /api/v2/health','POST /api/v2/identify']);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let stopping = false;
process.on('SIGTERM', () => { stopping = true; });
async function handle(job) {
  const path = new URL(job.path, 'http://127.0.0.1:5180');
  if (path.origin !== 'http://127.0.0.1:5180' || !allowed.has(job.method + ' ' + path.pathname) || job.expires < Date.now()) return;
  const headers = {};
  for (const key of ['accept','content-type','cookie','origin','x-csrf-token']) if (typeof job.headers[key] === 'string') headers[key] = job.headers[key];
  const body = Buffer.from(job.body, 'base64'); headers['content-length'] = String(body.length);
  const result = await new Promise(resolve => {
    const req = http.request(path, { method: job.method, headers }, response => {
      const chunks = []; let total = 0;
      response.on('data', chunk => { total += chunk.length; if (total > 3 * 1024 * 1024) response.destroy(); else chunks.push(chunk); });
      response.on('error', () => resolve({status:502,headers:[],body:''}));
      response.on('end', () => {
        const pairs = []; for (let i = 0; i < response.rawHeaders.length; i += 2) if (['content-type','set-cookie','location','retry-after'].includes(response.rawHeaders[i].toLowerCase())) pairs.push([response.rawHeaders[i],response.rawHeaders[i+1]]);
        resolve({ status: response.statusCode, headers: pairs, body: Buffer.concat(chunks).toString('base64') });
      });
    });
    req.setTimeout(35_000, () => req.destroy());
    req.on('error', () => resolve({ status: 502, headers: [], body: '' })); req.end(body);
  });
  const response = await fetch(origin + '/__bridge/result/' + job.id, { method:'POST', headers:auth, body:JSON.stringify(result), signal:AbortSignal.timeout(15_000) });
  if (!response.ok) throw new Error('Result upload failed');
}
console.log('Collector bridge started; forwarding only preview API routes.');
let failures = 0;
while (!stopping) {
  try {
    const response = await fetch(origin + '/__bridge/poll', { method:'POST', headers:auth, body:'{}', signal:AbortSignal.timeout(15_000) });
    if (!response.ok) throw new Error('Bridge not ready');
    const { jobs } = await response.json();
    await Promise.all(jobs.map(handle));
    if (failures) console.log('Collector connection recovered.'); failures = 0;
    await pause(jobs.length ? 200 : 2000);
  } catch {
    if (failures++ === 0 || failures % 30 === 0) console.log('Collector connection unavailable; retrying.');
    await pause(3000);
  }
}
