import {
    identify,
    parseQueries,
    assertCompatible,
    type Method,
    type Identification,
    type RankedCandidate,
} from './engine.js';
import { loadRepository } from './repository.js';
export function createScorer(directory: string) {
    const repo = loadRepository(directory);
    return {
        catalog: repo.catalog,
        score(input: { method: Method; queryId: string; queries?: unknown }): Identification {
            if (input.method !== 'rbf' && input.method !== 'linear')
                throw new Error('Choose Linear or RBF.');
            const queries =
                input.queries === undefined ? repo.queries : parseQueries(input.queries);
            assertCompatible(repo.protocol, queries.protocol);
            if (!queries.queries.some((q) => q.id === input.queryId))
                throw new Error('Select an available query.');
            if (queries.queries.some((q) => repo.referenceIds.has(q.id)))
                throw new Error('Query IDs overlap enrollment IDs. Use held-out response draws.');
            const selectedQueries = { ...queries, queries: queries.queries.filter((q) => q.id === input.queryId) };
            const candidates: RankedCandidate[] = [];
            // Score one identity at a time to bound live memory for large binary banks.
            for (const model of repo.modelIds)
                candidates.push(
                    ...identify(repo.galleryFor(model), selectedQueries, input.queryId, input.method)
                        .candidates,
                );
            candidates.sort(
                (a, b) =>
                    a.distance - b.distance ||
                    (a.modelId < b.modelId ? -1 : a.modelId > b.modelId ? 1 : 0),
            );
            const margin =
                candidates.length > 1 ? candidates[1].distance - candidates[0].distance : null;
            return {
                method: input.method,
                queryId: input.queryId,
                candidates,
                margin,
                tied: margin === 0,
            };
        },
    };
}
