import { useEffect, useMemo, useState } from 'react';
import { Alert, Badge, Button, Container, Group, Paper, Progress, Select, SimpleGrid, Stack, Table, Text, TextInput, Title } from '@mantine/core';
import type { ModelRow } from '../../server/admin/telemetry';

type AdminSession = { configured: boolean; authenticated: boolean; csrf?: string; user?: { login: string } };
type Snapshot = { available: boolean; reason?: string; observedAt: number; models: ModelRow[]; budget?: {
    available: boolean; reportedUsd: number; reservedUsd: number; operationalCapUsd: number | null;
}; counts?: Record<string, Record<string, number>> };
const money = (value: number | null | undefined) => value == null ? '—' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value);
const stamp = (value: number | null | undefined) => value ? new Date(value).toLocaleString() : '—';

export default function AdminPage() {
    const [session, setSession] = useState<AdminSession | null>(null);
    const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
    const [connection, setConnection] = useState('Connecting');
    const [error, setError] = useState('');
    const [search, setSearch] = useState('');
    const [mode, setMode] = useState<string | null>('all');
    const [state, setState] = useState<string | null>('all');
    const [limit, setLimit] = useState(50);
    const [clock, setClock] = useState(Date.now());
    useEffect(() => {
        document.title = 'Admin | LLM DNA Explorer';
        const abort = new AbortController();
        fetch('/api/admin/session', { signal: abort.signal }).then(async r => {
            if (!r.ok) throw new Error('Administrator service is unavailable.');
            setSession(await r.json());
        }).catch(e => { if (!abort.signal.aborted) setError(e.message); });
        return () => abort.abort();
    }, []);
    useEffect(() => {
        if (!session?.authenticated) return;
        const events = new EventSource('/api/admin/events');
        events.addEventListener('snapshot', event => {
            try { setSnapshot(JSON.parse((event as MessageEvent).data)); setConnection('Live'); setError(''); }
            catch { setError('Unable to read the latest update.'); }
        });
        events.addEventListener('expired', () => { events.close(); setSession({ configured: true, authenticated: false }); setSnapshot(null); });
        events.onerror = () => setConnection('Reconnecting');
        const timer = setInterval(() => setClock(Date.now()), 1000);
        return () => { events.close(); clearInterval(timer); };
    }, [session?.authenticated]);
    const rows = useMemo(() => (snapshot?.models || []).filter(m =>
        (mode === 'all' || m.mode === mode) && (state === 'all' || m.state === state) &&
        `${m.model} ${m.provider || ''}`.toLowerCase().includes(search.toLowerCase()))
        .sort((a, b) => Number(['running', 'chat_running'].includes(b.state)) - Number(['running', 'chat_running'].includes(a.state)) || a.model.localeCompare(b.model)), [snapshot, mode, state, search]);
    const states = ['all', ...new Set((snapshot?.models || []).map(m => m.state))].sort();
    const budget = snapshot?.budget;
    const stale = snapshot && clock - snapshot.observedAt > 10_000;
    async function logout() {
        try {
            const r = await fetch('/api/admin/logout', { method: 'POST', headers: { 'X-CSRF-Token': session?.csrf || '' } });
            if (!r.ok) throw new Error('Unable to sign out. Please retry.');
            setSnapshot(null); setSession({ configured: true, authenticated: false });
        } catch (e) { setError((e as Error).message); }
    }
    return <Container size="xl" py="xl"><Stack gap="lg">
        <Group justify="space-between"><div><Title order={1}>Administration</Title><Text c="dimmed">Live enrollment and collection status</Text></div>
            {session?.authenticated && <Group><Text size="sm">{session.user?.login}</Text><Button variant="default" onClick={logout}>Sign out</Button></Group>}
        </Group>
        {error && <Alert color="red">{error}</Alert>}
        {!session && !error && <Text>Checking administrator session…</Text>}
        {session && !session.authenticated && <Paper withBorder p="xl" radius="md"><Stack align="flex-start">
            <Title order={3}>Administrator sign-in</Title>
            <Text>Use an authorized GitHub account to view collection status.</Text>
            {new URLSearchParams(location.search).has('signin') && <Alert color="red">Sign-in could not be completed. Check that you used an authorized account and try again.</Alert>}
            {!session.configured && <Alert color="yellow">GitHub administrator sign-in has not been configured on this server.</Alert>}
            <Button component="a" href="/api/admin/github/start" disabled={!session.configured}>Sign in with GitHub</Button>
        </Stack></Paper>}
        {session?.authenticated && <>
            <Group justify="space-between"><Badge color={connection === 'Live' && !stale ? 'green' : 'yellow'}>{stale ? 'Updates delayed' : connection}</Badge>
                <Text size="sm" c="dimmed">Updated {stamp(snapshot?.observedAt)}</Text></Group>
            {snapshot && !snapshot.available && <Alert color="yellow">{snapshot.reason}</Alert>}
            <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
                {[['API charges reported', money(budget?.available ? budget.reportedUsd : null)],
                  ['Outstanding reservations', money(budget?.available ? budget.reservedUsd : null)],
                  ['API controller limit', money(budget?.operationalCapUsd)],
                  ['Active collection jobs', String((snapshot?.models || []).filter(m => ['running', 'chat_running'].includes(m.state) && m.workerAlive).length)]].map(([label, value]) =>
                    <Paper key={label} withBorder p="lg" radius="md"><Text size="sm" c="dimmed">{label}</Text><Title order={2} mt="xs">{value}</Title></Paper>)}
            </SimpleGrid>
            <Text size="sm" c="dimmed">Charges reflect provider usage reports. Reservations cover calls whose final charge is not yet known. Local GPU costs are separate.</Text>
            <Group><TextInput label="Find a model or provider" value={search} onChange={e => { setSearch(e.currentTarget.value); setLimit(50); }} style={{ flex: 1 }} />
                <Select label="Collection" value={mode} onChange={v => { setMode(v); setLimit(50); }} data={[{ value: 'all', label: 'All' }, { value: 'raw', label: 'Raw · local' }, { value: 'chat', label: 'Chat · API' }]} />
                <Select label="Status" value={state} onChange={v => { setState(v); setLimit(50); }} data={states.map(s => ({ value: s, label: s === 'all' ? 'All' : s.replace(/_/g, ' ') }))} /></Group>
            <Text size="sm">{rows.length} matching entries · {snapshot?.models.length || 0} in the collection queue</Text>
            <Table.ScrollContainer minWidth={900}><Table striped highlightOnHover withTableBorder>
                <Table.Thead><Table.Tr><Table.Th>Model</Table.Th><Table.Th>Collection / provider</Table.Th><Table.Th>State</Table.Th><Table.Th>Responses</Table.Th><Table.Th>Size / GPUs</Table.Th><Table.Th>Worker updated</Table.Th></Table.Tr></Table.Thead>
                <Table.Tbody>{rows.slice(0, limit).map(m => <Table.Tr key={m.key}>
                    <Table.Td><Text size="sm" fw={500}>{m.model}</Text></Table.Td>
                    <Table.Td><Text size="sm">{m.mode === 'raw' ? 'Local plain-text' : 'Native chat'}</Text><Text size="xs" c="dimmed">{m.provider}</Text></Table.Td>
                    <Table.Td><Badge color={m.state.endsWith('complete') || m.state === 'enrolled' ? 'green' : m.workerAlive ? 'blue' : 'gray'}>{m.state.replace(/_/g, ' ')}</Badge>{m.workerAlive === false && <Text size="xs" c="red">Worker unavailable</Text>}</Table.Td>
                    <Table.Td><Text size="sm">{m.records == null ? '—' : `${m.records} / ${m.expected}`}</Text>{m.records != null && <Progress value={100 * m.records / m.expected} size="xs" mt={4} />}</Table.Td>
                    <Table.Td><Text size="sm">{m.parameters == null ? 'Size unknown' : `${(m.parameters / 1e9).toFixed(2)}B`}</Text><Text size="xs" c="dimmed">{m.gpuCount == null ? (m.mode === 'chat' ? 'API' : 'GPU requirement unknown') : `${m.gpuCount} GPU${m.gpuCount === 1 ? '' : 's'}`}</Text></Table.Td>
                    <Table.Td><Text size="xs">{stamp(m.workerUpdated)}</Text></Table.Td>
                </Table.Tr>)}</Table.Tbody>
            </Table></Table.ScrollContainer>
            {rows.length > limit && <Button variant="light" onClick={() => setLimit(n => n + 100)}>Show more models</Button>}
        </>}
    </Stack></Container>;
}
