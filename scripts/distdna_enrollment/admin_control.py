"""Authenticated server adapter; JSON stdin only, never a shell command."""
import argparse,json,sys
from pathlib import Path
from admin_estimates import estimate

def dispatch(run,action,request,actor=0):
    if action=='estimate':return estimate(run,request)
    raise ValueError('Unknown administrator action.')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,required=True);args=parser.parse_args()
    try:
        payload=json.load(sys.stdin)
        print(json.dumps(dispatch(args.run.resolve(),payload['action'],payload.get('input',{}),payload.get('actor',0)),allow_nan=False))
    except ValueError as exc:
        # Only intentionally authored validation errors are exposed; remote exceptions are generic.
        print(json.dumps({'error':str(exc) if str(exc).startswith(('Enter ','Choose ','Unknown ')) else 'Model inspection failed. Check access, exact model ID and protocol support.','status':400}));sys.exit(1)
    except Exception:
        print(json.dumps({'error':'Model inspection is unavailable. Check credentials and provider availability.','status':503}));sys.exit(1)
