/** Check the production scorer against the independent NumPy extension report. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { createScorer } from '../server/distdna/scorer';

const directory = resolve(process.argv[2] || 'server/data/distdna-expanded');
const read = (name: string) =>
    JSON.parse(readFileSync(join(directory, name), 'utf8'));
const scorer = createScorer(directory);
const provenance = read('enrollment-provenance.json');
const expected = read('validation.json');
let comparisons = 0;
let maxError = 0;
for (const query of provenance.newSavedQueryTruth) {
    for (const method of ['linear', 'rbf'] as const) {
        const result = scorer.score({ method, queryId: query.queryId });
        assert.equal(result.candidates.length, scorer.catalog.models.length);
        const oracle = expected.rows.find(
            (row: any) =>
                row.method === method &&
                row.modelId === query.modelId &&
                row.settingId === query.settingId,
        );
        assert.ok(oracle, 'Missing NumPy validation row');
        assert.equal(
            result.candidates.findIndex((r) => r.modelId === query.modelId) + 1,
            oracle.rank,
        );
        for (let i = 0; i < oracle.predictions.length; i++) {
            const actual = result.candidates[i];
            const target = oracle.predictions[i];
            assert.equal(actual.modelId, target.modelId);
            assert.equal(actual.referenceId, target.referenceId);
            const error = Math.abs(actual.distance - target.distance);
            assert.ok(error < 1e-10, `Distance mismatch: ${error}`);
            maxError = Math.max(maxError, error);
            comparisons++;
        }
    }
}
assert.ok(comparisons > 0, 'No new saved query was validated');
console.log(
    JSON.stringify({
        passed: true,
        galleryEntries: scorer.catalog.models.length,
        newQueries: provenance.newSavedQueryTruth.length,
        candidateComparisons: comparisons,
        maxDistanceError: maxError,
    }),
);
