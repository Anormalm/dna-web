import { describe, expect, it } from 'vitest';
import { demoReducer as reduce, initialDemo, demoSpent } from '../src/admin/demo';

const add = (mode: 'raw' | 'chat') => reduce(initialDemo(), { type: 'add', quote: { mode, model: 'demo/model', byok: true } });
describe('isolated admin demonstration', () => {
    it('adds stopped and retains responses through stop/resume', () => {
        let s = add('raw');
        expect(s.jobs[0].phase).toBe('stopped');
        s = reduce(s, { type: 'start', id: 1 });
        s = reduce(reduce(s, { type: 'tick' }), { type: 'tick' });
        expect(s.jobs[0].records).toBe(40);
        s = reduce(s, { type: 'stop', id: 1 });
        s = reduce(s, { type: 'tick' });
        expect(s.jobs[0].records).toBe(40);
        s = reduce(reduce(s, { type: 'start', id: 1 }), { type: 'tick' });
        expect(s.jobs[0].records).toBe(80);
        for (let i = 0; i < 25; i++) s = reduce(s, { type: 'tick' });
        expect(s.jobs[0].phase).toBe('complete');
        expect(demoSpent(s)).toBe(0);
        expect(s.events.some(e => e.includes('removing temporary model weights'))).toBe(true);
    });
    it('shares a finite mock budget across API jobs and resumes after raising it', () => {
        let s = reduce(add('chat'), { type: 'budget', cap: 0.12 });
        s = reduce(s, { type: 'add', quote: { mode: 'chat', model: 'demo/second', byok: false } });
        s = reduce(reduce(s, { type: 'start', id: 1 }), { type: 'start', id: 2 });
        s = reduce(reduce(s, { type: 'tick' }), { type: 'tick' });
        expect(demoSpent(s)).toBe(0.08);
        expect(s.jobs.every(j => j.phase === 'budget_paused')).toBe(true);
        s = reduce(s, { type: 'budget', cap: 0 });
        expect(demoSpent(s)).toBe(0.08);
        s = reduce(s, { type: 'budget', cap: 10 });
        s = reduce(reduce(s, { type: 'start', id: 1 }), { type: 'start', id: 2 });
        for (let i = 0; i < 25; i++) s = reduce(s, { type: 'tick' });
        expect(s.jobs.every(j => j.phase === 'complete')).toBe(true);
        expect(demoSpent(s)).toBe(3.2);
    });
});
