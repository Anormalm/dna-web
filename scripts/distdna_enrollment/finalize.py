"""Wait for a collection queue, build a versioned bank, check the web scorer.

This prepares local artifacts only. It does not restart services or deploy.
"""
import argparse
import fcntl
import os
import subprocess
import time
from pathlib import Path
from common import read, write
from merge import build


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--wait-hours', type=float, default=24)
    args = p.parse_args()
    run, output = args.run.resolve(), args.output.resolve()
    lock = (run / 'finalize.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    start = time.time()
    def state(status, **kw):
        write(run / 'finalize-status.json', dict(status=status, pid=os.getpid(),
              updated=time.time(), output=str(output), **kw))
    try:
        state('waiting_for_collection')
        while True:
            path = run / 'status.json'
            launch = read(path) if path.exists() else {}
            if launch.get('status') in ['complete', 'finished_with_failures']:
                break
            if launch.get('status') == 'stopped':
                raise RuntimeError('Collection stopped; inspect its status before merging')
            if time.time()-start > args.wait_hours*3600:
                raise TimeoutError('Collection wait limit reached')
            if launch.get('pid'):
                os.kill(launch['pid'], 0)
            time.sleep(20)
        state('building')
        build(run, output)
        state('checking_web_scorer')
        website = Path(__file__).resolve().parents[2]
        check = subprocess.run([str(website/'node_modules/.bin/tsx'),
                                str(website/'scripts/validate_distdna_bundle.ts'), str(output)],
                               cwd=website, capture_output=True, text=True, timeout=300, check=True)
        import json
        validation = json.loads(check.stdout.strip().splitlines()[-1])
        write(output / 'web-scorer-validation.json', validation)
        state('ready', collectionStatus=launch['status'], validation=validation)
        print(check.stdout, flush=True)
    except BaseException as e:
        state('failed', error=repr(e))
        raise


if __name__ == '__main__':
    main()
