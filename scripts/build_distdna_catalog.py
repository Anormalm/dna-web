#!/usr/bin/env python3
"""Build server catalog and deterministic classical-MDS coordinates from enrollment.
No query outcomes enter the visualization. Full banks stay in ignored runtime data.
"""
import argparse, hashlib, json, re
from pathlib import Path
import numpy as np
from scipy.spatial.distance import cdist

def write(path,value):
    path.write_text(json.dumps(value,separators=(',',':'),allow_nan=False)+'\n')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('server/distdna/data'))
    parser.add_argument('--run',type=Path,help='Optional archived 115-entry run; exports all eight references per identity')
    args=parser.parse_args();out=args.output;out.mkdir(parents=True,exist_ok=True)
    demo=Path('server/distdna/data');g=json.loads((demo/'example-gallery.json').read_text());q=json.loads((demo/'example-queries.json').read_text())
    if args.run:
        spec=json.loads((args.run/'protocol.json').read_text());collection=json.loads((args.run/'enrollment_collection.json').read_text())
        ids=spec['model_ids'];n=len(ids);train=np.load(args.run/'fits/enrollment-bge.npy',mmap_mode='r')
        assert train.shape==(8*n,20,4,768)
        raw=np.asarray(train,dtype='<f4');raw.tofile(out/'enrollment.f32');del raw
        refs=[dict(id=f'{m}:train_{s:02d}',modelId=m,revision=collection['metadata']['model_revisions'][m],row=s*n+i) for s in range(8) for i,m in enumerate(ids)]
        write(out/'bank.json',dict(schema='distdna.binary.v1',protocol=g['protocol'],shape=list(train.shape),references=refs,sha256=hashlib.sha256((out/'enrollment.f32').read_bytes()).hexdigest()))
        # Retain the same four independent query examples; every query ranks all 115 identities.
        write(out/'example-queries.json',q)
        first=np.asarray(train[:n],dtype=np.float64);title='115-checkpoint enrolled gallery';count=8
    else:
        ids=list(dict.fromkeys(r['modelId'] for r in g['references']));refs=g['references'];first=np.array([next(r['samples'] for r in refs if r['modelId']==m) for m in ids]);title=g['title'];count=2
    n,t,r,d=first.shape;sigma2=g['protocol']['sigmaSquared'];matrix=np.zeros((n,n))
    for i in range(t):
        a=first[:,i].reshape(n*r,d)
        kernel=np.exp(-cdist(a,a,'sqeuclidean')/(2*sigma2)).reshape(n,r,n,r).mean(axis=(1,3))
        diag=np.diag(kernel);matrix+=(diag[:,None]+diag[None,:]-2*kernel)/t
    matrix=np.maximum(matrix,0);np.fill_diagonal(matrix,0)
    h=np.eye(n)-np.ones((n,n))/n;values,vectors=np.linalg.eigh(-.5*h@matrix@h);ix=np.argsort(values)[-2:][::-1]
    xy=vectors[:,ix]*np.sqrt(np.maximum(values[ix],0))
    for j in range(2):
        if xy[np.argmax(np.abs(xy[:,j])),j]<0:xy[:,j]*=-1
    models=[]
    for i,m in enumerate(ids):
        org,name=m.split('/',1);family=(re.match('[A-Za-z]+',name).group() if re.match('[A-Za-z]+',name) else org);param=re.search(r'(\d+(?:\.\d+)?[BbMm])',name)
        models.append(dict(id=m,name=name,organization=org,family=family,parameters=param.group() if param else None,isInstruct=bool(re.search(r'instruct|chat|it$',name,re.I)),signature=None,x=float(xy[i,0]),y=float(xy[i,1]),revision=next(r['revision'] for r in refs if r['modelId']==m)))
    write(out/'catalog.json',dict(models=models,metadata=dict(version='v2',title=title,mode='raw',referenceCount=len(refs),referencesPerModel=count,protocol=g['protocol'],queries=[dict(id=x['id'],label=f"Held-out query {i+1}") for i,x in enumerate(q['queries'])],projection='Classical MDS of promptwise RBF MMD squared, using the first enrollment setting of each checkpoint. Coordinates are a visualization; identification uses all separately enrolled references.',readout='nearest-reference',methods=['rbf','linear'],responsesPerPrompt=4,encoding=('little-endian float32' if args.run else 'JSON decimal embeddings'),dataRevision=hashlib.sha256((json.dumps(g['protocol'],sort_keys=True)+(out/'bank.json').read_text()+(out/'example-queries.json').read_text() if args.run else json.dumps(g,sort_keys=True)+json.dumps(q,sort_keys=True)).encode()).hexdigest())))
    write(out/'geometry-distances.json',dict(ids=ids,squaredRbf=matrix.tolist()))
    print(f'Exported {len(refs)} references for {n} entries, first-reference MDS geometry, {len(q["queries"])} held-out queries to {out}')

if __name__=='__main__':main()
