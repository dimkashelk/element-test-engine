"""Summarize existing task-44 receipts; no discovery or runtime execution."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import json,re
REPO=Path(__file__).resolve().parents[3];OUT=REPO/'result/result-resources'
def read(p):return json.loads(p.read_text())
def receipt(p):return {'file':str(p.relative_to(REPO)),'sha256':sha256(p.read_bytes()).hexdigest()}
def main():
 scoped=('final-targeted-plan.stderr','final-docker.stderr','final-sql.stderr','final-public.stderr','final-legacy-runtime.stderr','repeat-legacy-runtime.stderr','final-rowtype-plan.stderr','final-owner-guard-plan.stderr')
 latest={};logs=[];failures=[]
 for log in sorted(OUT.glob('*.stderr'),key=lambda p:p.stat().st_mtime):
  text=log.read_text();summary=re.findall(r'^Ran \d+ tests?.*|^FAILED.*|^OK.*',text,re.M)
  if summary:logs.append({**receipt(log),'summary':summary,'finalScope':log.name in scoped})
  for name,status in re.findall(r'test_\w+ \(([^)]+)\) \.\.\. (ok|FAIL|ERROR)',text):
   if log.name in scoped:latest[name]={'status':status,'evidence':str(log.relative_to(REPO))}
   if status in ('FAIL','ERROR'):failures.append({'test':name,'status':status,'evidence':str(log.relative_to(REPO))})
 assert len(latest)==35 and all(v['status']=='ok' for v in latest.values()),latest
 public=[]
 for label in ('public-real-test','public-real-run','public-control'):
  r=read(OUT/label/'result.json');public.append({'folder':str((OUT/label).relative_to(REPO)),**{k:r[k] for k in ('sourceHash','score','maxScore','unavailablePoints')},'checks':[c['status'] for c in r['checks']]})
 batches=[]
 for folder in read(OUT/'batch-latest.json'):
  r=read(REPO/folder/'batch-result.json');batches.append({'folder':folder,'counts':r['counts'],'scores':[s['score'] for s in r['submissions']],'cacheHits':[s['cacheHit'] for s in r['submissions']]})
 sql=[]
 for name in ('sql-ordinary-Dynamic.json','sql-ordinary-State.json','sql-daily-Produce.json','sql-real.json'):
  p=OUT/name;r=read(p);assert r['status']=='EXECUTED' and r['integration']['cleanup'] and 'source:commit' not in r['storageTrace']
  sql.append({**receipt(p),'status':r['status'],'integration':r['integration'],'storageDiagnostics':r['storageDiagnostics']})
 mutations=[]
 for label in ('Produce','Early','Dynamic','Loop','Closed','Local'):
  p=OUT/('mutation-assessment-'+label+'.json');r=read(p);assert r['status']=='FAIL' and r['engine']=='SBSL'
  mutations.append({**receipt(p),**r})
 coverage=read(REPO/'docs/query-stage-044-coverage.json')
 report={'stage':44,'date':'2026-10-05','status':'implemented-within-query-result-executor-scope','reference':coverage['reference'],'executor':read(REPO/'config/runtimes.json')['9.3'],'sourceCompatibilityVersions':['9.0'],
  'catalog':{'scopeCount':coverage['scopeCount'],'familyCounts':coverage['familyCounts'],'counts':coverage['counts'],'archives':coverage['archives'],'scopeOnly':True,'completeQueryCatalogExecuted':False,'unselectedRecordsPreserved':True,'baselineBeforeImplementation':260,'associatedExternalOccurrences':11,
   'assessedOccurrences':[{k:r.get(k) for k in ('contractId','family','archive','file','range','sourceHash','projectSourceHash','fixture','fixtureHash','target','criteria','evidence')} for r in coverage['contracts'] if r['independentlyAssessed']]},
  'tests':{'distinctSuccessfulScopedMethods':len(latest),'newMethods':sum(k.startswith('test_query_results.') for k in latest),'affectedLegacyMethods':sum(not k.startswith('test_query_results.') for k in latest),'latestSuccessfulEvidence':latest,'logs':logs,'initialFailuresRetained':failures,
   'accidentalInitialDiscoveryOnce':{'evidence':receipt(OUT/'first-legacy-plan.stderr'),'reason':'Initial selector included the entire legacy FillPlanTest class, unexpectedly invoking full read-only discovery. It failed on missing generated-type owner identity. Guard repaired; full discovery was not repeated, per local task restriction. A separate out-of-scope evidence test passed in that initial run.'}},
  'public':public,'batches':batches,'schemas':read(OUT/'schemas.json'),'metadataValidation':read(OUT/'yaml-validation-repeat.json')['summary'],'sql':sql,'mutations':mutations,'alternative':read(OUT/'assessment-alternative.json'),'recovery':{'failed':receipt(OUT/'infrastructure-failure.json'),'restored':receipt(OUT/'assessment-infrastructure-recovery.json')},
  'native':read(OUT/'native/results.json'),'nativeArtifacts':[receipt(p) for p in sorted((OUT/'native').glob('*')) if p.is_file()],
  'sourceArtifacts':[receipt(REPO/p) for p in ('element_test/query_api.py','element_test/query_construction.py','element_test/query_results.py','element_test/execution_plan.py','element_test/generated_types.py','element_test/query_plan.py','element_test/query_joins.py','element_test/query_composites.py','element_test/storage_queries.py','tests/result_fixtures.py','tests/test_query_results.py')]+[receipt(p) for p in sorted((REPO/'tests/corpus/result-resources').rglob('*')) if p.is_file() and '__pycache__' not in p.parts],
  'savedEvidence':[receipt(p) for p in sorted(OUT.glob('*.json'))],'localExclusions':['full discovery after inadvertent initial selector','303 forms','44 previous roots','four archive validation passes','full regression suite','nightly execution','native platform XBQL compilation','browser UI scenarios'],
  'preserved':['four archives and SHA-256','previous assignment expected','runtime/input limits','CI schedule'],'nextStage':'045-state-rights-and-combinations'}
 path=REPO/'docs/query-stage-044-measurements.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print({'tests':len(latest),'new':report['tests']['newMethods'],'legacy':report['tests']['affectedLegacyMethods'],'mutations':len(mutations),'sql':len(sql),'schemas':report['schemas'],'catalog':coverage['counts']})
if __name__=='__main__':main()
