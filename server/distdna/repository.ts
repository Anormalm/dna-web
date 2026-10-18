import { readFileSync, existsSync, openSync, closeSync, readSync, fstatSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
    parseGallery,
    parseQueries,
    assertCompatible,
    type Gallery,
    type Reference,
    type Protocol,
} from './engine.js';

export const defaultDataDir = fileURLToPath(new URL('./data/', import.meta.url));
export const dataDirectory = () => resolve(process.env.DISTDNA_DATA_DIR || defaultDataDir);
export function readCatalog(directory = dataDirectory()) {
    const catalog = JSON.parse(readFileSync(join(directory, 'catalog.json'), 'utf8'));
    if (
        catalog.metadata?.version !== 'v2' ||
        !Array.isArray(catalog.models) ||
        !catalog.models.length
    )
        throw new Error('Invalid DistDNA catalog. Rebuild the server data.');
    return catalog;
}
/** Data directory is operator-controlled. Requests never select paths or enroll new references. */
export function loadRepository(directory: string) {
    const catalog = readCatalog(directory);
    const queries = parseQueries(
        JSON.parse(readFileSync(join(directory, 'example-queries.json'), 'utf8')),
    );
    let references: Reference[];
    let protocol: Protocol;
    let samples: (ref: Reference) => number[][][];
    if (existsSync(join(directory, 'bank.json'))) {
        const manifest = JSON.parse(readFileSync(join(directory, 'bank.json'), 'utf8'));
        if (manifest.schema !== 'distdna.binary.v1') throw new Error('Invalid binary bank schema.');
        const [n, t, r, d] = manifest.shape;
        if (
            ![n, t, r, d].every(Number.isSafeInteger) ||
            n < 1 ||
            n > 65536 ||
            t < 1 ||
            t > 128 ||
            r < 1 ||
            r > 64 ||
            d < 1 ||
            d > 4096
        )
            throw new Error('Invalid binary bank dimensions.');
        const bankPath = join(directory, 'enrollment.f32');
        const expectedBytes = n * t * r * d * 4;
        if (!Number.isSafeInteger(expectedBytes)) throw new Error('Invalid bank size.');
        // Hash in bounded chunks: the full catalog exceeds Node's single-Buffer limit.
        const fd = openSync(bankPath, 'r');
        try {
            if (fstatSync(fd).size !== expectedBytes)
                throw new Error('Enrollment checksum or dimensions do not match.');
            const hash = createHash('sha256');
            const chunk = Buffer.allocUnsafe(Math.min(expectedBytes, 8 * 1024 * 1024));
            let position = 0;
            while (position < expectedBytes) {
                const count = readSync(fd, chunk, 0, Math.min(chunk.length, expectedBytes - position), position);
                if (!count) throw new Error('Truncated enrollment bank.');
                hash.update(chunk.subarray(0, count));
                position += count;
            }
            if (hash.digest('hex') !== manifest.sha256)
                throw new Error('Enrollment checksum or dimensions do not match.');
        } finally {
            closeSync(fd);
        }
        protocol = manifest.protocol;
        if (protocol.dimension !== d || protocol.promptIds.length !== t)
            throw new Error('Binary dimensions disagree with protocol.');
        references = manifest.references;
        if (
            !Array.isArray(references) ||
            references.length !== n ||
            new Set(references.map((x) => x.id)).size !== n
        )
            throw new Error('Invalid enrollment reference manifest.');
        const rows = references.map((ref) => (ref as Reference & { row: number }).row);
        if (new Set(rows).size !== n || rows.some((row) => !Number.isSafeInteger(row) || row < 0 || row >= n))
            throw new Error('Invalid enrollment row mapping.');
        const rowBytes = t * r * d * 4;
        // Read only the selected reference; immutable bundles cannot change under a worker.
        samples = (ref) => {
            const row = (ref as Reference & { row: number }).row;
            const bytes = Buffer.allocUnsafe(rowBytes);
            const fd = openSync(bankPath, 'r');
            try {
                let offset = 0;
                while (offset < rowBytes) {
                    const count = readSync(fd, bytes, offset, rowBytes - offset, row * rowBytes + offset);
                    if (!count) throw new Error('Truncated enrollment row.');
                    offset += count;
                }
            } finally {
                closeSync(fd);
            }
            return Array.from({ length: t }, (_, i) =>
                Array.from({ length: r }, (_, j) =>
                    Array.from({ length: d }, (_, k) => bytes.readFloatLE(((i * r + j) * d + k) * 4)),
                ),
            );
        };
    } else {
        const gallery = parseGallery(
            JSON.parse(readFileSync(join(directory, 'example-gallery.json'), 'utf8')),
        );
        protocol = gallery.protocol;
        references = gallery.references;
        samples = (ref) => ref.samples;
    }
    assertCompatible(protocol, queries.protocol);
    assertCompatible(protocol, catalog.metadata.protocol);
    const modelIds: string[] = catalog.models.map((m: { id: string }) => m.id);
    if (
        new Set(modelIds).size !== modelIds.length ||
        references.some((r) => !modelIds.includes(r.modelId)) ||
        modelIds.some((m) => !references.some((r) => r.modelId === m))
    )
        throw new Error('Catalog does not match enrolled identities.');
    const referenceIds = new Set(references.map((r) => r.id));
    if (queries.queries.some((q) => referenceIds.has(q.id)))
        throw new Error('Saved query IDs overlap enrollment.');
    return {
        catalog,
        queries,
        protocol,
        referenceIds,
        modelIds,
        galleryFor(modelId: string): Gallery {
            return {
                schema: 'distdna.gallery.v1',
                title: catalog.metadata.title,
                description: 'Separately enrolled decoding settings',
                protocol,
                references: references
                    .filter((r) => r.modelId === modelId)
                    .map((r) => ({ ...r, samples: samples(r) })),
            };
        },
    };
}
