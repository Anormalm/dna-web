import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import type { AdminConfig } from './auth.js';

export class AdminOperationError extends Error { constructor(message: string, readonly status = 400) { super(message); } }
export class Operations {
    constructor(private config: AdminConfig | null) {}
    async call(action: string, input: unknown = {}, actor = 0): Promise<any> {
        if (!this.config?.runDir || !process.env.DNA_ADMIN_PYTHON) throw new AdminOperationError('Collector controls are not configured on this server.', 503);
        const script = process.env.DNA_ADMIN_CONTROL_SCRIPT || fileURLToPath(new URL('../../scripts/distdna_enrollment/admin_control.py', import.meta.url));
        return new Promise((resolve, reject) => {
            const child = spawn(process.env.DNA_ADMIN_PYTHON!, [script, '--run', this.config!.runDir!], { stdio: ['pipe','pipe','pipe'], env: { ...process.env, PYTHONNOUSERSITE: '1' } });
            let text = '', size = 0; const timer = setTimeout(() => { child.kill('SIGTERM'); reject(new AdminOperationError('Collector operation timed out. Check status before retrying.', 504)); }, 30_000);
            child.stdout.on('data', chunk => { size += chunk.length; if (size > 2*1024*1024) child.kill('SIGTERM'); else text += chunk; });
            child.stderr.resume(); // Provider bodies, filesystem paths and credentials never reach clients.
            child.on('error', () => { clearTimeout(timer); reject(new AdminOperationError('Collector controls are unavailable.', 503)); });
            child.on('exit', code => { clearTimeout(timer); try { const result = JSON.parse(text); if (code || result.error) reject(new AdminOperationError(result.error || 'Collector operation failed.',result.status || 400)); else resolve(result); } catch { reject(new AdminOperationError('Collector operation failed. Completed records are retained.',503)); } });
            child.stdin.on('error', () => {});
            child.stdin.end(JSON.stringify({ action, input, actor }));
        });
    }
}
