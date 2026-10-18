import { parentPort, workerData } from 'node:worker_threads';
import { tsImport } from 'tsx/esm/api';
const { createScorer } = await tsImport('./scorer.ts', import.meta.url);
const scorer = createScorer(workerData.directory);
parentPort.postMessage({ ready: true, catalog: scorer.catalog });
parentPort.on('message', ({ id, input }) => {
    try {
        parentPort.postMessage({ id, result: scorer.score(input) });
    } catch (error) {
        parentPort.postMessage({
            id,
            error: error instanceof Error ? error.message : 'Unable to score query.',
        });
    }
});
