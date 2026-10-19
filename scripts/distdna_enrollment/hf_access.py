"""Load private Hub credentials and retry access-blocked jobs after authorization."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
from common import write


def hf_token(run):
    token = os.environ.get('HF_TOKEN', '').strip()
    path = Path(run)/'huggingface.env'
    # An edited run-specific file takes precedence over the controller's old env.
    if path.exists():
        if path.stat().st_mode & 0o077:
            raise ValueError('Set huggingface.env permissions to 600 before use')
        values = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[7:]
            name, separator, value = line.partition('=')
            if not separator or name.strip() != 'HF_TOKEN':
                raise ValueError('huggingface.env must contain only HF_TOKEN=...')
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            values.append(value)
        if len(values) > 1:
            raise ValueError('Duplicate HF_TOKEN assignment')
        if values and values[0]:
            token = values[0]
    if not token:
        return None
    if not token.startswith('hf_') or any(c.isspace() for c in token):
        raise ValueError('Hugging Face token format is invalid')
    return token


def authenticate(token):
    req = urllib.request.Request('https://huggingface.co/api/whoami-v2',
                                  headers={'Authorization': 'Bearer '+token})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            data = json.load(response)
        if not data.get('name'):
            raise ValueError('Hub authentication returned no account')
    except urllib.error.HTTPError as exc:
        raise ValueError(f'Hub token authentication failed (HTTP {exc.code})') from None
    except (urllib.error.URLError, TimeoutError):
        raise ValueError('Hub authentication temporarily unavailable') from None


def refresh(run, db, state):
    """Check edits promptly; recheck unchanged access every 30 minutes."""
    now = time.time()
    try:
        token = hf_token(run)
        if not token:
            return
        fingerprint = hashlib.sha256(token.encode()).hexdigest()
        interval = 1800 if state.get('authenticated') else 300
        if state.get('fingerprint') == fingerprint and now-state.get('checkedAt',0) < interval:
            return
        state.update(fingerprint=fingerprint,checkedAt=now,authenticated=False)
        authenticate(token)
        state['authenticated'] = True
        os.environ['HF_TOKEN'] = token
        rows = db.execute("SELECT key,detail FROM jobs WHERE mode='raw' AND state='blocked'").fetchall()
        retried = 0
        for row in rows:
            detail = row['detail'] or ''
            if any(marker in detail for marker in ('Cannot access gated repo', '401 Client Error', '403 Client Error', 'Repository Not Found')):
                db.execute("UPDATE jobs SET state='pending',detail=?,updated=? WHERE key=?",
                    ('Retrying Hub access using private authorized token',now,row['key']))
                retried += 1
        db.commit()
        write(Path(run)/'hf-access-status.json',dict(configured=True,authenticated=True,
              checkedAt=now,retriedJobs=retried,credential='Loaded privately; value omitted'))
    except ValueError as exc:
        write(Path(run)/'hf-access-status.json',dict(authenticated=False,checkedAt=now,error=str(exc)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--check',action='store_true')
    args = parser.parse_args()
    if args.check:
        token = hf_token(args.run)
        if not token:
            raise SystemExit('No token configured')
        authenticate(token)
        print('Hugging Face authentication succeeded; token omitted')
        return
    token = getpass.getpass('Hugging Face read token (hidden): ').strip()
    if not token.startswith('hf_') or any(c.isspace() for c in token):
        raise SystemExit('Invalid token format')
    authenticate(token)
    args.run.mkdir(parents=True,exist_ok=True)
    path = args.run/'huggingface.env'
    temp = path.with_name(path.name+f'.{os.getpid()}.tmp')
    fd = os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:
        stream.write('HF_TOKEN='+token+'\n')
    temp.replace(path)
    print('Private token configured; the running queue will detect it')


if __name__ == '__main__':
    main()
