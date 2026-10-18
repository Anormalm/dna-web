import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import {
    identify,
    parseGallery,
    parseQueries,
    promptDistance,
    assertCompatible,
    type Gallery,
    type QueryBank,
    type Method,
} from '../server/distdna/engine';
const p = {
    encoder: 'test',
    encoderRevision: 'fixed',
    dimension: 1,
    normalized: false,
    preprocessing: 'identity',
    promptIds: ['p'],
    promptSha256: '0'.repeat(64),
    sigmaSquared: 1,
    calibration: 'independent fixed fixture',
};
function fixture(): { gallery: Gallery; queries: QueryBank } {
    return {
        gallery: {
            schema: 'distdna.gallery.v1',
            title: 'test',
            description: 'test',
            protocol: p,
            references: [
                { id: 'A-r0', modelId: 'A', revision: 'a', samples: [[[0]]] },
                { id: 'B-r0', modelId: 'B', revision: 'b', samples: [[[1]]] },
            ],
        },
        queries: {
            schema: 'distdna.queries.v1',
            title: 'test',
            protocol: p,
            queries: [{ id: 'q', samples: [[[0.25]]] }],
        },
    };
}
describe('DistDNA scientific contract', () => {
    it('matches the closed form for singleton RBF MMD', () => {
        expect(promptDistance([[0]], [[1]], 'rbf', 1)).toBeCloseTo(2 * (1 - Math.exp(-0.5)), 14);
    });
    it('retains within-arm diagonal terms and supports unequal counts', () => {
        // P = delta_0; Q = .5 delta_0 + .5 delta_1. MMD² = .5 (1 - k(0,1)).
        expect(promptDistance([[0]], [[0], [1]], 'rbf', 1)).toBeCloseTo(
            0.5 * (1 - Math.exp(-0.5)),
            14,
        );
    });
    it('Linear keeps means unnormalized and RBF distinguishes equal-mean distributions', () => {
        expect(promptDistance([[-1], [1]], [[0], [0]], 'linear', 1)).toBe(0);
        expect(promptDistance([[-1], [1]], [[0], [0]], 'rbf', 1)).toBeGreaterThan(0.35);
    });
    it('averages promptwise distances rather than mixing prompts', () => {
        const { gallery, queries } = fixture();
        gallery.protocol = queries.protocol = { ...p, promptIds: ['p', 'q'] };
        gallery.references.forEach((r) => (r.samples = [[[0]], [[1]]]));
        queries.queries[0].samples = [[[1]], [[0]]];
        expect(identify(gallery, queries, 'q', 'linear').candidates[0].distance).toBe(1);
    });
    it('minimizes over separate references before ranking checkpoint identities', () => {
        const { gallery, queries } = fixture();
        gallery.references.push({ id: 'B-r1', modelId: 'B', revision: 'b', samples: [[[0.25]]] });
        const result = identify(gallery, queries, 'q', 'linear');
        expect(result.candidates.map((r) => r.modelId)).toEqual(['B', 'A']);
        expect(result.candidates[0].referenceId).toBe('B-r1');
        expect(result.margin).toBe(0.0625);
    });
    it('uses a stable ID tie rule and reports ties', () => {
        const { gallery, queries } = fixture();
        queries.queries[0].samples = [[[0.5]]];
        gallery.references.reverse();
        const result = identify(gallery, queries, 'q', 'linear');
        expect(result.candidates[0].modelId).toBe('A');
        expect(result.tied).toBe(true);
    });
    it('does not invent an unknown-model rejection or a confidence percentage', () => {
        const { gallery, queries } = fixture();
        queries.queries[0].samples = [[[100]]];
        const result = identify(gallery, queries, 'q', 'linear');
        expect(result.candidates[0].modelId).toBe('B');
        expect(result).not.toHaveProperty('confidence');
    });
    it('rejects legacy vectors and malformed or non-finite tensors', () => {
        expect(() => parseGallery({ models: [{ signature: [1, 2] }] })).toThrow('Legacy');
        const { gallery } = fixture();
        gallery.references[0].samples = [[[NaN]]];
        expect(() => parseGallery(gallery)).toThrow('finite');
        gallery.references[0].samples = [[[0, 1]]];
        expect(() => parseGallery(gallery)).toThrow('dimension');
    });
    it('rejects mismatched protocol metadata including prompt order and bandwidth', () => {
        for (const key of [
            'encoder',
            'encoderRevision',
            'preprocessing',
            'calibration',
            'promptSha256',
        ] as const) {
            expect(() => assertCompatible(p, { ...p, [key]: 'different' })).toThrow(key);
        }
        expect(() => assertCompatible(p, { ...p, sigmaSquared: 2 })).toThrow('sigmaSquared');
        expect(() => assertCompatible(p, { ...p, promptIds: ['other'] })).toThrow('prompt');
    });
    it('rejects false normalization metadata and inconsistent checkpoint revisions', () => {
        const { gallery } = fixture();
        gallery.protocol = { ...p, normalized: true };
        expect(() => parseGallery(gallery)).toThrow('unit length');
        gallery.protocol = p;
        gallery.references[1].modelId = 'A';
        expect(() => parseGallery(gallery)).toThrow('revisions');
    });
    it('rejects reused query IDs, unknown queries, empty arms and duplicate IDs', () => {
        const { gallery, queries } = fixture();
        expect(() => identify(gallery, queries, 'missing', 'rbf')).toThrow('available');
        queries.queries[0].id = 'A-r0';
        expect(() => identify(gallery, queries, 'A-r0', 'rbf')).toThrow('overlap');
        queries.queries[0].samples = [[]];
        expect(() => parseQueries(queries)).toThrow('Responses');
        gallery.references.push(gallery.references[0]);
        expect(() => parseGallery(gallery)).toThrow('unique');
    });
});
describe('real research-bank replay against independent NumPy oracle', () => {
    const read = (name: string) =>
        JSON.parse(
            readFileSync(new URL(`../server/distdna/data/${name}`, import.meta.url), 'utf8'),
        );
    const gallery = parseGallery(read('example-gallery.json'));
    const queries = parseQueries(read('example-queries.json'));
    const golden: {
        method: Method;
        queryId: string;
        distances: { modelId: string; referenceId: string; distance: number }[];
    }[] = read('example-distances.json');
    for (const trial of golden) {
        it(`${trial.method} ${trial.queryId} matches NumPy and ranks every candidate`, () => {
            const result = identify(gallery, queries, trial.queryId, trial.method);
            for (const candidate of result.candidates) {
                const expected = trial.distances
                    .filter((r) => r.modelId === candidate.modelId)
                    .sort((a, b) => a.distance - b.distance)[0];
                expect(candidate.distance).toBeCloseTo(expected.distance, 10);
                expect(candidate.referenceId).toBe(expected.referenceId);
            }
        });
    }
});
describe('benchmark data provenance and aggregation', () => {
    it('contains every measured grid cell, with published aggregate scores', () => {
        const data = JSON.parse(
            readFileSync(new URL('../server/distdna/data/benchmark.json', import.meta.url), 'utf8'),
        );
        expect(data.cells).toHaveLength(288);
        const keys = data.cells.map(
            (c: any) => `${c.method}/${c.readout}/${c.temperature}/${c.topP}`,
        );
        expect(new Set(keys).size).toBe(288);
        const mean = (method: string, readout: string) => {
            const cells = data.cells.filter(
                (c: any) => c.method === method && c.readout === readout,
            );
            return cells.reduce((sum: number, c: any) => sum + c.top1, 0) / cells.length;
        };
        expect(mean('exact_mmd', 'nearest')).toBeCloseTo(81.2922705314, 8);
        expect(mean('exact_mmd', 'svm')).toBeCloseTo(89.9275362319, 8);
        expect(mean('llmdna_d512_s42', 'nearest')).toBeCloseTo(73.6594202899, 8);
    });
});
