// Browser-only fixtures. No imports from the live API or collector.
export type DemoMode = 'raw' | 'chat';
export type DemoPhase = 'stopped' | 'downloading' | 'collecting' | 'encoding' | 'cleanup' | 'complete' | 'paused' | 'budget_paused';
export type DemoQuote = { mode: DemoMode; model: string; byok: boolean };
export type DemoJob = DemoQuote & { id: number; phase: DemoPhase; resumePhase: DemoPhase; records: number; spent: number };
export type DemoState = { cap: number; jobs: DemoJob[]; events: string[]; nextId: number };
export type DemoAction = { type: 'add'; quote: DemoQuote } | { type: 'start' | 'stop'; id: number } | { type: 'budget'; cap: number } | { type: 'tick' };
export const initialDemo = (): DemoState => ({ cap: 25, jobs: [], events: ['Demo ready. Choose a model and inspect its example estimate.'], nextId: 1 });
export const demoSpent = (state: DemoState) => state.jobs.reduce((sum, job) => sum + job.spent, 0);
export const isDemoRunning = (phase: DemoPhase) => ['downloading', 'collecting', 'encoding', 'cleanup'].includes(phase);
const record = (state: DemoState, text: string): DemoState => ({ ...state, events: [text, ...state.events].slice(0, 12) });

export function demoReducer(state: DemoState, action: DemoAction): DemoState {
    if (action.type === 'budget') {
        if (!Number.isFinite(action.cap) || action.cap < 0) return state;
        return record({ ...state, cap: action.cap }, `Mock API budget saved: $${action.cap.toFixed(2)}. Existing simulated charges are retained.`);
    }
    if (action.type === 'add') {
        const job: DemoJob = { ...action.quote, id: state.nextId, phase: 'stopped', resumePhase: action.quote.mode === 'raw' ? 'downloading' : 'collecting', records: 0, spent: 0 };
        return record({ ...state, nextId: state.nextId + 1, jobs: [...state.jobs, job] }, `${job.model} added stopped. Select Start to simulate collection.`);
    }
    if (action.type === 'start' || action.type === 'stop') {
        const job = state.jobs.find(j => j.id === action.id);
        if (!job || job.phase === 'complete') return state;
        if (action.type === 'start' && isDemoRunning(job.phase)) return state;
        if (action.type === 'stop' && !isDemoRunning(job.phase)) return state;
        const next = action.type === 'start' ? { ...job, phase: job.resumePhase } : { ...job, resumePhase: job.phase, phase: 'paused' as const };
        return record({ ...state, jobs: state.jobs.map(j => j.id === job.id ? next : j) }, `${job.model}: ${action.type === 'start' ? 'simulation started' : 'stopped; collected responses retained'}.`);
    }
    let committed = demoSpent(state);
    const changes: string[] = [];
    const jobs = state.jobs.map(job => {
        if (!isDemoRunning(job.phase)) return job;
        if (job.phase === 'collecting') {
            const charge = job.mode === 'chat' ? 0.08 : 0;
            if (charge && committed + charge > state.cap + 1e-9) {
                changes.push(`${job.model}: mock API budget reached. Raise the ceiling, then Start to resume.`);
                return { ...job, phase: 'budget_paused' as const, resumePhase: 'collecting' as const };
            }
            committed += charge;
            const records = Math.min(800, job.records + 40);
            if (records === 800) changes.push(`${job.model}: 800 example responses collected; constructing a simulated fingerprint.`);
            return { ...job, records, spent: Math.round((job.spent + charge) * 100) / 100, phase: records === 800 ? 'encoding' as const : job.phase };
        }
        const phase: DemoPhase = job.phase === 'downloading' ? 'collecting' : job.phase === 'encoding' ? (job.mode === 'raw' ? 'cleanup' : 'complete') : 'complete';
        changes.push(`${job.model}: ${phase === 'collecting' ? 'mock weights ready; collecting responses' : phase === 'cleanup' ? 'fingerprint ready; removing temporary model weights' : 'demo complete. Example fingerprint is ready; the live Galaxy is unchanged'}.`);
        return { ...job, phase };
    });
    return { ...state, jobs, events: [...changes.reverse(), ...state.events].slice(0, 12) };
}
