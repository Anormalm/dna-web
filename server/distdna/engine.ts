/** Prompt-conditional DistDNA. All distances are squared and averaged over prompts. */
export type Method = 'rbf' | 'linear';
export interface Protocol {
    encoder: string;
    encoderRevision: string;
    dimension: number;
    normalized: boolean;
    preprocessing: string;
    promptIds: string[];
    promptSha256: string;
    sigmaSquared: number;
    calibration: string;
}
export interface Fingerprint {
    id: string;
    samples: number[][][]; // [prompt][independent response][embedding coordinate]
}
export interface Reference extends Fingerprint {
    modelId: string;
    revision: string;
}
export interface Gallery {
    schema: 'distdna.gallery.v1';
    title: string;
    description: string;
    protocol: Protocol;
    references: Reference[];
}
export interface QueryBank {
    schema: 'distdna.queries.v1';
    title: string;
    protocol: Protocol;
    queries: Fingerprint[];
}
export interface RankedCandidate {
    modelId: string;
    revision: string;
    referenceId: string;
    distance: number;
    promptDistances: number[];
}
export interface Identification {
    method: Method;
    queryId: string;
    candidates: RankedCandidate[];
    margin: number | null;
    tied: boolean;
}
export const MAX_FILE_BYTES = 64 * 1024 * 1024;
const MAX_VALUES = 4_000_000;

function object(value: unknown, name: string): Record<string, unknown> {
    if (!value || typeof value !== 'object' || Array.isArray(value))
        throw new Error(`${name} must be an object.`);
    return value as Record<string, unknown>;
}
function text(value: unknown, name: string): asserts value is string {
    if (typeof value !== 'string' || !value.trim() || value.length > 1000)
        throw new Error(`${name} must be a nonempty string of at most 1,000 characters.`);
}
function list(value: unknown, name: string, max: number): asserts value is unknown[] {
    if (!Array.isArray(value) || !value.length || value.length > max)
        throw new Error(`${name} must contain 1–${max} entries.`);
}
function protocol(value: unknown): Protocol {
    const p = object(value, 'Protocol');
    for (const key of ['encoder', 'encoderRevision', 'preprocessing', 'calibration'])
        text(p[key], key);
    if (
        typeof p.dimension !== 'number' ||
        !Number.isInteger(p.dimension) ||
        p.dimension < 1 ||
        p.dimension > 4096
    )
        throw new Error('Embedding dimension must be an integer from 1 to 4096.');
    if (typeof p.normalized !== 'boolean')
        throw new Error('Protocol must declare whether response embeddings are normalized.');
    if (
        typeof p.sigmaSquared !== 'number' ||
        !Number.isFinite(p.sigmaSquared) ||
        p.sigmaSquared < 1e-12
    )
        throw new Error(
            'A positive, frozen calibration sigmaSquared (at least 1e-12) is required.',
        );
    if (typeof p.promptSha256 !== 'string' || !/^[a-f0-9]{64}$/.test(p.promptSha256))
        throw new Error('A SHA-256 digest of the ordered prompts is required.');
    list(p.promptIds, 'Prompt IDs', 128);
    p.promptIds.forEach((id) => text(id, 'Prompt ID'));
    if (new Set(p.promptIds).size !== p.promptIds.length)
        throw new Error('Prompt IDs must be unique.');
    return p as unknown as Protocol;
}
function fingerprints(value: unknown, p: Protocol, isGallery: boolean): Fingerprint[] {
    list(value, isGallery ? 'Enrollment references' : 'Queries', 1024);
    let total = 0;
    const ids = new Set<string>();
    const revisions = new Map<string, string>();
    for (const raw of value) {
        const row = object(raw, 'Fingerprint');
        text(row.id, 'Fingerprint ID');
        if (ids.has(row.id)) throw new Error('Fingerprint IDs must be unique.');
        ids.add(row.id);
        if (isGallery) {
            text(row.modelId, 'Checkpoint ID');
            text(row.revision, 'Checkpoint revision');
            if (revisions.has(row.modelId) && revisions.get(row.modelId) !== row.revision)
                throw new Error('Give different revisions distinct checkpoint IDs.');
            revisions.set(row.modelId, row.revision);
        }
        if (!Array.isArray(row.samples) || row.samples.length !== p.promptIds.length)
            throw new Error(
                'Every fingerprint must contain the same ordered prompts as its protocol.',
            );
        for (const responses of row.samples) {
            list(responses, 'Responses per prompt', 64);
            for (const vec of responses) {
                if (!Array.isArray(vec) || vec.length !== p.dimension)
                    throw new Error('Every response must match the declared embedding dimension.');
                total += vec.length;
                if (total > MAX_VALUES)
                    throw new Error(
                        'This bank exceeds the request limit of 4 million embedding coordinates. Use a smaller request.',
                    );
                let norm = 0;
                for (const x of vec) {
                    if (typeof x !== 'number' || !Number.isFinite(x) || Math.abs(x) > 1e6)
                        throw new Error(
                            'Embeddings must contain finite numbers with absolute value at most 1e6.',
                        );
                    norm += x * x;
                }
                if (p.normalized && Math.abs(norm - 1) > 0.002)
                    throw new Error('An embedding is not unit length, despite normalized=true.');
            }
        }
    }
    return value as unknown as Fingerprint[];
}
export function parseGallery(value: unknown): Gallery {
    const g = object(value, 'Gallery');
    if (g.schema !== 'distdna.gallery.v1')
        throw new Error(
            'Expected distdna.gallery.v1. Legacy LLM-DNA vectors need new repeated-response enrollment.',
        );
    text(g.title, 'Gallery title');
    text(g.description, 'Gallery description');
    const p = protocol(g.protocol);
    fingerprints(g.references, p, true);
    return g as unknown as Gallery;
}
export function parseQueries(value: unknown): QueryBank {
    const q = object(value, 'Query bank');
    if (q.schema !== 'distdna.queries.v1')
        throw new Error('Expected distdna.queries.v1 with per-prompt response embeddings.');
    text(q.title, 'Query bank title');
    const p = protocol(q.protocol);
    fingerprints(q.queries, p, false);
    return q as unknown as QueryBank;
}
export function assertCompatible(a: Protocol, b: Protocol): void {
    for (const key of [
        'encoder',
        'encoderRevision',
        'dimension',
        'normalized',
        'preprocessing',
        'promptSha256',
        'sigmaSquared',
        'calibration',
    ] as const) {
        if (a[key] !== b[key])
            throw new Error(
                `Query and enrollment ${key} differ. Use the enrollment protocol when encoding queries.`,
            );
    }
    if (JSON.stringify(a.promptIds) !== JSON.stringify(b.promptIds))
        throw new Error('Query and enrollment prompt IDs or order differ.');
}
function squared(a: number[], b: number[]): number {
    let result = 0;
    for (let i = 0; i < a.length; i++) result += (a[i] - b[i]) ** 2;
    return result;
}
function kernelMean(a: number[][], b: number[][], sigmaSquared: number): number {
    let sum = 0;
    for (const x of a) for (const y of b) sum += Math.exp(-squared(x, y) / (2 * sigmaSquared));
    return sum / (a.length * b.length);
}
function mean(a: number[][]): number[] {
    return a[0].map((_, j) => a.reduce((sum, vec) => sum + vec[j], 0) / a.length);
}
export function promptDistance(
    a: number[][],
    b: number[][],
    method: Method,
    sigmaSquared: number,
): number {
    return method === 'linear'
        ? squared(mean(a), mean(b))
        : Math.max(
              0,
              kernelMean(a, a, sigmaSquared) +
                  kernelMean(b, b, sigmaSquared) -
                  2 * kernelMean(a, b, sigmaSquared),
          );
}
export function identify(
    gallery: Gallery,
    queries: QueryBank,
    queryId: string,
    method: Method,
): Identification {
    // This entry point validates even when called outside the scoring worker.
    parseGallery(gallery);
    parseQueries(queries);
    assertCompatible(gallery.protocol, queries.protocol);
    if (method !== 'linear' && method !== 'rbf') throw new Error('Choose Linear or RBF.');
    const referenceIds = new Set(gallery.references.map((r) => r.id));
    if (queries.queries.some((q) => referenceIds.has(q.id)))
        throw new Error(
            'Query IDs overlap enrollment IDs. Use independent held-out response draws.',
        );
    const query = queries.queries.find((q) => q.id === queryId);
    if (!query) throw new Error('Select an available query.');
    // Bound CPU cost separately from upload size, including unequal response counts.
    const operations = gallery.references.reduce(
        (sum, r) =>
            sum +
            query.samples.reduce((n, a, i) => {
                const b = r.samples[i];
                return (
                    n +
                    (a.length ** 2 + b.length ** 2 + a.length * b.length) *
                        gallery.protocol.dimension
                );
            }, 0),
        0,
    );
    if (operations > 500_000_000)
        throw new Error(
            'This comparison exceeds the per-checkpoint compute limit. Export fewer references or responses.',
        );
    const models = new Map<string, RankedCandidate>();
    // Stable lexicographic tie-breaking, independent of JSON order or runtime locale.
    const compare = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
    for (const ref of [...gallery.references].sort((a, b) => compare(a.id, b.id))) {
        const promptDistances = query.samples.map((a, i) =>
            promptDistance(a, ref.samples[i], method, gallery.protocol.sigmaSquared),
        );
        const distance = promptDistances.reduce((sum, d) => sum + d, 0) / promptDistances.length;
        const previous = models.get(ref.modelId);
        if (!previous || distance < previous.distance)
            models.set(ref.modelId, {
                modelId: ref.modelId,
                revision: ref.revision,
                referenceId: ref.id,
                distance,
                promptDistances,
            });
    }
    const candidates = [...models.values()].sort(
        (a, b) => a.distance - b.distance || compare(a.modelId, b.modelId),
    );
    const margin = candidates.length > 1 ? candidates[1].distance - candidates[0].distance : null;
    return { method, queryId, candidates, margin, tied: margin === 0 };
}
