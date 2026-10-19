import { useEffect, useReducer, useState } from 'react';
import { Alert, Badge, Button, Container, Group, NumberInput, Paper, Progress, Select, SimpleGrid, Stack, Text, TextInput, Title } from '@mantine/core';
import { demoReducer, demoSpent, initialDemo, isDemoRunning, type DemoMode, type DemoQuote } from '../admin/demo';

const money = (n: number) => `$${n.toFixed(2)}`;
const labels = { stopped: 'Ready to start', downloading: 'Downloading weights', collecting: 'Collecting responses', encoding: 'Building fingerprint', cleanup: 'Removing weights', complete: 'Complete', paused: 'Stopped', budget_paused: 'Budget paused' };

function DemoPanel() {
    const [state, dispatch] = useReducer(demoReducer, undefined, initialDemo);
    const [mode, setMode] = useState<DemoMode>('raw');
    const [model, setModel] = useState('Qwen/Qwen2.5-0.5B');
    const [byok, setByok] = useState(true);
    const [cap, setCap] = useState<string | number>(25);
    const [quote, setQuote] = useState<DemoQuote | null>(null);
    const [added, setAdded] = useState(false);
    const running = state.jobs.some(j => isDemoRunning(j.phase));
    useEffect(() => {
        if (!running) return;
        const timer = setInterval(() => dispatch({ type: 'tick' }), 1000);
        return () => clearInterval(timer);
    }, [running]);
    const spent = demoSpent(state);
    function selectMode(next: DemoMode) {
        setMode(next); setModel(next === 'raw' ? 'Qwen/Qwen2.5-0.5B' : 'openai/gpt-4o-mini'); setQuote(null); setAdded(false);
    }
    return <Stack gap="lg">
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
            {[['Mock API budget', money(state.cap)], ['Simulated API usage', money(spent)], ['Demo jobs complete', `${state.jobs.filter(j => j.phase === 'complete').length} / ${state.jobs.length}`]].map(([label, value]) =>
                <Paper key={label} withBorder p="lg" radius="md"><Text c="dimmed" size="sm">{label}</Text><Title order={2} mt="xs">{value}</Title></Paper>)}
        </SimpleGrid>
        <Paper withBorder p="lg" radius="md"><Stack>
            <Title order={3}>1. Set the example budget</Title>
            <Group align="end"><NumberInput label="Mock API budget ceiling (USD)" value={cap} min={0} max={100000} decimalScale={2} onChange={setCap} />
                <Button variant="light" disabled={cap === '' || !Number.isFinite(Number(cap)) || Number(cap) < 0} onClick={() => dispatch({ type: 'budget', cap: Number(cap) })}>Save mock budget</Button></Group>
            <Text size="sm" c="dimmed">Remaining: {money(Math.max(0, state.cap - spent))}. Try $0.40 to see an API job pause at the limit, then increase the budget and resume it. Local GPU jobs use no API budget.</Text>
        </Stack></Paper>
        <Paper withBorder p="lg" radius="md"><Stack>
            <Title order={3}>2. Inspect and add a model</Title>
            <Group><Button variant={mode === 'raw' ? 'filled' : 'light'} onClick={() => selectMode('raw')}>Open-weight example</Button>
                <Button variant={mode === 'chat' ? 'filled' : 'light'} onClick={() => selectMode('chat')}>API / BYOK example</Button></Group>
            <Group align="end"><TextInput label={mode === 'raw' ? 'Example Hugging Face model ID' : 'Example OpenRouter model ID'} value={model} onChange={e => { setModel(e.currentTarget.value); setQuote(null); setAdded(false); }} style={{ flex: 1, minWidth: 210 }} />
                {mode === 'chat' && <Select label="Example billing" value={byok ? 'byok' : 'openrouter'} data={[{ value: 'byok', label: 'OpenRouter BYOK' }, { value: 'openrouter', label: 'OpenRouter account' }]} onChange={v => { setByok(v === 'byok'); setQuote(null); setAdded(false); }} />}
                <Button disabled={!model.trim()} onClick={() => { setQuote({ mode, model: model.trim(), byok }); setAdded(false); }}>Inspect example</Button></Group>
            <Text c="dimmed" size="sm">These are fixed illustrative estimates, including when you edit the model name. No model metadata or provider prices are fetched.</Text>
            {quote && <Paper withBorder p="md" bg="gray.0"><Stack gap="sm">
                <Group justify="space-between"><Text fw={600} style={{ overflowWrap: 'anywhere' }}>{quote.model}</Text><Badge color="violet">Example estimate</Badge></Group>
                <SimpleGrid cols={{ base: 1, sm: 3 }}>
                    {(quote.mode === 'raw' ? [
                        ['Compute', '1 local GPU'], ['Temporary storage', '2 GiB staging · 1 GiB download'], ['Estimated duration', '30–55 minutes'],
                    ] : [
                        ['Billing', quote.byok ? 'Provider via OpenRouter BYOK' : 'OpenRouter account'], ['Example total allowance', '$1.60'], ['Estimated duration', '8–15 minutes'],
                    ]).map(([label, value]) => <div key={label}><Text c="dimmed" size="xs">{label}</Text><Text fw={500}>{value}</Text></div>)}
                </SimpleGrid>
                <Text size="sm">800 responses: 640 enrollment + 160 query. The live workflow also runs 4 preflight requests. This demo skips preflight and accelerates collection to about 25 seconds.</Text>
                <Text size="sm" c="dimmed">{quote.mode === 'raw' ? 'After a successful run, remove downloaded weights and keep the response archive and fingerprint (example: 12 MiB).' : quote.byok ? 'In a real run, configure the provider key in OpenRouter first. This mock assumes that configuration is complete; $1.60 represents an illustrative combined allowance, not a provider price quote.' : 'In a real run, charges come from provider usage reports. This mock increases usage by $0.08 for each block of 40 responses.'}</Text>
                <Button disabled={added} onClick={() => { dispatch({ type: 'add', quote }); setAdded(true); }} style={{ alignSelf: 'flex-start' }}>{added ? 'Added to demo queue' : 'Add mock job'}</Button>
            </Stack></Paper>}
        </Stack></Paper>
        <Paper withBorder p="lg" radius="md"><Stack>
            <Group justify="space-between"><Title order={3}>3. Start, stop, and resume</Title><Badge color="violet">Accelerated simulation</Badge></Group>
            {!state.jobs.length && <Text c="dimmed">Your added models appear here, stopped and ready to start.</Text>}
            {state.jobs.map(job => <Paper key={job.id} withBorder p="md" radius="md" data-testid={`demo-job-${job.id}`}><Stack gap="sm">
                <Group justify="space-between"><div><Text fw={600} style={{ overflowWrap: 'anywhere' }}>{job.model}</Text><Text size="xs" c="dimmed">{job.mode === 'raw' ? 'Open-weight · local GPU' : job.byok ? 'API · OpenRouter BYOK' : 'API · OpenRouter account'} · Mock job #{job.id}</Text></div>
                    <Badge color={job.phase === 'complete' ? 'green' : job.phase === 'budget_paused' ? 'orange' : isDemoRunning(job.phase) ? 'blue' : 'gray'}>{labels[job.phase]}</Badge></Group>
                <Progress value={job.records / 8} aria-label={`${job.model} response progress`} />
                <Group justify="space-between"><Text size="sm">{job.records} / 800 responses</Text><Text size="sm">{job.mode === 'chat' ? `Simulated usage: ${money(job.spent)}` : job.phase === 'complete' ? 'Weights removed · 12 MiB retained (example)' : 'Up to 2 GiB temporary storage (example)'}</Text></Group>
                {job.phase === 'budget_paused' && <Alert color="orange">Mock budget reached. Increase the ceiling above, then Start to resume.</Alert>}
                {job.phase === 'complete' ? <Alert color="green">Example fingerprint ready. In the live workflow, validated results can be published to Galaxy. This demo adds nothing to the real catalog.</Alert> : <Group>
                    <Button size="sm" disabled={isDemoRunning(job.phase)} onClick={() => dispatch({ type: 'start', id: job.id })}>Start</Button>
                    <Button size="sm" variant="light" color="red" disabled={!isDemoRunning(job.phase)} onClick={() => dispatch({ type: 'stop', id: job.id })}>Stop</Button>
                    <Text c="dimmed" size="xs">Stopping keeps progress. Start resumes it.</Text>
                </Group>}
            </Stack></Paper>)}
        </Stack></Paper>
        <Paper withBorder p="lg" radius="md"><Title order={3} mb="sm">Demo activity</Title><Stack gap="xs" role="log" aria-label="Demo activity">{state.events.map((event, i) => <Text key={`${i}-${event}`} size="sm" c={i === 0 ? undefined : 'dimmed'}>{event}</Text>)}</Stack></Paper>
    </Stack>;
}

export default function AdminDemoPage() {
    const [generation, setGeneration] = useState(0);
    return <Container size="lg" py="xl"><Stack gap="lg">
        <Group justify="space-between"><div><Title order={1}>Admin workflow demo</Title><Text c="dimmed">Estimate → add → run → fingerprint</Text></div><Group><Button variant="light" onClick={() => setGeneration(v => v + 1)}>Reset demo</Button><Button component="a" href="/admin" variant="default">Live admin</Button></Group></Group>
        <Alert color="violet" title="Demo only · no real jobs or charges">All models, estimates, progress, and costs below are simulated in this browser. No keys or GitHub sign-in are required for the demo. Reset or refresh to start over. Live administration still requires GitHub sign-in.</Alert>
        <DemoPanel key={generation} />
    </Stack></Container>;
}
