"""Summarize scoped saved evidence; never rerun discovery or student code."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
REPO=Path(__file__).resolve().parents[3];OUT=REPO/'result/projections-aggregates'
def read(path):return json.loads(path.read_text())
def receipt(path):return {'file':str(path.relative_to(REPO)),'sha256':sha256(path.read_bytes()).hexdigest()}
def main():
 methods={};failures=[];logs=[]
 for log in sorted(OUT.rglob('*.log'),key=lambda p:p.stat().st_mtime):
  body=log.read_text();logs.append({**receipt(log),'summary':re.findall(r'^Ran \d+ tests?.*|^FAILED.*|^OK.*',body,re.M)})
  for line in body.splitlines():
   m=re.match(r'test_\w+ \(([^)]+)\) \.\.\. (ok|FAIL|ERROR|skipped.*)$',line)
   if m:
    key,status=m.groups();methods[key]={'status':status,'evidence':str(log.relative_to(REPO))}
    if status in ('FAIL','ERROR'):failures.append({'test':key,'status':status,'evidence':str(log.relative_to(REPO))})
 assert methods and all(v['status']=='ok' for v in methods.values()),methods
 public=[]
 for folder in [f'public-{label}-{mode}' for label in ('tasks','max') for mode in ('test','run')]+['public-control']:
  r=read(OUT/folder/'result.json');public.append({'folder':str((OUT/folder).relative_to(REPO)),**{k:r[k] for k in ('sourceHash','score','maxScore','unavailablePoints')},'checks':[c['status'] for c in r['checks']]})
 batches=[]
 for n in (1,2):
  r=read(OUT/f'batch-{n}/batch-result.json');batches.append({'folder':f'result/projections-aggregates/batch-{n}','counts':r['counts'],'scores':[s['score'] for s in r['submissions']],'cacheHits':[s['cacheHit'] for s in r['submissions']]})
 sql=[]
 for p in sorted(OUT.glob('sql-*.json')):
  r=read(p);assert r['status']=='EXECUTED' and r['integration']['cleanup'];sql.append({**receipt(p),'status':r['status'],'cleanup':r['integration']['cleanup']})
 mutations=[]
 for label in ('multiply','case','count','sum','having','join','distinct'):
  p=OUT/('mutation-'+label+'.json');r=read(p);assert r['status']=='EXECUTED';mutations.append({**receipt(p),'label':label,'assessment':'SBSL FAIL asserted in scoped unittest'})
 coverage=read(REPO/'docs/query-stage-041-coverage.json')
 report={'stage':41,'date':'2026-10-04','status':'implemented-within-projections-aggregates-scope','reference':coverage['reference'],'executor':read(REPO/'config/runtimes.json')['9.3'],'sourceCompatibilityVersions':['9.0','9.3'],
  'catalog':{'scopeCount':coverage['scopeCount'],'familyCounts':dict(Counter(r['family'] for r in coverage['contracts'])),'counts':coverage['counts'],'archives':coverage['archives'],'scopeOnly':True,'completeQueryCatalogExecuted':False,'assessedOccurrences':[{'contractId':r['contractId'],'archive':r.get('archive'),'fixture':r.get('fixture'),'fixtureHash':r.get('fixtureHash'),'file':r.get('file'),'range':r.get('range'),'sourceHash':r.get('sourceHash'),'projectSourceHash':r.get('projectSourceHash'),'target':r.get('target'),'criteria':r['criteria'],'evidence':r['evidence']} for r in coverage['contracts'] if r['independentlyAssessed']]},
  'tests':{'distinctSuccessfulMethods':len(methods),'newMethods':sum(k.startswith('test_query_projections.') for k in methods),'affectedLegacyMethods':sum(not k.startswith('test_query_projections.') for k in methods),'scope':'Targeted runs with separate successful repairs; no full regression or full discovery','latestSuccessfulEvidence':methods,'initialFailuresRetained':failures,'logs':logs},
  'public':public,'batches':batches,'schemas':read(OUT/'schemas.json'),'sql':sql,'mutations':mutations,'native':read(OUT/'native/results.json'),'nativeArtifacts':[receipt(p) for p in sorted((OUT/'native').glob('*')) if p.is_file()], 'initialNativeProbeFilenameFailure':receipt(OUT/'first-failures/native-modulus-file-name.stderr'),
  'portableCases':{'core':32,'edges':8,'empty':5,'overflowAndCoalesce':2,'documentedExactTemplates':5},
  'localExclusions':['full query discovery','303 forms','44 previous roots','four validate','full regression suite','nightly execution','native XBQL/platform compilation','browser UI'],
  'preserved':['four source archives and SHA-256','previous assignment expected','runtime/input limits','CI schedule','unrelated workspace output'],
  'nextStage':'042-unions-and-nesting'}
 path=REPO/'docs/query-stage-041-measurements.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print({'tests':len(methods),'new':report['tests']['newMethods'],'legacy':report['tests']['affectedLegacyMethods'],'mutations':len(mutations),'sql':len(sql),'schemas':report['schemas'],'catalog':coverage['counts']})
if __name__=='__main__':main()
