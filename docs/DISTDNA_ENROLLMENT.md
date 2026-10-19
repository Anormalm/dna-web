# Collect the DistDNA Galaxy catalog

The collector inventories every entry in both legacy catalogs and creates new
repeated-response enrollment data. Legacy compressed LLM-DNA vectors are not
inputs to the DistDNA encoder. Enrollment and querying use a frozen 20-prompt
schedule, four responses per prompt, a pinned normalized BGE encoder and fixed
calibration from an existing research bank.

## Prerequisites

Use a DistDNA research checkout with the archived generation implementation,
its tested Python environment, an exported compatible reference bank and its
`enrollment_collection.json`. These research inputs are required; the small
example bank shipped with the website is for API demonstration. Keep run data,
model staging and credentials outside the source checkout. The web app itself
needs no provider credentials.

Ordinary raw checkpoints use the archived float32 generation protocol. Native
quantized checkpoints require a separately verified runtime. There is no blanket
conversion to a different quantization or substitution of unavailable model IDs.

## Full-catalog collection

`scripts/distdna_enrollment/full_catalog.py` inventories every raw and chat row.
It reuses existing enrollment and observes previously launched runs without
collecting duplicates. Native raw checkpoints are pinned to immutable revisions,
inspected, downloaded when necessary, and scheduled continuously on idle GPUs.
Large float32 models reserve multiple GPUs. There is no model-count limit or
24-GPU-hour cutoff. Each checkpoint collects 640 enrollment responses, 160
independent validation responses, and four separate preflight responses.

Use `start --research ... --base ... --collection ... --raw ... --chat ...
--cache ... --cache ... --inherit ... --output ... --staging ...`, then `resume
--run ...` after an interruption. `status --run ...` reports progress; the SQLite
queue and `inventory.json` retain unavailable, gated, unsupported, and failed
entries with reasons. Existing shared caches are read-only. After each successful
run, the controller verifies response and embedding checksums, array shapes and
normalization, frozen inputs and checkpoint provenance, and waits for the worker
to exit. It then deletes that run's owned staging downloads immediately, regardless
of free disk space. Responses, embeddings and copied checkpoint provenance remain
intact, with a `weights-cleanup.json` receipt. Failed or incomplete runs keep their
weights for retries; paths outside the owned staging directory are never removed.

Configure the API provider in `<run>/openrouter.env`, with permissions `600`:

```dotenv
OPENROUTER_API_KEY=your_key_here
```

This file belongs outside the website source and frontend environment. It is
parsed as data, never executed as shell code. The controller never substitutes
an unrelated OpenAI key. Chat entries are checked against the OpenRouter model
catalog; unsupported decoding controls and retired IDs remain explicit blockers.
`chat_queue.py prepare --run ...` freezes a compatible endpoint for each available
entry. `chat_queue.py launch --run ... --max-cost-usd 75` runs the full prepared
queue, with two models and four requests per model in flight. Routing uses a
single selected provider, disables fallback, and requires temperature/top-p
support. Native-chat data remains separate from the raw gallery. Credentials
are read by the backend collector only and never saved in collection artifacts.

Each chat entry has eight enrollment settings and two independent query settings,
using the same 20 prompts and four responses per prompt. Requests contain one user
message, at most 128 output tokens, and a seed where supported. Empty completions
are retained. Chat encoding uses the pinned BGE encoder on CPU; the RBF bandwidth
is explicitly transferred from the frozen raw calibration. Served model IDs,
provider routes, response IDs, settings, usage and observed revisions are recorded.
These are service observations, not independently pinned closed-source weights.

The spending ledger reserves an estimated upper request cost before dispatch and
settles it from returned usage. Unknown/failed calls retain their reservation.
The collection stops scheduling on authentication, credit or budget failures.
Model/parameter errors remain visible in the inventory; it never silently switches
model identity or drops an unsupported decoding control.

Launch collectors with the experiment virtual environment's literal executable
path (for example `/path/to/.venv-torch26/bin/python`), without resolving its
symlink. The chat controller verifies the encoder imports before collection and
resumes each job with its archived collection code. A completed response bank
that failed solely because the encoder dependency was missing is retried for
encoding, with a bounded retry count and no regenerated responses.

Native bitsandbytes checkpoints use a separate frozen loader and an isolated,
pinned dependency directory. Their saved quantization and compute dtype are
retained and recorded per checkpoint; ordinary raw checkpoints continue using
float32. A `nativeRuntimes.bitsandbytes` entry in the queue configuration names
the dependency path, version, archived worker directory and file checksums. The
controller reuses complete native caches or downloads pinned weights, and shares
the same cooperative GPU scheduling. Other quantization formats still need
their own verified runtimes. Non-finite values in descriptive config metadata
are represented as strings; original checkpoint configuration files stay exact.

`full_bundle.py --run ... --output ... --wait` builds one final bundle when all
raw jobs reach a recorded terminal state. It verifies completed shards, preserves
base references, includes a complete coverage report, and validates saved queries
against the production scorer. Failed/blocked entries are not enrolled. The new
full-catalog bundle uses fixed enrollment-only landmarks for MDS display; exact
identification retains all references. The previous preview uses full classical
MDS. Neither display is an accuracy benchmark.

Binary banks are checksum-verified in chunks and references read on demand, so a
full catalog can exceed Node's single-Buffer size limit. New bundles are versioned
and are not automatically deployed to the public website.

## Start and resume

The Python path must be the literal virtual-environment executable. An example
full-catalog invocation follows; replace the input and storage paths with your
research archive and writable run directories. Each `--inherit` argument is
optional and adopts an existing compatible collection without duplicating it.

```bash
export PYTHONNOUSERSITE=1
/path/to/DistDNA/.venv-torch26/bin/python scripts/distdna_enrollment/full_catalog.py start \
  --research /path/to/DistDNA \
  --base /path/to/exported-distdna-bank \
  --collection /path/to/enrollment_collection.json \
  --raw public/dna_database.json \
  --chat public/dna_database_chat.json \
  --cache /path/to/huggingface/hub \
  --output /path/to/full-catalog-run \
  --staging /path/to/owned-checkpoint-staging
/path/to/DistDNA/.venv-torch26/bin/python scripts/distdna_enrollment/full_catalog.py resume \
  --run /path/to/full-catalog-run
/path/to/DistDNA/.venv-torch26/bin/python scripts/distdna_enrollment/full_catalog.py status \
  --run /path/to/full-catalog-run
```

Run `start` once; use `resume` after an interruption. The controller occupies the
foreground, so use another terminal for status. It cooperatively claims idle GPUs
and uses a per-model 72-hour guard. Large models can reserve multiple GPUs while
waiting for enough cards, avoiding starvation. Generation and encoding artifacts
are resumable and source/input hashes prevent mixing collection implementations.

## Hugging Face access

Create a private run-specific credential with the hidden-input helper:

```bash
/path/to/DistDNA/.venv-torch26/bin/python scripts/distdna_enrollment/hf_access.py \
  --run /path/to/full-catalog-run
```

The helper validates the token and atomically writes `<run>/huggingface.env` with
permissions `600`. Alternatively create that file privately with a single
`HF_TOKEN=...` assignment, or export `HF_TOKEN` to the controller's environment.
Never put the token in Vite variables, browser code or a checked-in file.

The running controller detects the private file, checks Hub authentication and
retries access-blocked raw entries. It checks unchanged authorization every 30
minutes, allowing newly granted repository access to take effect. This does not
grant access to gated repositories: the account needs the relevant approvals.
`hf-access-status.json` records only authentication status and retry counts.

## Recovery without changing the sampling protocol

Each checkpoint download runs in a fresh process with Xet disabled and HTTP
transfer enabled. This isolates downloader failures between repositories while
retaining the pinned revision and checksum checks. Downloads have up to three
attempts with increasing delays. Chat workers reuse their archived source,
provider route, completed responses and spending ledger; temporary 429/5xx or
transport failures retry after cooldown, up to four model attempts. Authentication,
budget and incompatible-request failures remain explicit.

Loader repairs use a new immutable worker archive and a separate job directory
(`jobRevision`), preserving prior attempts. Single-device quantized models are
validated from their actual GPU parameter placement even when Transformers omits
`hf_device_map`.

For read-only bundle import, a legacy run can supply `archived-inputs.json` with a
`files` mapping from original source paths to archived copies. Every copy must
match the original frozen checksum. This supports verification after an editable
checkout changes; collector execution still uses strict original-path checks.
Response files and original completion/run manifests are preserved.

## Validation

```bash
PYTHONNOUSERSITE=1 /path/to/DistDNA/.venv-torch26/bin/python \
  -m unittest discover -s test -p 'test_distdna_*.py'
```

Tests cover protocol freezing, restart behavior, provider routing, spend
reservations, binary bank assembly, native-runtime checks and private token
loading. Full coverage is a collection outcome, not a property of a passing test
suite: consult `inventory.json` and the coverage report for unfinished or blocked
entries. Native-chat collection is implemented separately; this PR's bundle
builder publishes raw enrollment only. Integrating a native-chat serving bank
requires its own protocol-aware build and validation.
