"""Run a bounded cached-model queue on idle GPUs, using cooperative locks."""
import argparse
import fcntl
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from common import read, verify_run, write


def availability():
    rows = subprocess.check_output(['nvidia-smi', '--query-gpu=index,uuid,memory.used,utilization.gpu',
                                    '--format=csv,noheader,nounits'], text=True)
    busy = subprocess.check_output(['nvidia-smi', '--query-compute-apps=gpu_uuid',
                                    '--format=csv,noheader,nounits'], text=True)
    return {int(i): uuid not in busy and int(mem) < 1024 and int(util) < 5
            for i, uuid, mem, util in [list(map(str.strip, line.split(','))) for line in rows.strip().splitlines()]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--gpus', default='0,1,2,3')
    args = p.parse_args()
    run = args.run.resolve()
    spec = verify_run(run)
    lock = (run / 'launch.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    gpus = [int(g) for g in args.gpus.split(',')]
    locks = Path(spec['research']) / '.local/experiments'
    active, attempted, idle = {}, set(), {}
    start = time.time()
    def state(status, **kw):
        write(run / 'status.json', dict(status=status, pid=os.getpid(), updated=time.time(),
              elapsedSeconds=time.time()-start, active={str(g): dict(index=i, pid=p.pid)
              for g, (i, p, _, _, _) in active.items()}, **kw))
    def stop(signum, frame):
        raise InterruptedError(f'Received signal {signum}')
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while True:
            for g, (i, child, handle, started, prior_elapsed) in list(active.items()):
                elapsed = prior_elapsed + time.time() - started
                path = run / 'models' / f'{i:03d}' / 'status.json'
                if child.poll() is None and elapsed > spec['perModelHours']*3600:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait()
                    previous = read(path) if path.exists() else dict(records=0)
                    write(path, dict(previous, status='failed', elapsedSeconds=elapsed,
                                     error='External per-model time limit reached', updated=time.time()))
                if child.poll() is not None:
                    previous = read(path) if path.exists() else dict(records=0)
                    if previous.get('status') not in ['complete', 'failed']:
                        write(path, dict(previous, status='failed', elapsedSeconds=elapsed,
                                         error=f'Worker exited with code {child.returncode}', updated=time.time()))
                    handle.close()
                    del active[g]
            statuses = {i: read(path) for i in range(len(spec['models']))
                        if (path := run / 'models' / f'{i:03d}' / 'status.json').exists()}
            done = {i for i, s in statuses.items() if s['status'] == 'complete'}
            pending = [i for i in range(len(spec['models'])) if i not in done and i not in attempted]
            used = sum(s['elapsedSeconds'] for s in statuses.values()) / 3600
            for i, _, _, started, prior_elapsed in active.values():
                recorded = statuses.get(i, {}).get('elapsedSeconds', 0)
                used += max(0, prior_elapsed + time.time()-started-recorded)/3600
            records = sum(s['records'] for s in statuses.values())
            if used > spec['maxGpuHours'] or shutil.disk_usage(run).free < 5*2**30:
                raise RuntimeError('Compute budget or disk reserve reached; partial records retained')
            if not pending and not active:
                state('complete' if len(done) == len(spec['models']) else 'finished_with_failures',
                      completedModels=len(done), records=records, gpuHours=used,
                      failed={str(i): s.get('error') for i, s in statuses.items() if s['status'] != 'complete'})
                break
            av = availability()
            for g in gpus:
                idle[g] = idle.get(g, 0)+1 if av.get(g, False) else 0
                if not pending or len(active) >= spec['maxParallelGpus']:
                    break
                if g in active or idle[g] < 2:
                    continue
                handle = (locks / f'.gpu-{g}.lock').open('a')
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    handle.close()
                    continue
                if not availability().get(g, False):
                    handle.close()
                    continue
                i = pending.pop(0)
                attempted.add(i)
                env = dict(os.environ, CUDA_VISIBLE_DEVICES=str(g), PYTHONNOUSERSITE='1',
                     OPENBLAS_NUM_THREADS='4', OMP_NUM_THREADS='4', MKL_NUM_THREADS='4',
                     HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
                with (run / f'model-{i:03d}.log').open('a') as stream:
                    child = subprocess.Popen([sys.executable, str(Path(__file__).with_name('collect.py')),
                         '--run', str(run), '--index', str(i)], env=env, stdin=subprocess.DEVNULL,
                         stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
                         pass_fds=(handle.fileno(),))
                active[g] = (i, child, handle, time.time(), statuses.get(i, {}).get('elapsedSeconds', 0))
            state('collecting' if active else 'waiting_for_idle_gpu', completedModels=len(done),
                  records=records, expectedRecords=len(spec['models'])*800, gpuHours=used)
            time.sleep(10)
    except BaseException as e:
        for _, child, _, _, _ in active.values():
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        for _, child, handle, _, _ in active.values():
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            handle.close()
        state('stopped', error=repr(e))
        raise


if __name__ == '__main__':
    main()
