"""Read a backend-only OpenRouter key without evaluating shell configuration."""
import os
from pathlib import Path
import stat


def openrouter_key(run):
    configured = os.getenv('OPENROUTER_API_KEY', '').strip()
    if configured:
        return configured
    path = Path(run) / 'openrouter.env'
    if not path.exists():
        return None
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ValueError('Set openrouter.env permissions to 600 before use')
    result = None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:]
        name, separator, value = line.partition('=')
        if not separator or name.strip() != 'OPENROUTER_API_KEY':
            raise ValueError('openrouter.env must contain only OPENROUTER_API_KEY=...')
        if result is not None:
            raise ValueError('Duplicate OPENROUTER_API_KEY in config')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if not value or any(ch.isspace() for ch in value):
            raise ValueError('OpenRouter key is empty or contains whitespace')
        result = value
    return result
