import { useEffect, useRef, useState } from 'react';
import {
    Alert,
    Badge,
    Button,
    Divider,
    FileButton,
    Group,
    Modal,
    Paper,
    Select,
    Stack,
    Table,
    Text,
    Title,
} from '@mantine/core';
import { useData } from '@/contexts/DataContext';
import type {
    Identification,
    Method,
    QueryBank,
    RankedCandidate,
} from '../../server/distdna/engine';

export function DistDnaWorkbench() {
    const loader = useData();
    const meta = loader.metadata!;
    const [method, setMethod] = useState<Method>('rbf');
    const [queryId, setQueryId] = useState<string | null>(meta.queries[0]?.id || null);
    const [upload, setUpload] = useState<QueryBank | null>(null);
    const [result, setResult] = useState<Identification | null>(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [detail, setDetail] = useState<RankedCandidate | null>(null);
    const [visible, setVisible] = useState(12);
    const active = useRef<AbortController | null>(null);
    const uploadEpoch = useRef(0);
    useEffect(
        () => () => {
            active.current?.abort();
            uploadEpoch.current++;
        },
        [],
    );
    const clear = () => {
        active.current?.abort();
        active.current = null;
        setBusy(false);
        setResult(null);
        setDetail(null);
        setError('');
        setVisible(12);
    };
    const options = upload
        ? upload.queries.map((q) => ({ value: q.id, label: q.id }))
        : meta.queries.map((q: { id: string; label: string }) => ({ value: q.id, label: q.label }));
    async function load(file: File | null) {
        clear();
        if (!file) return;
        setUpload(null);
        setQueryId(null);
        const epoch = ++uploadEpoch.current;
        try {
            if (file.size > 16 * 1024 * 1024)
                throw new Error('Choose a query bank smaller than 16 MB.');
            const bank = JSON.parse(await file.text());
            if (epoch !== uploadEpoch.current) return;
            if (
                bank.schema !== 'distdna.queries.v1' ||
                !Array.isArray(bank.queries) ||
                !bank.queries.length ||
                bank.queries.some((q: any) => typeof q.id !== 'string')
            )
                throw new Error('Choose a distdna.queries.v1 bank of encoded responses.');
            setUpload(bank);
            setQueryId(bank.queries[0].id);
        } catch (e) {
            if (epoch === uploadEpoch.current) setError((e as Error).message);
        }
    }
    async function run() {
        clear();
        if (!queryId) return;
        const controller = new AbortController();
        active.current = controller;
        setBusy(true);
        try {
            const response = await fetch('/api/v2/identify', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: controller.signal,
                body: JSON.stringify({ method, queryId, ...(upload ? { queries: upload } : {}) }),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Identification failed.');
            if (active.current === controller) setResult(data);
        } catch (e) {
            if (!controller.signal.aborted) setError((e as Error).message);
        } finally {
            if (active.current === controller) {
                active.current = null;
                setBusy(false);
            }
        }
    }
    return (
        <>
            <div className="workbench-layout">
                <Paper
                    p="lg"
                    radius="md"
                    withBorder
                    className="workbench-panel workbench-panel-config"
                >
                    <Text size="xs" fw={600} c="dimmed" mb="md" tt="uppercase">
                        Configuration
                    </Text>
                    <Divider mb="lg" />
                    <Stack gap="lg">
                        <Select
                            label="Query fingerprint"
                            placeholder="Choose a query…"
                            value={queryId}
                            data={options}
                            onChange={(v) => {
                                clear();
                                setQueryId(v);
                            }}
                            searchable
                        />
                        <Select
                            label="Fingerprint method"
                            value={method}
                            onChange={(v) => {
                                clear();
                                setMethod(v as Method);
                            }}
                            data={[
                                { value: 'rbf', label: 'DistDNA-RBF' },
                                { value: 'linear', label: 'DistDNA-Linear' },
                            ]}
                        />
                        <div>
                            <Text size="sm" fw={500} mb="xs">
                                Enrollment gallery
                            </Text>
                            <Text size="sm">{loader.models.length} checkpoint entries</Text>
                            <Text size="xs" c="dimmed">
                                {meta.referencesPerModel} references per entry ·{' '}
                                {meta.protocol.promptIds.length} shared prompts ·{' '}
                                {meta.responsesPerPrompt} responses per prompt
                            </Text>
                        </div>
                        <Button
                            fullWidth
                            color="violet"
                            onClick={run}
                            loading={busy}
                            disabled={!queryId}
                        >
                            Run identification
                        </Button>
                        <FileButton onChange={load} accept="application/json">
                            {(props) => (
                                <Button {...props} variant="light" fullWidth>
                                    Upload query embeddings
                                </Button>
                            )}
                        </FileButton>
                        {upload && (
                            <Button
                                variant="subtle"
                                size="xs"
                                onClick={() => {
                                    uploadEpoch.current++;
                                    clear();
                                    setUpload(null);
                                    setQueryId(meta.queries[0]?.id || null);
                                }}
                            >
                                Use saved examples
                            </Button>
                        )}
                        <Text size="xs" c="dimmed">
                            Uploads use the gallery’s encoder and ordered prompts. The server checks
                            compatibility before scoring.
                        </Text>
                        <Text
                            size="xs"
                            component="a"
                            href="/distdna/data-guide.txt"
                            target="_blank"
                        >
                            Query format and protocol
                        </Text>
                        {error && (
                            <Alert color="red" role="alert">
                                {error}
                            </Alert>
                        )}
                    </Stack>
                </Paper>
                <div className="workbench-panel workbench-panel-results" style={{ minWidth: 0 }}>
                    <Group justify="space-between" mb="md">
                        <Title order={4}>Identification results</Title>
                        <Badge variant="light">Nearest reference</Badge>
                    </Group>
                    {result ? (
                        <>
                            <Text size="sm" c="dimmed" mb="md">
                                {result.candidates.length} candidates · lower distance is closer
                                {result.margin !== null
                                    ? ` · runner-up gap ${result.margin.toFixed(5)}`
                                    : ''}
                                {result.tied ? ' · tied minimum; checkpoint ID breaks ties' : ''}
                            </Text>
                            <Stack gap="sm">
                                {result.candidates.slice(0, visible).map((c, i) => {
                                    const model = loader.getModelById(c.modelId);
                                    return (
                                        <Paper
                                            key={c.modelId}
                                            p="md"
                                            radius="md"
                                            withBorder
                                            className="workbench-result-card"
                                        >
                                            <Group
                                                gap="md"
                                                wrap="nowrap"
                                                className="workbench-result-main"
                                                style={{ flex: 1, minWidth: 0 }}
                                            >
                                                <div
                                                    className={`workbench-rank-badge ${i < 3 ? `workbench-rank-${i + 1}` : ''}`}
                                                    style={{ minWidth: 36 }}
                                                >
                                                    <Text fw={700} ta="center" size="sm">
                                                        {i + 1}
                                                    </Text>
                                                </div>
                                                <Stack gap={5} style={{ minWidth: 0, flex: 1 }}>
                                                    <Text
                                                        fw={600}
                                                        size="sm"
                                                        className="model-title-truncate"
                                                    >
                                                        {model?.name || c.modelId}
                                                    </Text>
                                                    <Group gap="xs">
                                                        <Badge size="xs" variant="light">
                                                            {model?.organization}
                                                        </Badge>
                                                        {model?.parameters && (
                                                            <Badge
                                                                size="xs"
                                                                variant="light"
                                                                color="gray"
                                                            >
                                                                {model.parameters}
                                                            </Badge>
                                                        )}
                                                    </Group>
                                                </Stack>
                                            </Group>
                                            <Group gap="sm" className="workbench-result-meta">
                                                <Stack gap={0} align="flex-end">
                                                    <Text
                                                        fw={700}
                                                        c="violet"
                                                        size="sm"
                                                        style={{
                                                            fontVariantNumeric: 'tabular-nums',
                                                        }}
                                                    >
                                                        {c.distance.toFixed(5)}
                                                    </Text>
                                                    <Text size="xs" c="dimmed">
                                                        Squared distance
                                                    </Text>
                                                </Stack>
                                                <Button
                                                    variant="light"
                                                    size="xs"
                                                    className="workbench-result-btn"
                                                    onClick={() => setDetail(c)}
                                                >
                                                    Details
                                                </Button>
                                            </Group>
                                        </Paper>
                                    );
                                })}
                            </Stack>
                            {visible < result.candidates.length && (
                                <Button
                                    mt="md"
                                    variant="subtle"
                                    onClick={() => setVisible((n) => n + 24)}
                                >
                                    Show more candidates
                                </Button>
                            )}
                            <Text size="xs" c="dimmed" mt="md">
                                Closed-set identification selects the closest enrolled checkpoint.
                                The distance and gap are scores, not calibrated probabilities or an
                                unseen-model rejection test.
                            </Text>
                        </>
                    ) : (
                        <Paper p="xl" radius="md" withBorder>
                            <Stack align="center" gap="sm" py="xl">
                                <Text size="xl">🧬</Text>
                                <Title order={4}>Ready to identify</Title>
                                <Text size="sm" c="dimmed" ta="center">
                                    Choose a saved query or upload encoded responses, then run
                                    identification.
                                </Text>
                            </Stack>
                        </Paper>
                    )}
                </div>
            </div>
            <Modal
                opened={!!detail}
                onClose={() => setDetail(null)}
                title="Fingerprint comparison"
                size="lg"
            >
                {detail && (
                    <Stack>
                        <Text fw={600} style={{ overflowWrap: 'anywhere' }}>
                            {detail.modelId}
                        </Text>
                        <Text size="xs" c="dimmed" style={{ overflowWrap: 'anywhere' }}>
                            Revision: {detail.revision}
                            <br />
                            Closest reference: {detail.referenceId}
                        </Text>
                        <Text size="sm">
                            Promptwise squared distances average to {detail.distance.toFixed(6)}.
                        </Text>
                        <Table>
                            <Table.Thead>
                                <Table.Tr>
                                    <Table.Th>Shared prompt</Table.Th>
                                    <Table.Th>Squared distance</Table.Th>
                                </Table.Tr>
                            </Table.Thead>
                            <Table.Tbody>
                                {detail.promptDistances.map((d, i) => (
                                    <Table.Tr key={i}>
                                        <Table.Td>{meta.protocol.promptIds[i]}</Table.Td>
                                        <Table.Td>{d.toFixed(6)}</Table.Td>
                                    </Table.Tr>
                                ))}
                            </Table.Tbody>
                        </Table>
                    </Stack>
                )}
            </Modal>
        </>
    );
}
