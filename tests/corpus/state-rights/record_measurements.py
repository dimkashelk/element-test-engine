"""Summarize saved task-45 receipts, never execute or discover extra roots."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import json,re
REPO=Path(__file__).resolve().parents[3];OUT=REPO/'result/state-rights'
def read(p):return json.loads(p.read_text())
def receipt(p):return {'file':str(p.relative_to(REPO)),'sha256':sha256(p.read_bytes()).hexdigest()}
def main():
 latest={};logs=[];failures=[]
 for log in sorted(OUT.glob('*.stderr'),key=lambda p:p.stat().st_mtime):
  text=log.read_text();summary=re.findall(r'^Ran \d+ tests?.*|^FAILED.*|^OK.*',text,re.M)
  if not summary:continue
  logs.append({**receipt(log),'summary':summary})
  for name,status in re.findall(r'test_\w+ \(([^)]+)\) \.\.\. (ok|FAIL|ERROR)',text):
   latest[name]={'status':status,'evidence':str(log.relative_to(REPO))}
   if status in ('FAIL','ERROR'):failures.append({'test':name,'status':status,'evidence':str(log.relative_to(REPO))})
 assert latest and all(v['status']=='ok' for v in latest.values()),latest
 public=[]
 for label in ('public-real-test','public-real-run','public-control'):
  r=read(OUT/label/'result.json');public.append({'folder':str((OUT/label).relative_to(REPO)),**{k:r[k] for k in ('sourceHash','score','maxScore','unavailablePoints')},'checks':[c['status'] for c in r['checks']]})
 batches=[]
 for folder in read(OUT/'batch-latest.json'):
  r=read(REPO/folder/'batch-result.json');batches.append({'folder':folder,'counts':r['counts'],'scores':[s['score'] for s in r['submissions']],'cacheHits':[s['cacheHit'] for s in r['submissions']]})
 sql=[]
 for name in ('sql-ordinary-State.json','sql-ordinary-Mutate.json','sql-ordinary-Access.json','sql-daily-Combined.json','sql-hierarchy-Hierarchy.json','sql-real.json'):
  p=OUT/name;r=read(p);assert r['status']=='EXECUTED' and r['integration']['cleanup'] and 'source:commit' not in r['storageTrace']
  sql.append({**receipt(p),'status':r['status'],'integration':r['integration'],'storageDiagnostics':r['storageDiagnostics']})
 mutations=[]
 for label in ('Between','Like','Mutate','DeleteCount','Exists','Distinct'):
  p=OUT/('mutation-assessment-'+label+'.json');r=read(p);assert r['status']=='FAIL' and r['engine']=='SBSL';mutations.append({**receipt(p),**r})
 coverage=read(REPO/'docs/query-stage-045-coverage.json')
 assert all(a['unchanged'] for a in coverage['archives'])
 report={'stage':45,'date':'2026-10-05','status':'implemented-within-explicit-executor-scope; full-catalog-contract-remains-open','reference':coverage['reference'],'executor':read(REPO/'config/runtimes.json')['9.3'],'sourceCompatibilityVersions':['9.0'],
  'catalog':{'scopeCount':coverage['scopeCount'],'familyCounts':coverage['familyCounts'],'counts':coverage['counts'],'archives':coverage['archives'],'scopeOnly':True,'completeQueryCatalogExecuted':False,'unselectedRecordsPreserved':True,
   'assessedOccurrences':[{k:r.get(k) for k in ('contractId','family','archive','file','range','sourceHash','projectSourceHash','fixture','fixtureHash','target','criteria','evidence')} for r in coverage['contracts'] if r['independentlyAssessed']]},
  'tests':{'distinctSuccessfulScopedMethods':len(latest),'newMethods':sum(k.startswith('test_query_state.') for k in latest),'affectedLegacyMethods':sum(not k.startswith('test_query_state.') for k in latest),'latestSuccessfulEvidence':latest,'logs':logs,'initialFailuresRetained':failures},
  'public':public,'batches':batches,'schemas':read(OUT/'schemas.json'),'metadataValidation':read(OUT/'yaml-validation-final.json')['summary'],'sql':sql,'mutations':mutations,'alternative':receipt(OUT/'alternative.json'),'recovery':{'failed':receipt(OUT/'infrastructure-failure.json'),'restored':receipt(OUT/'assessment-infrastructure-recovery.json')},
  'native':read(OUT/'native/results.json'),
  'sourceArtifacts':[receipt(REPO/p) for p in ('element_test/query_predicates.py','element_test/query_state.py','element_test/query_access.py','element_test/execution_plan.py','element_test/generated_types.py','element_test/storage.py','element_test/query_plan.py','element_test/query_joins.py','element_test/query_composites.py','element_test/query_projections.py','element_test/query_sources.py','element_test/storage_queries.py','tests/state_fixtures.py','tests/test_query_state.py')]+[receipt(p) for p in sorted((REPO/'tests/corpus/state-rights').rglob('*')) if p.is_file() and '__pycache__' not in p.parts],
  'savedEvidence':[receipt(p) for p in sorted(OUT.glob('*.json'))],
  'localExclusions':['full discovery','303 forms','44 previous roots','four archive validation passes','full regression suite','nightly execution','native platform XBQL compilation','native authentication/ACL/RLS','additional hierarchy tables','nullable or multi-level correlated captures','correlated IN','DML with joined sources/default declarations','custom data-source providers'],
  'preserved':['four archives and SHA-256','previous assignment expected','runtime/input limits','CI schedule']}
 (REPO/'docs/query-stage-045-measurements.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print({'tests':len(latest),'new':report['tests']['newMethods'],'legacy':report['tests']['affectedLegacyMethods'],'mutations':len(mutations),'sql':len(sql),'schemas':report['schemas'],'catalog':coverage['counts']})
if __name__=='__main__':main()
