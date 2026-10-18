# DistDNA in the existing explorer

The versioned API identifies an enrolled checkpoint from repeated, prompt-conditional
response embeddings. It supports Linear and exact RBF nearest-reference scoring,
with version-separated Community Lab requests. Existing v1 routes remain available.

## Existing pages

Use the v1/v2 selector within Galaxy, Community Lab, Workbench and About.
`?version=v2` survives navigation; v1 remains the default. Raw/chat controls remain
available in v1; v2 currently displays the separately enrolled raw gallery.
Workbench provides saved queries and compatible encoded-query uploads, with
Linear or RBF nearest-reference scores. It states the closed-set assumption.

For a local frontend and API together, run `npm run dev:all` and open
`http://localhost:5173/workbench?version=v2`. For separate ports use `PORT=3002`
on the backend and `API_PROXY_TARGET=http://localhost:3002` on Vite.

## Run

```bash
npm ci
npm run server
# GET http://localhost:3001/api/v2/health
# GET http://localhost:3001/api/v2/catalog
```

The bundled server dataset contains four real checkpoint entries and four held-out
queries. Select a compatible larger bank with `DISTDNA_DATA_DIR=/path/to/bank`;
`PORT=3002` selects a separate development port. Runtime banks belong in ignored
`server/data` or an external mounted directory. Restart the backend after changing
banks. The worker validates its bank checksum and serves one consistent snapshot.

To export the archived 115-entry bank:

```bash
python -m pip install numpy scipy
python scripts/build_distdna_catalog.py --run /path/to/wide-decoding-115-20260924 --output server/data/distdna-115
DISTDNA_DATA_DIR=server/data/distdna-115 npm run server
npx tsx scripts/validate_distdna_bundle.ts server/data/distdna-115
```

This preserves 20 prompts, four responses per prompt, eight enrolled settings per
entry, and the frozen 768-dimensional BGE encoder. The binary bank uses streamed
checksums and per-reference reads, supporting banks beyond Node's single-Buffer
limit. Full-catalog latency still needs validation on the completed gallery.

## Backend

- `GET /api/v2/catalog`: checkpoint metadata, visualization coordinates, query IDs
  and enrollment protocol. It does not transfer the response-embedding bank.
- `GET /api/v2/health`: worker readiness and queue size.
- `POST /api/v2/identify`: `{ "method": "rbf" | "linear", "queryId": "heldout-01",
  "queries"?: <distdna.queries.v1 bank> }`. The optional bank accepts independently
  collected, already-encoded responses. Response: ranked candidates, nearest
  reference IDs, per-prompt squared distances, runner-up gap and gallery revision.

Scoring runs in a server worker thread, so it does not block Express. The queue
allows three outstanding jobs, uploads are limited to 16 MB, and jobs have a
60-second deadline. Timeout terminates the worker; the next request starts a new
one. Invalid protocol/shape/numeric values yield 400, oversized bodies 413,
queue saturation 429 and unavailable/timed-out scoring 503. Inputs cannot name
files, supply alternate enrollment, or fit calibration. Uploads stay in worker
memory for processing and are not written to disk. A disconnected browser may
leave a bounded scoring job running until completion or timeout.

The current endpoint performs nearest-reference identification. The saved SVM
benchmark remains an archived measurement, not an interactive SVM implementation.
Raw generation and semantic encoding are offline steps; no model API keys or GPU
are required for this web preview. See [query format](../public/distdna/data-guide.txt).

## Scientific behavior

For each prompt, Linear compares sample means with squared Euclidean distance;
RBF computes exact biased empirical maximum mean discrepancy squared, retaining
within-arm diagonal terms and supporting unequal response counts. The kernel is
`exp(-squaredDistance / (2 * sigmaSquared))`. Average squared distances over the
ordered prompts, then minimize over separate references for each checkpoint.
Sort ascending; ties use checkpoint ID, then reference ID. Means are not
renormalized. The query's true identity and decoding settings are not inputs.

The encoder, pinned revision, preprocessing, per-response normalization, ordered
prompt IDs/digest, and frozen calibration must match. The bank uses independently
collected calibration prompts. Metadata validation cannot authenticate a client's
actual sampling/encoding procedure. Clients must collect independent query draws.

The score is a distance; the runner-up gap is a score difference. Neither is a
calibrated probability or an open-set rejection rule. An unenrolled model can be
assigned to the closest enrolled entry.

Galaxy uses deterministic classical multidimensional scaling of RBF squared
distances between each checkpoint's first enrollment fingerprint. It is a
visualization of enrollment geometry, not an accuracy measurement or an ancestry
claim. Full identification uses every separate reference, not these coordinates.

## Community Lab

Existing proposal rows migrate to `version='v1'`. New submissions, pending
verification, completed verification, recovery, and duplicate checks preserve
version and collection mode. `GET /api/proposals?version=v2&mode=raw` lists the
v2 queue; omitting parameters selects legacy v1/raw. A v1 enrollment/proposal
therefore does not imply a checkpoint has been enrolled for v2. Votes remain
attached to their proposal IDs. Existing email verification and CAPTCHA are retained.

## Provenance and verification

`server/distdna/data/example-provenance.json` records source hashes, enrollment
settings, pinned revisions, query identities and JSON serialization error. The
four example queries come from roster indices 0, 1, 6, 7, at T=0.9, p=0.7. Their
selection is fixed, not selected by outcomes. They rank against all 115 candidates
when the larger bank is configured. Small-bank results are not the paper's scores.

`benchmark.json` preserves 288 measured grid cells (four representations, two
readouts, 36 settings). It is used in provenance tests; the UI adds no benchmark
tab. LLM-DNA there is the paper's 512-coordinate shared-bank adaptation, not the
later native v1.0.1 rerun. Semantic scores average BGE and MPNet. The interactive
preview uses BGE only and does not claim to reproduce those aggregate scores.

Recreate the small bank and numerical oracle with
`scripts/export_distdna_research.py --run /path/to/wide-decoding-115-20260924`, then
run `python scripts/build_distdna_catalog.py` to generate its catalog and geometry.
The former requires the neighboring calibration and main-analysis archives.

```bash
npm run test:distdna
npm run typecheck:distdna
npm run build
```

Tests cover analytic distances, unequal response counts, prompt separation,
reference minimization, deterministic ties, protocol rejection, NumPy replay,
server API scoring and input limits, legacy database migration, version-specific
queues, verification and recovery. Email sending is mocked in Lab tests.

Full runtime banks are separate from the portable example. This change does not deploy a public service.
