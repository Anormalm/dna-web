import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Badge, Button, Checkbox, Divider, Group, NumberInput, Paper, Select, SimpleGrid, Stack, Tabs, Text, TextInput, Title } from '@mantine/core';

export async function mutate(csrf: string, path: string, data: unknown) {
    const r = await fetch('/api/admin/'+path, { method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify(data) });
    const value = await r.json(); if(!r.ok) throw new Error(value.error || 'Operation failed.'); return value;
}
const usd = (v: number | null | undefined) => v == null ? 'Unavailable' : '$'+v.toFixed(v < 1 ? 4 : 2);
const gb = (v: number | undefined) => v == null ? 'Unknown' : (v/2**30).toFixed(2)+' GiB';
const duration = (v: any) => v == null ? 'No comparable runs yet' : `${(v.low*60).toFixed(0)}–${(v.high*60).toFixed(0)} min${v.samples ? ` · ${v.samples} comparable runs` : ' · historical API estimate'}`;

export function JobActions({csrf,job}:{csrf:string;job:{key:string;state:string}}) {
    const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('');
    const completed=['complete','chat_complete','enrolled'].includes(job.state);
    const stopped=['paused','blocked','blocked_protocol','failed','chat_failed','download_failed','unavailable','awaiting_credentials'].includes(job.state);
    async function act(operation:string){setBusy(true);setError('');setMessage('');try{const d=await mutate(csrf,'job-action',{key:job.key,operation});setMessage(d.message||'Updated.');}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
    if(completed)return <Text size="xs" c="dimmed">Complete</Text>;
    return <Stack gap={4}><Group gap="xs" wrap="nowrap">
        <Button size="xs" variant="light" loading={busy} disabled={busy || !stopped} onClick={()=>void act('start')}>Start</Button>
        <Button size="xs" color="red" variant="subtle" disabled={busy || job.state==='paused'} onClick={()=>void act('stop')}>Stop</Button>
    </Group>{error&&<Text size="xs" c="red" role="alert">{error}</Text>}{message&&<Text size="xs" c="dimmed">{message}</Text>}</Stack>;
}

export function AdminControls({csrf}:{csrf:string}) {
    const [controls,setControls]=useState<any>(null),[error,setError]=useState(''),[notice,setNotice]=useState('');
    const [cap,setCap]=useState<string|number>(''),[version,setVersion]=useState(0),[saving,setSaving]=useState(false),dirty=useRef(false);
    const [mode,setMode]=useState<string|null>('raw'),[model,setModel]=useState(''),[billing,setBilling]=useState<string|null>('openrouter'),[provider,setProvider]=useState<string|null>(null);
    const [quote,setQuote]=useState<any>(null),[busy,setBusy]=useState(false),[confirmed,setConfirmed]=useState(false);
    const [added,setAdded]=useState<string|null>(null),inspectSequence=useRef(0);
    const refresh=useCallback(async()=>{try{const r=await fetch('/api/admin/controls');const d=await r.json();if(!r.ok)throw new Error(d.error||'Controls unavailable.');setControls(d);if(!dirty.current){setCap(d.budget.capUsd);setVersion(d.budget.version);}}catch(e){setError((e as Error).message);}},[]);
    useEffect(()=>{void refresh();const id=setInterval(()=>void refresh(),10_000);return()=>clearInterval(id);},[refresh]);
    const invalidate=()=>{inspectSequence.current++;setQuote(null);setAdded(null);setNotice('');setError('');};
    async function inspect(){const seq=++inspectSequence.current;setBusy(true);setError('');setQuote(null);setAdded(null);try{const d=await mutate(csrf,'estimate',{mode,model:model.trim(),billing,provider});if(seq===inspectSequence.current)setQuote(d);}catch(e){if(seq===inspectSequence.current)setError((e as Error).message);}finally{setBusy(false);}}
    async function add(){setBusy(true);setError('');try{const d=await mutate(csrf,'jobs',{quoteId:quote.quoteId,byokConfirmed:confirmed});setAdded(d.key);setNotice(d.alreadyExists?'This model is already in the queue. Use its controls below.':'Model added stopped. Start it when ready.');}catch(e){setError((e as Error).message);}finally{setBusy(false);void refresh();}}
    async function action(path:string,data:unknown){setSaving(true);setError('');try{const d=await mutate(csrf,path,data);setNotice(d.message||'Budget saved. The next API request uses this ceiling.');if(path==='budget')dirty.current=false;await refresh();}catch(e){setError((e as Error).message);}finally{setSaving(false);}}
    return <Stack gap="lg">
        {error&&<Alert color="red" role="alert" withCloseButton onClose={()=>setError('')}>{error}</Alert>}
        {notice&&<Alert color="green" withCloseButton onClose={()=>setNotice('')}>{notice}</Alert>}
        <Paper withBorder p="lg" radius="md"><Stack>
            <Group justify="space-between"><Title order={3}>Collection controls</Title><Badge color={controls?.supervisorOnline?'green':'yellow'}>{controls?.supervisorOnline?'Collector connected':'Collector unavailable'}</Badge></Group>
            <SimpleGrid cols={{base:1,sm:2}}>{[['raw','Open-weight · local GPUs'],['chat','API models · OpenRouter / BYOK']].map(([key,label])=><Paper key={key} withBorder p="md"><Stack gap="sm">
                <Group justify="space-between"><Text fw={600}>{label}</Text><Badge color={controls?.modes[key]?'gray':'blue'}>{controls?.modes[key]?'Stopped':'Scheduling enabled'}</Badge></Group>
                <Group><Button size="sm" variant="light" disabled={saving||!controls?.modes[key]} onClick={()=>void action('collection-action',{mode:key,operation:'start'})}>Resume collection</Button>
                <Button size="sm" variant="light" color="red" disabled={saving||!controls||Boolean(controls.modes[key])} onClick={()=>void action('collection-action',{mode:key,operation:'stop'})}>Stop collection</Button></Group>
                <Text size="xs" c="dimmed">{key==='raw'?'GPU workers release memory after shutdown. Current downloads may finish; completed responses are kept.':'Stops new requests. Calls already in flight can finish and be billed.'}</Text>
            </Stack></Paper>)}</SimpleGrid>
            <Divider/>
            <Group align="end"><NumberInput disabled={!controls} label="API budget ceiling (USD, entire run)" value={cap} onChange={v=>{dirty.current=true;setCap(v);}} min={0} max={100000} decimalScale={2} style={{minWidth:230}}/>
                <Button loading={saving} disabled={!controls||cap===''||!Number.isFinite(Number(cap))} onClick={()=>void action('budget',{capUsd:Number(cap),version})}>Save budget</Button>
                <Button variant="subtle" onClick={()=>{dirty.current=false;void refresh();}}>Reload budget</Button></Group>
            <Text size="sm">Committed or reserved: {usd(controls?.budget.committedUsd)} · Remaining: {usd(controls?.budget.remainingUsd)} · BYOK inference estimate: {usd(controls?.budget.byokInferenceEstimateUsd)}</Text>
            <Text size="xs" c="dimmed">This ceiling covers OpenRouter charges, BYOK inference estimates and unresolved reservations. It does not include local GPU costs. Saving a lower ceiling preserves existing charges and blocks new requests when exhausted.</Text>
        </Stack></Paper>
        <Paper withBorder p="lg" radius="md"><Stack>
            <Title order={3}>Add a model</Title>
            <Tabs value={mode} onChange={v=>{setMode(v);setProvider(null);invalidate();}}><Tabs.List>
                <Tabs.Tab value="raw">Open-weight models</Tabs.Tab><Tabs.Tab value="chat">API models</Tabs.Tab>
            </Tabs.List></Tabs>
            <Text size="sm" c="dimmed">{mode==='raw'?'Inspect a Hugging Face checkpoint before downloading weights. Runtime estimates use comparable completed jobs.':'Use an exact OpenRouter model ID. API models include closed-source and hosted open-weight models.'}</Text>
            <Group align="end"><TextInput label={mode==='raw'?'Hugging Face model ID':'OpenRouter model ID'} placeholder={mode==='raw'?'Qwen/Qwen2.5-0.5B':'anthropic/claude-sonnet-4'} value={model} onChange={e=>{setModel(e.currentTarget.value);setProvider(null);invalidate();}} style={{flex:1,minWidth:240}}/>
                {mode==='chat'&&<Select label="Billing" value={billing} onChange={v=>{setBilling(v);setConfirmed(false);invalidate();}} data={[{value:'openrouter',label:'OpenRouter account'},{value:'byok',label:'OpenRouter BYOK'}]}/>}
                <Button loading={busy} disabled={!model.trim()} onClick={()=>void inspect()}>Inspect &amp; estimate</Button></Group>
            <Text size="xs" c="dimmed">{mode==='raw'?`Hugging Face credential: ${controls?.huggingFaceConfigured?'configured':'public repositories only until configured'}`:`OpenRouter credential: ${controls?.openrouterConfigured?'configured on server':'not configured'}`}. Inspection generates no model responses.</Text>
            {mode==='chat'&&billing==='byok'&&<Paper withBorder p="md"><Stack gap="sm">
                <Text size="sm">Configure your provider key in <a href="https://openrouter.ai/settings/integrations" target="_blank" rel="noreferrer">OpenRouter BYOK settings</a>. Make it prioritized and disable shared-capacity fallback. Provider keys stay with OpenRouter.</Text>
                <Checkbox label="I configured a prioritized key for the selected provider and disabled shared-capacity fallback" checked={confirmed} onChange={e=>setConfirmed(e.currentTarget.checked)}/>
                <Text size="xs" c="dimmed">Each response must confirm BYOK. A failed configuration check stops further calls; that first request can still incur a charge.</Text>
            </Stack></Paper>}
            {quote&&<Stack>
                <Group><Badge>Estimate · valid for 15 minutes</Badge>{quote.existing&&<Badge color="gray">Already in queue</Badge>}</Group>
                <SimpleGrid cols={{base:1,sm:2,lg:3}}>{(mode==='raw'?[
                    ['Native weights',gb(quote.weightBytes)],['GPUs required',String(quote.gpuCount)],['Download',gb(quote.downloadBytes)],
                    ['Temporary staging',gb(quote.temporaryDiskBytes)],['Available disk',gb(quote.freeDiskBytes)],['Collection runtime',duration(quote.runtimeHours)],
                ]:[['Provider',quote.providerName],['Expected inference cost',usd(quote.expectedInferenceUsd)],['One-pass reservation',usd(quote.onePassReservationUsd+(quote.byokFeeAllowanceUsd||0))],['Input / 1M tokens',usd(quote.inputUsdPerMillion)],['Output / 1M tokens',usd(quote.outputUsdPerMillion)],['Collection runtime',duration(quote.runtimeHours)]]).map(([label,value])=><Paper key={label} withBorder p="sm"><Text size="xs" c="dimmed">{label}</Text><Text fw={600}>{value}</Text></Paper>)}</SimpleGrid>
                <Text size="sm">800 responses + 4 preflight checks · Retained-data allowance: {gb(quote.retainedBytesEstimate)}</Text>
                {quote.revision&&<Text size="xs" c="dimmed">Pinned revision: {quote.revision}</Text>}
                {quote.providers&&<Select label="Provider route (inspect again after changing)" data={quote.providers} value={provider||quote.provider} onChange={v=>{setProvider(v);invalidate();}}/>}
                {quote.warnings.map((s:string)=><Text size="xs" c="dimmed" key={s}>{s}</Text>)}
                {quote.cleanup&&<Text size="xs" c="dimmed">{quote.cleanup}</Text>}
                <Group><Button variant="light" loading={busy} disabled={!!added||(mode==='chat'&&billing==='byok'&&!confirmed)} onClick={()=>void add()}>{quote.existing?'Use queued model':'Add model · keep stopped'}</Button>
                    {added&&<JobActions csrf={csrf} job={{key:added,state:quote.existing?.state||'paused'}}/>}</Group>
            </Stack>}
        </Stack></Paper>
    </Stack>;
}
