"""Operational wrapper for archived collectors: dynamic budgets and cooperative stop.
The archived sampling implementation is verified and loaded unchanged.
"""
import argparse,importlib.util,json,signal,threading,sys
from pathlib import Path
from common import read,sha,write
from admin_runtime import reserve,reconcile_cost,ControlError

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--job',type=Path,required=True);p.add_argument('--key',required=True);p.add_argument('--billing',choices=['openrouter','byok'],default='openrouter');a=p.parse_args()
    spec=read(a.job/'chat-run.json');source=next(Path(s) for s in spec['sourceSha256'] if Path(s).name=='chat_queue.py')
    if sha(source)!=spec['sourceSha256'][str(source)]:raise ValueError('Archived collector checksum mismatch')
    module_spec=importlib.util.spec_from_file_location('archived_chat_collector',source);collector=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(collector)
    halted=threading.Event();signal.signal(signal.SIGTERM,lambda *_:halted.set())
    original_payload=collector.build_payload
    def payload(s,row,prompt):
        value=original_payload(s,row,prompt);value['usage']={'include':True};return value
    collector.build_payload=payload
    def reserve_cost(run,rid,amount,cap):
        if halted.is_set():raise ControlError('Job paused by administrator.')
        try:reserve(run,a.key,a.billing,rid,amount*1.05)
        except ControlError as exc:halted.set();raise collector.ApiFailure('admin_pause',str(exc))
    def record_cost(run,rid,usage):
        reconcile_cost(run,rid,usage,spec['endpoint']['pricing'])
        if a.billing=='byok' and usage.get('is_byok') is not True:
            halted.set();raise collector.ApiFailure('byok','Response did not confirm BYOK. Check prioritized key and disabled shared-capacity fallback.')
    collector.reserve_cost=reserve_cost;collector.record_cost=record_cost
    # Run the four protocol preflight observations sequentially for BYOK, so a
    # misconfigured account sends at most one request before pausing.
    if a.billing=='byok':
        original_completion=collector.completion;gate=threading.Lock()
        def completion(*args,**kwargs):
            with gate:
                if halted.is_set():raise collector.ApiFailure('admin_pause','Job paused; completed records retained')
                return original_completion(*args,**kwargs)
        collector.completion=completion
    write(a.job/'operational-wrapper.json',{'source':str(Path(__file__).resolve()),'sha256':sha(Path(__file__)),'billing':a.billing,'samplingSource':str(source),'controls':'Dynamic global spend reservations; stop preserves completed response records'})
    collector.collect(a.run,a.job,float('inf'))
if __name__=='__main__':main()
