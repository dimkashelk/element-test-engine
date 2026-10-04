"""Refresh the saved stage-41 scope only, with exact source/criterion receipts."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from element_test.bridge import write_json
from element_test.execution_plan import plan_execution
from element_test.generated_types import ProjectTypes
from element_test.loader import open_project
from element_test.model import analyze
from element_test.query_catalog import attach_evidence, markdown
from element_test.query_plan import parse_storage_query
from element_test.yaml_io import InputError
from projection_fixtures import documented_check
REPO=Path(__file__).resolve().parents[3];OUT=REPO/'result/projections-aggregates'
REFERENCE={'sources':[{'version':'9.3','url':'https://1cmycloud.com/console/help/element/9.3/docs/topics/'+s+'/'} for s in ('count-function','sum-function','average-function','maximum-function','minimum-function','cast-expression','arithmetic-operations-in-query-language','concatenation','date-and-time-in-query-language','case-expression','substring-function')], 'executorVersion':'10.0.2-1','executionCompatibilityVersion':'current','nativeXBQL':False}
METHODS={'computed-projection':'ComputedProjection','group':'Group','having':'Having','case':'Case','combined-join-group-null':'CombinedJoinGroupNull'}
def main():
 baseline=json.loads((REPO/'docs/query-stage-041-baseline.json').read_text());path=REPO/'docs/query-contract-catalog.json';catalog=json.loads(path.read_text())
 selected={r['contractId'] for r in baseline['contracts']};records=[r for r in catalog['contracts'] if r['contractId'] in selected];assert len(records)==len(selected)
 groups={}
 for r in records:
  if r.get('archive'):
   p=r['projectIdentity'];groups.setdefault((r['archive'],p['Поставщик']+'::'+p['Имя']),[]).append(r)
 for (archive,project),group in groups.items():
  with open_project(REPO/archive,project) as root:
   model=analyze(root);modules={m['sourceFile']:m for m in model['modules']}
   for r in group:
    source=(root/r['file']).read_text(encoding='utf-8-sig')
    assert sha256((root/r['file']).read_bytes()).hexdigest()==r['sourceHash']
    assert model['sourceHash']==r['projectSourceHash']
    a,b=r['range'];assert source[r['bodyStart']:b-(1 if r['family']=='literal' else 0)]==r['text']
    r.update(planned=False,executed=False,independentlyAssessed=False,criteria=[],evidence=[],reference041=REFERENCE)
    if r.get('unsupportedReason')=='Затенённый владелец литерала Запрос':continue
    module=modules.get(r['file']);c=ProjectTypes(model,module['namespace'] if module else r['ownerIdentity']['namespace'],module.get('imports',[]) if module else [])
    c.rename_collisions=True;c.reference_id_type='Ууид'
    try:
     r['ast']=parse_storage_query(r['text'],c).to_dict();r['parsed']=True;r['unsupportedReason']=None
     r['semantics']=['adapter-storage-projections-aggregates-041','element-reference-9.3','script-current']
    except InputError as e:r['ast']=None;r['parsed']=False;r['unsupportedReason']=str(e)
 attach_evidence(records,[OUT/('public-'+label+'-'+mode) for label in ('tasks','max') for mode in ('test','run')])
 root=REPO/'tests/corpus/projections-aggregates/documented';model=analyze(root)
 for r in records:
  r['reference041']=REFERENCE
  if r['family']=='documented-form':
   method=METHODS[r['contractId'].removeprefix('documented-')];fixture=REPO/r['fixture'];assert fixture.read_text().strip()==r['text'].strip()
   receipt_path=OUT/('documented-assessment-'+method+'.json');receipt=json.loads(receipt_path.read_text());runtime=json.loads((REPO/receipt['runtime']).read_text())
   source_hash=sha256((root/'Entry/Main.xbsl').read_bytes()).hexdigest()
   assert receipt['status']=='PASS' and receipt['engine']=='SBSL' and runtime['status']=='EXECUTED'
   assert receipt['sourceHash']==source_hash and receipt['projectSourceHash']==model['sourceHash']
   plan=plan_execution(root,model,documented_check(method));query=plan.queries[0];assert query['text'].strip()==r['text'].strip()
   r.update(ast=query['ast'],parsed=True,planned=True,executed=True,independentlyAssessed=True,unsupportedReason=None,fixtureStatus='executable-transferable-project',fixtureHash=sha256(fixture.read_bytes()).hexdigest(),referenceVersion='9.3',semantics=['adapter-storage-projections-aggregates-041','element-reference-9.3','script-current'])
   r['criteria']=[{'criterionId':method,'direct':True,'status':'PASS','origin':'fresh-041-transferable','sourceHash':source_hash,'projectSourceHash':model['sourceHash'],'sourceFile':query['sourceFile'],'range':[query['start'],query['end']],'fixtureHash':r['fixtureHash']}]
   r['evidence']=[str(receipt_path.relative_to(REPO)),receipt['runtime']]
  elif r['family']=='documentation-index':
   r['unsupportedReason']='Documentation inventory has no exact executable source/criterion; finite supported signatures and remaining methods are listed in the stage-41 contract'
  elif r['parsed'] and not r['executed']:
   r['unsupportedReason']='Typed AST parsed; no independently authored direct executable criterion for this exact source/range (external XBQL binding or additional source operations remain queued)'
 archives=[{**a,'unchanged':sha256((REPO/a['file']).read_bytes()).hexdigest()==a['sha256']} for a in baseline['archives']];assert all(a['unchanged'] for a in archives)
 counts={k:sum(bool(r.get(k)) for r in records) for k in ('parsed','planned','executed','independentlyAssessed')}
 catalog['documentationReferenceVersions']=['9.1','9.3'];catalog['stage041Reference']=REFERENCE
 path.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n');path.with_suffix('.md').write_text(markdown(catalog))
 report={'stage':'041','scopeCount':len(records),'counts':counts,'archives':archives,'reference':REFERENCE,'contracts':records,'remainingCauses':dict(Counter(r.get('unsupportedReason') for r in records if r.get('unsupportedReason')))}
 write_json(OUT/'catalog-stage-041.json',report);write_json(REPO/'docs/query-stage-041-coverage.json',report);print(counts)
if __name__=='__main__':main()
