import { VersionSelector } from '@/components/VersionSelector';
import { useVersion } from '@/contexts/VersionContext';
import { DistDnaAbout } from '@/distdna/About';
import { Card } from '@/components/Card';
import { Flow, type FlowStep } from '@/components/Flow';
import { CopyButton, ActionIcon, Tooltip, Button, Group, Text, Stack } from '@mantine/core';
// import { TextInput, Alert, Loader } from '@mantine/core';
// import { Turnstile, type TurnstileInstance } from '@marsidev/react-turnstile';

const extractionFlowSteps: FlowStep[] = [
    { icon: '⌨️', label: 'Random input text' },
    { icon: '📦', label: 'Sentence embedding model' },
    { icon: '🔗', label: 'Concatenation' },
    { icon: '🔀', label: 'Random projection' },
    { icon: '🧬', label: 'DNA', isFinal: true },
];

const BIBTEX = `@inproceedings{wu2026llmdna,
  title={LLM DNA: Tracing Model Evolution via Functional Representations},
  author={Wu, Zhaomin and Zhao, Haodong and Wang, Ziyang and Guo, Jizhou and Wang, Qian and He, Bingsheng},
  booktitle={The Fourteenth International Conference on Learning Representations},
  year={2026},
  url={https://openreview.net/forum?id=UIxHaAqFqQ},
  publisher={OpenReview}
}`;

interface Paper {
    title: string;
    authors: string;
    link: string;
    venue?: string;
}

const PAPERS: Paper[] = [
    {
        title: "LLM DNA: Tracing Model Evolution via Functional Representations",
        authors: "Zhaomin Wu, Haodong Zhao, Ziyang Wang, Jizhou Guo, Qian Wang, Bingsheng He",
        venue: "ICLR 2026 (Oral)",
        link: "https://openreview.net/forum?id=UIxHaAqFqQ"
    }
];

export default function AboutPage() {
    const {version}=useVersion();
    return (
        <div className="page about-page">
            <header className="page-header">
                <h1 className="page-title">{version==='v2'?'About DistDNA':'About LLM DNA'}</h1>
                <p className="page-subtitle">
                    {version==='v2'?'Identifying enrolled checkpoints from stochastic responses':'Tracing Model Evolution via Functional Representations'}
                </p>
            </header>

            <VersionSelector />
            {version==='v2' ? <DistDnaAbout /> : <>
            <div className="mt-8">
                <Card title="📄 Related Papers" titleClassName="text-accent">
                    <Stack gap="xl">
                        {PAPERS.map((paper, index) => (
                            <Group key={index} justify="space-between" align="flex-start" wrap="wrap" className="about-paper-row">
                                <Stack gap={4} style={{ flex: 1, minWidth: 0 }}>
                                    <Stack gap={4}>
                                        <Text size="lg" fw={600} ta="left" lh={1.3}>
                                            {paper.title}
                                        </Text>
                                        {paper.venue && (
                                            <span className="badge badge-info" style={{ whiteSpace: 'nowrap', alignSelf: 'flex-start' }}>
                                                {paper.venue}
                                            </span>
                                        )}
                                    </Stack>
                                    <Text ta="left" size="sm" fw={500}>
                                        {paper.authors}
                                    </Text>

                                </Stack>
                                <Button
                                    component="a"
                                    href={paper.link}
                                    target="_blank"
                                    variant="light"
                                    color="violet"
                                    size="xs"
                                    leftSection="📄"
                                    className="about-read-paper-btn"
                                >
                                    Read Paper
                                </Button>
                            </Group>
                        ))}
                    </Stack>
                </Card>
            </div>

            <div className="stats-grid mt-4">
                <Card title="🧬 Definition" titleClassName="text-accent">
                    <p>
                        We define the DNA of language models as a{' '}
                        <strong>bi-Lipschitz embedding</strong> in the function space of
                        models.
                    </p>
                </Card>

                <Card title="√x Theory" titleClassName="text-accent">
                    <p>
                        We prove the <strong>existence</strong> of such embedding for any
                        LLM with a finite context window.
                    </p>
                </Card>
            </div>

            <Card
                title="⚙️ Extraction Method: Random-projection based DNA extraction"
                className="mt-8"
            >
                <Flow steps={extractionFlowSteps} />
            </Card>

            <Card title="💡 Key Insights" className="mt-8">
                <div className="insights-grid">
                    <div className="insight-card">
                        <h4 className="insight-heading"><span className="insight-icon">👪</span> Family Clustering</h4>
                        <p>
                            Models from the same family (e.g., Llama, Qwen) naturally cluster
                            together, indicating shared foundational training.
                        </p>
                    </div>
                    <div className="insight-card">
                        <h4 className="insight-heading"><span className="insight-icon">🎯</span> Fine-tuning Drift</h4>
                        <p>
                            Instruct-tuned models drift away from their base versions, with
                            distance proportional to fine-tuning intensity.
                        </p>
                    </div>
                    <div className="insight-card">
                        <h4 className="insight-heading"><span className="insight-icon">🔗</span> Hidden Lineages</h4>
                        <p>
                            Some models cluster unexpectedly, revealing undisclosed training
                            data sharing or architecture borrowing.
                        </p>
                    </div>
                </div>
            </Card>

            <Card title="📝 Citation" className="mt-8">
                <Group justify="space-between" align="center" mb="xs">
                    <p style={{ margin: 0 }}>If you find this work useful, please cite our paper:</p>
                    <CopyButton value={BIBTEX} timeout={2000}>
                        {({ copied, copy }) => (
                            <Tooltip label={copied ? 'Copied' : 'Copy BibTeX'} withArrow position="left">
                                <ActionIcon
                                    color={copied ? 'teal' : 'gray'}
                                    variant="light"
                                    size="lg"
                                    onClick={copy}
                                >
                                    {copied ? '✓' : '📋'}
                                </ActionIcon>
                            </Tooltip>
                        )}
                    </CopyButton>
                </Group>
                <pre className="code-block" style={{ whiteSpace: 'pre-wrap', position: 'relative' }}>
                    {BIBTEX}
                </pre>
            </Card>

            <Card title="📬 Contact & Feedback" className="mt-8">
                <p>
                    We&apos;d love to hear from you! Whether you have questions,
                    suggestions, or want to report an issue:
                </p>
                <ul>
                    <li>
                        <strong>GitHub:</strong>{' '}
                        <a
                            href="https://github.com/JerryLife/RepTrace"
                            target="_blank"
                            rel="noreferrer"
                        >
                            JerryLife/RepTrace
                        </a>
                    </li>
                    <li>
                        <strong>Issues:</strong> Report bugs or request features on GitHub
                        Issues
                    </li>
                </ul>
                <div className="cta-box mt-6">
                    <h4>Want a model added?</h4>
                    <p>
                        Visit the <a href="/lab">Community Lab</a> to propose new models and
                        vote on which ones we should analyze next!
                    </p>
                    <a href="/lab" className="btn btn-primary mt-4">
                        Go to Community Lab →
                    </a>
                </div>
            </Card>

            </> }
        </div>
    );
}
