"""Show collection progress without reading model outputs or changing a run."""
import argparse
import time
from pathlib import Path
from common import read


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    args = p.parse_args()
    spec = read(args.run / 'run.json')
    total = complete = 0
    for i, model in enumerate(spec['models']):
        path = args.run / 'models' / f'{i:03d}' / 'status.json'
        state = read(path) if path.exists() else dict(status='queued', records=0)
        total += state['records']
        complete += state['status'] == 'complete'
        age = f" updated {int(time.time()-state['updated'])}s ago" if 'updated' in state else ''
        print(f"{i:03d} {state['status']:12} {state['records']:3}/{spec['expectedPerModel']} {model['modelId']}{age}")
        if 'error' in state:
            print('   ', state['error'])
    print(f"{complete}/{len(spec['models'])} complete; {total}/{len(spec['models'])*spec['expectedPerModel']} responses")
    launch = args.run / 'status.json'
    if launch.exists():
        state = read(launch)
        print('Launcher:', state['status'], 'PID:', state['pid'])


if __name__ == '__main__':
    main()
