import { afterEach, describe, expect, it } from 'vitest';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { defaultDataDir, loadRepository } from '../server/distdna/repository';

const directories: string[] = [];
afterEach(() => directories.splice(0).forEach((p) => rmSync(p, { recursive: true, force: true })));
function fixture(n = 9000) {
    const directory = mkdtempSync(join(tmpdir(), 'distdna-large-bank-'));
    directories.push(directory);
    const protocol = { ...JSON.parse(readFileSync(join(defaultDataDir, 'example-queries.json'), 'utf8')).protocol,
        dimension: 1, promptIds: ['test-prompt'], normalized: false };
    const bytes = Buffer.alloc(n * 4);
    for (let i = 0; i < n; i++) bytes.writeFloatLE(i / n, i * 4);
    writeFileSync(join(directory, 'enrollment.f32'), bytes);
    const manifest = { schema: 'distdna.binary.v1', protocol, shape: [n, 1, 1, 1],
        sha256: createHash('sha256').update(bytes).digest('hex'),
        references: Array.from({ length: n }, (_, row) => ({ id: `ref-${row}`, modelId: 'test/model', revision: 'pinned', row })) };
    const write = (name: string, value: unknown) => writeFileSync(join(directory, name), JSON.stringify(value));
    write('bank.json', manifest);
    write('catalog.json', { models: [{ id: 'test/model' }], metadata: { version: 'v2', protocol } });
    write('example-queries.json', { schema: 'distdna.queries.v1', title: 'Independent query', protocol,
        queries: [{ id: 'query', samples: [[[0.5]]] }] });
    return { directory, manifest, write };
}

describe('large binary enrollment banks', () => {
    it('loads more than 8192 rows and preserves little-endian reference values', () => {
        const { directory } = fixture();
        const repo = loadRepository(directory);
        const refs = repo.galleryFor('test/model').references;
        expect(refs).toHaveLength(9000);
        expect(refs[8999].samples[0][0][0]).toBeCloseTo(8999 / 9000, 7);
    });
    it('rejects duplicate row mappings before scoring', () => {
        const { directory, manifest, write } = fixture(2);
        manifest.references[1].row = 0;
        write('bank.json', manifest);
        expect(() => loadRepository(directory)).toThrow('row mapping');
    });
    it('rejects a modified binary bank', () => {
        const { directory } = fixture(2);
        writeFileSync(join(directory, 'enrollment.f32'), Buffer.alloc(8));
        expect(() => loadRepository(directory)).toThrow('checksum');
    });
});
