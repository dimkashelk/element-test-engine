"""Summarize saved scoped evidence without running discovery or student code."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
REPO=Path(__file__).resolve().parents[3];OUT=REPO/'result/joins-null'
def read(path):return json.loads(path.read_text())
def main():
 methods={};failures=[];logs=[]
 for log in sorted(OUT.glob('*.log'),key=lambda p:p.stat().st_mtime):
  body=log.read_text();logs.append({'file':str(log.relative_to(REPO)),'sha256':sha256(log.read_bytes()).hexdigest(),'summary':re.findall(r'^Ran \d+ tests?.*|^FAILED.*|^OK.*',body,re.M)})
  for line in body.splitlines():
   match=re.match(r'test_\w+ \(([^)]+)\) \.\.\. (ok|FAIL|ERROR|skipped.*)$',line)
   if match:
    key,status=match.groups();methods[key]={'status':status,'evidence':str(log.relative_to(REPO))}
    if status in ('FAIL','ERROR'):failures.append({'test':key,'status':status,'evidence':str(log.relative_to(REPO))})
 old='test_query_joins.JoinPublicTest.test_public_test_run_control_two_fresh_batches_and_schemas'
 new='test_query_joins.JoinPublicTest.test_public_test_run_control_two_fresh_batches_and_packages'
 if old in methods and new in methods:
  del methods[old]
  for f in failures:
   if f['test']==old:f['supersededBy']=new
 assert len(methods)==51 and all(v['status']=='ok' for v in methods.values()),methods
 public=[]
 for folder in ('public-test','public-run','public-cases-test','public-cases-run','public-control'):
  result=read(OUT/folder/'result.json')
  public.append({'folder':str((OUT/folder).relative_to(REPO)), **{k:result[k] for k in ('sourceHash','score','maxScore','unavailablePoints')},'checks':[c['status'] for c in result['checks']]})
 batches=[]
 for n in (1,2):
  r=read(OUT/f'batch-{n}/batch-result.json')
  batches.append({'folder':f'result/joins-null/batch-{n}','counts':r['counts'],'scores':[s['score'] for s in r['submissions']],'cacheHits':[s['cacheHit'] for s in r['submissions']]})
 coverage=read(REPO/'docs/query-stage-040-coverage.json')
 sql=[]
 for label in ('real','ordinary-full','ordinary-state','renamed-full','renamed-state','cases'):
  p=OUT/('sql-'+label+'.json');r=read(p)
  assert r['status']=='EXECUTED' and r['integration']['cleanup']
  sql.append({'file':str(p.relative_to(REPO)),'status':r['status'],'cleanup':r['integration']['cleanup'],'sha256':sha256(p.read_bytes()).hexdigest()})
 mutations=[]
 for label in ('inner','on','resource','fallback','base','boundary','right-left','full-left','null-not-null','cases-status','cases-order'):
  p=OUT/('mutation-'+label+'.json');r=read(p);assert r['status']=='EXECUTED'
  mutations.append({'label':label,'runtimeStatus':r['status'],'assessment':'SBSL FAIL asserted in scoped unittest','file':str(p.relative_to(REPO)),'sha256':sha256(p.read_bytes()).hexdigest()})
 runtimes=read(REPO/'config/runtimes.json')
 report={'stage':40,'date':'2026-10-04','status':'implemented-within-relational-joins-null-scope',
  'documentationReferenceVersion':'9.3','executor':runtimes['9.3'],
  'sourceCompatibilityVersions':['9.0','9.3'],'runtimeSelection':'check.runtimeProfile = 9.3; installed Script uses current; native -c 9.3 is rejected',
  'catalog':{'scopeCount':coverage['scopeCount'],'familyCounts':dict(Counter(r['family'] for r in coverage['contracts'])),'counts':coverage['counts'],'archives':coverage['archives'],
             'scopeOnly':True,'completeQueryCatalogExecuted':False,'assessedOccurrences':[{'contractId':r['contractId'],'archive':r['archive'],'file':r['file'],'range':r['range'],'sourceHash':r['sourceHash'],'projectSourceHash':r['projectSourceHash'],'target':r['target'],'criteria':r['criteria'],'evidence':r['evidence']} for r in coverage['contracts'] if r['independentlyAssessed']]},
  'tests':{'distinctSuccessfulMethods':len(methods),'newMethods':sum(k.startswith('test_query_joins.') for k in methods),'affectedLegacyMethods':sum(not k.startswith('test_query_joins.') for k in methods),'scope':'Several targeted runs and separate repairs; no full suite or full discovery','latestSuccessfulEvidence':methods,'initialFailuresRetained':failures,'logs':logs},
  'public':public,'batches':batches,'schemas':read(OUT/'schemas.json'),'sql':sql,'mutations':mutations,
  'native':read(OUT/'native/results.json'),'reference':coverage['reference'],
  'localExclusions':['full query discovery','303 forms','44 previous roots','four validate','full regression suite','nightly execution','native XBQL/platform compilation','browser UI'],
  'preserved':['four source archives and their SHA-256','previous assignment expected','runtime and input limits','CI schedule','unrelated workspace output'],
  'nextStage':'041-projections-and-aggregates'}
 p=REPO/'docs/query-stage-040-measurements.json';p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print({'tests':len(methods),'new':report['tests']['newMethods'],'legacy':report['tests']['affectedLegacyMethods'],'mutations':len(mutations),'sql':len(sql),'schemas':report['schemas'],'catalog':coverage['counts']})
if __name__=='__main__':main()
