import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../hosting/sites/bridge.js', import.meta.url), 'utf8');
const assets = { '/index.html': { type: 'text/html', body: btoa('<h1>DNA</h1>') } };
const { handler } = await import('data:text/javascript;base64,' + Buffer.from(source + '\nconst ASSETS = ' + JSON.stringify(assets) + ';').toString('base64'));
const values = new Map(); let counter = 0;
const BUCKET = {
 async put(key, body, options) { if(options?.onlyIf && values.get(key)?.etag!==options.onlyIf.etagMatches)return null; const item={body:String(body),etag:String(++counter)};values.set(key,item);return item; },
 async get(key) { const item=values.get(key);return item?{...item,json:async()=>JSON.parse(item.body)}:null; },
 async delete(keys) { for(const key of Array.isArray(keys)?keys:[keys])values.delete(key); },
 async list({prefix,limit}) {return {objects:[...values.keys()].filter(k=>k.startsWith(prefix)).slice(0,limit).map(key=>({key}))};},
};
const env={BUCKET,DNA_BRIDGE_KEY:'test-private-key'}, ctx={waitUntil:p=>p};
const call=(path,options={})=>handler(new Request('https://preview.test'+path,options),env,ctx);
assert.equal((await call('/admin')).status,200);
assert.equal((await call('/.env')).status,404);
assert.equal((await call('/__bridge/poll',{method:'POST'})).status,401);
assert.equal((await call('/api/admin/snapshot')).status,503);
assert.equal((await call('/api/submit',{method:'POST'})).status,403);
const headers={'x-dna-bridge-key':'test-private-key'};
await call('/__bridge/poll',{method:'POST',headers});
const awaiting=call('/api/admin/github/callback?state=state&code=code',{headers:{cookie:'dna_admin_oauth=browser'}});
await new Promise(r=>setTimeout(r,50));
const jobs=await(await call('/__bridge/poll',{method:'POST',headers})).json();
assert.equal(jobs.jobs.length,1);
assert.equal(jobs.jobs[0].headers.cookie,'dna_admin_oauth=browser');
assert.equal(jobs.jobs[0].path,'/api/admin/github/callback?state=state&code=code');
assert.equal((await(await call('/__bridge/poll',{method:'POST',headers})).json()).jobs.length,0);
const result={status:303,headers:[['Location','https://preview.test/admin'],['Set-Cookie','dna_admin_oauth=; Secure; HttpOnly'],['Set-Cookie','dna_admin_session=example; Secure; HttpOnly']],body:''};
await call('/__bridge/result/'+jobs.jobs[0].id,{method:'POST',headers,body:JSON.stringify(result)});
const response=await awaiting;
assert.equal(response.status,303);
assert.equal(response.headers.get('location'),'https://preview.test/admin');
assert.equal(response.headers.getSetCookie().length,2);
assert.equal([...values.keys()].filter(k=>k.startsWith('requests/')||k.startsWith('results/')).length,0);
console.log('Bridge checks passed: static assets, private routes, offline state, claim-once delivery, OAuth cookies/redirect and cleanup.');
