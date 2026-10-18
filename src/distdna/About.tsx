import { Card } from '@/components/Card';
import { Flow } from '@/components/Flow';
import { useData } from '@/contexts/DataContext';
export function DistDnaAbout() {
    const loader = useData();
    const meta = loader.metadata!;
    return (
        <>
            <div className="stats-grid mt-4">
                <Card title="🧬 Behavioral fingerprint" titleClassName="text-accent">
                    <p>
                        DistDNA represents a checkpoint through its responses to a fixed set of
                        prompts. Repeated responses capture stochastic behavior within each prompt.
                    </p>
                </Card>
                <Card title="🎯 Identification" titleClassName="text-accent">
                    <p>
                        Compare a query with separately enrolled decoding settings and select the
                        closest checkpoint. The current gallery contains{' '}
                        <strong>{loader.models.length} entries</strong>, with{' '}
                        {meta.referencesPerModel} references per entry.
                    </p>
                </Card>
            </div>
            <Card title="⚙️ Extraction and identification" className="mt-8">
                <Flow
                    steps={[
                        { icon: '⌨️', label: 'Shared prompts' },
                        { icon: '💬', label: 'Repeated responses' },
                        { icon: '📦', label: 'Frozen encoder' },
                        { icon: '🧬', label: 'Promptwise fingerprint' },
                        { icon: '🎯', label: 'Nearest reference', isFinal: true },
                    ]}
                />
            </Card>
            <Card title="💡 Comparing fingerprints" className="mt-8">
                <div className="insights-grid">
                    <div className="insight-card">
                        <h4 className="insight-heading">DistDNA-RBF</h4>
                        <p>
                            Compare empirical response distributions with Gaussian-kernel maximum
                            mean discrepancy. Average squared distances across the shared prompts.
                        </p>
                    </div>
                    <div className="insight-card">
                        <h4 className="insight-heading">DistDNA-Linear</h4>
                        <p>
                            Compare the mean response embeddings for each prompt. Each response is
                            normalized before averaging; the promptwise means retain their
                            magnitude.
                        </p>
                    </div>
                    <div className="insight-card">
                        <h4 className="insight-heading">Separate enrollment settings</h4>
                        <p>
                            Retain each enrollment fingerprint separately. Each candidate’s score is
                            its minimum distance over these references.
                        </p>
                    </div>
                </div>
            </Card>
            <Card title="🔭 Reading the Galaxy" className="mt-8">
                <p>{meta.projection} The Workbench scores the full fingerprints on the server.</p>
            </Card>
            <Card title="📦 Current collection" className="mt-8">
                <p>
                    {meta.protocol.promptIds.length} shared prompts, four responses per prompt, and
                    a frozen {meta.protocol.encoder} encoder. Saved queries use independently
                    collected responses.
                </p>
                <p>
                    Identification assumes the query checkpoint is enrolled. A new checkpoint can be
                    proposed in the <a href="/lab?version=v2">Community Lab</a>.
                </p>
            </Card>
        </>
    );
}
