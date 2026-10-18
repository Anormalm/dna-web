import { Group, SegmentedControl, Text } from '@mantine/core';
import { useVersion, type DnaVersion } from '@/contexts/VersionContext';
export function VersionSelector() {
    const { version, setVersion } = useVersion();
    return (
        <Group gap="sm" mb="md" wrap="wrap">
            <Text size="sm" c="dimmed">
                Version
            </Text>
            <SegmentedControl
                size="xs"
                color="violet"
                value={version}
                onChange={(v) => setVersion(v as DnaVersion)}
                aria-label="DNA version"
                data={[
                    { value: 'v1', label: 'v1 · LLM DNA' },
                    { value: 'v2', label: 'v2 · DistDNA' },
                ]}
            />
        </Group>
    );
}
