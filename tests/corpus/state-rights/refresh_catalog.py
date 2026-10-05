"""Refresh only the frozen task-45 occurrence selection, without discovery."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import copy,json
from element_test.bridge import write_json
from element_test.generated_types import ProjectTypes
from element_test.loader import open_project
from element_test.model import analyze
from element_test.query_catalog import attach_evidence,markdown
from element_test.query_plan import parse_storage_query
from element_test.local_structures import scalar_structures
from element_test.execution_plan import plan_execution
from element_test.yaml_io import InputError
from state_fixtures import REPO,CORPUS,EVIDENCE,check
REFERENCE=json.loads((CORPUS/'reference-9.3.json').read_text())
NO_CRITERION='Typed contract resolved; no independent direct criterion for this exact sourceHash/range in stage 045'

def main():
 baseline=json.loads((REPO/'docs/query-stage-045-baseline.json').read_text())
 path=REPO/'docs/query-contract-catalog.json';catalog=json.loads(path.read_text());original=copy.deepcopy(catalog)
 frozen={r['contractId']:r for r in baseline['contracts']};selected=set(frozen)
 records=[r for r in catalog['contracts'] if r['contractId'] in selected];assert len(records)==len(selected)==161
 groups={}
 for r in records:
  for key in ('sourceHash','projectSourceHash','range','text','file','target'):
   assert r.get(key)==frozen[r['contractId']].get(key),(r['contractId'],key)
  history={k:frozen[r['contractId']].get(k) for k in ('parsed','planned','executed','independentlyAssessed','criteria','evidence')}
  r.update(parsed=False,planned=False,executed=False,independentlyAssessed=False,criteria=[],evidence=[],ast=None,historyBefore045=history,reference045='stage045Reference')
  if r.get('archive'):
   project=r['projectIdentity'];groups.setdefault((r['archive'],project['Поставщик']+'::'+project['Имя']),[]).append(r)
 for (archive,project),group in groups.items():
  with open_project(REPO/archive,project) as root:
   model=analyze(root);modules={m['sourceFile']:m for m in model['modules']}
   for r in group:
    file=r['file'];source=(root/file).read_text(encoding='utf-8-sig');module=modules.get(file,{})
    assert sha256((root/file).read_bytes()).hexdigest()==r['sourceHash']
    assert model['sourceHash']==r['projectSourceHash']
    c=ProjectTypes(model,module.get('namespace',''),module.get('imports',[]));c.rename_collisions=True;c.reference_id_type='Ууид';c.current_source=file;c.query_root=root;c.local_by_source={file:scalar_structures(source)}
    try:
     if frozen[r['contractId']].get('unsupportedReason')=='Затенённый владелец литерала Запрос':raise InputError('Затенённый владелец литерала Запрос')
     if r['family']=='api-candidate':raise InputError('No independently confirmed query API receiver for this occurrence in stage 045')
     assert source[r['bodyStart']:r['range'][1]-(r['family']=='literal')]==r['text']
     r['ast']=parse_storage_query(r['text'],c).to_dict();r['parsed']=True;r['unsupportedReason']=NO_CRITERION
     r['semantics']=['executor-predicates-state-045','element-reference-9.3','script-current','native-ACL-unavailable']
    except InputError as e:r['unsupportedReason']=str(e)
 attach_evidence(records,[EVIDENCE/('public-real-'+m) for m in ('test','run')])
 cases={'ordinary':([],None),'parameters':(['A'],None),'captured_expression':(['B'],None),'order_limit':([],None),'in':([[1]],None),'between':([1,3],None),'like':(['a'],None)}
 model=analyze(CORPUS/'documented')
 for r in records:
  if r['family']=='documented-form':
   method=r['contractId'].removeprefix('documented-').replace('-','_')
   if method not in cases:
    r['unsupportedReason']={'state':'Historical state outline is prose, not an exact executable source. Snapshot/rollback semantics have separate portable criteria.', 'rights':'Historical rights outline is prose. Executor read-policy fixtures do not establish native ACL/RLS.', 'collections':'Custom captured data source requires a separately confirmed provider contract.'}.get(method,'No direct author criterion for this template')
    continue
   receipt_path=EVIDENCE/('assessment-documented-'+method+'.json');receipt=json.loads(receipt_path.read_text());runtime=json.loads((REPO/receipt['runtime']).read_text())
   assert receipt['status']=='PASS' and receipt['engine']=='SBSL' and runtime['status']=='EXECUTED' and receipt['sourceHash']==model['sourceHash']
   c=check(method=method);c['args']=cases[method][0]
   p=plan_execution(CORPUS/'documented',model,c);q=p.queries[0];source=(CORPUS/'documented'/q['sourceFile']).read_text();exact=r['text'].strip()
   assert (REPO/r['fixture']).read_text().strip()==exact and q['text']==exact and source.count(exact)==1
   start=source.index(exact);end=start+len(exact);fixture_hash=sha256((REPO/r['fixture']).read_bytes()).hexdigest()
   r.update(ast=q['ast'],parsed=True,planned=True,executed=True,independentlyAssessed=True,unsupportedReason=None,fixtureStatus='executable-transferable-project',fixtureHash=fixture_hash,referenceVersion='9.3',semantics=['executor-predicates-state-045','element-reference-9.3','script-current'])
   r['criteria']=[{'criterionId':method,'direct':True,'status':'PASS','origin':'fresh-045-transferable','sourceHash':sha256((CORPUS/'documented'/q['sourceFile']).read_bytes()).hexdigest(),'projectSourceHash':model['sourceHash'],'sourceFile':q['sourceFile'],'range':[start,end],'queryRange':[q['start'],q['end']],'fixtureHash':fixture_hash}]
   r['evidence']=[str(receipt_path.relative_to(REPO)),receipt['runtime']]
  elif r['family']=='documentation-index':
   r['unsupportedReason']='Documentation inventory is not an exact executable source; supported contracts and platform limits are recorded in the stage-045 reference and executable fixtures.'
  if r['independentlyAssessed']:
   r['unsupportedReason']=None
   for criterion in r['criteria']:
    if r.get('sourceHash'):criterion.update(origin='fresh-045',sourceHash=r['sourceHash'],projectSourceHash=r['projectSourceHash'],sourceFile=r['file'],range=r['range'])
 for before,after in zip(original['contracts'],catalog['contracts']):
  if before['contractId'] not in selected:assert before==after
 archives=[{**a,'unchanged':sha256((REPO/a['file']).read_bytes()).hexdigest()==a['sha256']} for a in baseline['archives']];assert all(a['unchanged'] for a in archives)
 counts={k:sum(bool(r[k]) for r in records) for k in ('parsed','planned','executed','independentlyAssessed')}
 catalog['stage045Reference']=REFERENCE;path.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n')
 path.with_suffix('.md').write_text(markdown(catalog).replace('№39/40/41/42/43','№39/40/41/42/43/44/45').replace('№40–43','№40–45'))
 report={'stage':'045','scopeCount':len(records),'familyCounts':dict(Counter(r['family'] for r in records)),'counts':counts,'archives':archives,'reference':REFERENCE,'contracts':records,'remainingCauses':dict(Counter(r['unsupportedReason'] for r in records if r['unsupportedReason'])),'unselectedRecordsPreserved':True,'completeQueryCatalogExecuted':False}
 write_json(EVIDENCE/'catalog-stage-045.json',report);write_json(REPO/'docs/query-stage-045-coverage.json',report);print(counts)
if __name__=='__main__':main()
