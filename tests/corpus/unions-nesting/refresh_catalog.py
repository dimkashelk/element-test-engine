"""Refresh only the saved task-42 scope; source discovery never implies PASS."""
from pathlib import Path
from hashlib import sha256
from collections import Counter
from types import SimpleNamespace
import json
from element_test.bridge import write_json
from element_test.loader import open_project
from element_test.model import analyze
from element_test.generated_types import ProjectTypes
from element_test.query_plan import parse_storage_query
from element_test.query_catalog import attach_evidence,markdown
from element_test.query_context import prepare_query_context
from element_test.execution_plan import plan_execution
from element_test.yaml_io import InputError
from composite_fixtures import check,real_check,CORPUS,EVIDENCE,REPO
REFERENCE={'sources':[{'version':'9.3','url':'https://1cmycloud.com/console/help/element/9.3/docs/topics/'+s+'/','access':'read in authenticated in-app browser'} for s in ('union','select-statement','select-from','select-into','create-temporary-table-statement','create-index-statement','drop-statement','batch-of-statements')],'executorVersion':'10.0.2-1','executionCompatibilityVersion':'current','nativeXBQL':False}
def main():
 baseline=json.loads((REPO/'docs/query-stage-042-baseline.json').read_text());path=REPO/'docs/query-contract-catalog.json';catalog=json.loads(path.read_text())
 selected={r['contractId'] for r in baseline['contracts']};records=[r for r in catalog['contracts'] if r['contractId'] in selected];assert len(records)==len(selected)
 groups={}
 for r in records:
  r.update(planned=False,executed=False,independentlyAssessed=False,criteria=[],evidence=[],reference042=REFERENCE)
  if r.get('archive'):
   p=r['projectIdentity'];groups.setdefault((r['archive'],p['Поставщик']+'::'+p['Имя']),[]).append(r)
 for (archive,project),group in groups.items():
  with open_project(REPO/archive,project) as root:
   model=analyze(root);modules={m['sourceFile']:m for m in model['modules']}
   for r in group:
    source=(root/r['file']).read_text(encoding='utf-8-sig');assert sha256((root/r['file']).read_bytes()).hexdigest()==r['sourceHash'];assert model['sourceHash']==r['projectSourceHash']
    a,b=r['range'];assert source[r['bodyStart']:b-(1 if r['family']=='literal' else 0)]==r['text']
    if r.get('unsupportedReason')=='Затенённый владелец литерала Запрос':continue
    module=modules.get(r['file']);c=ProjectTypes(model,module['namespace'] if module else r['ownerIdentity']['namespace'],module.get('imports',[]) if module else [])
    c.rename_collisions=True;c.reference_id_type='Ууид'
    if (r.get('target') or {}).get('method')=='ТекущееПодразделение':prepare_query_context(SimpleNamespace(check=real_check()),c)
    try:
     r['ast']=parse_storage_query(r['text'],c).to_dict();r['parsed']=True;r['unsupportedReason']=None;r['semantics']=['adapter-storage-unions-nesting-042','element-reference-9.3','script-current']
    except InputError as e:r['ast']=None;r['parsed']=False;r['unsupportedReason']=str(e)
 attach_evidence(records,[EVIDENCE/('public-real-'+mode) for mode in ('test','run')])
 root=CORPUS/'documented';model=analyze(root)
 for r in records:
  if r['family']=='documented-form':
   method=r['contractId'].removeprefix('documented-').replace('-','_');fixture=REPO/r['fixture'];assert fixture.read_text().strip()==r['text'].strip()
   receipt_path=EVIDENCE/('documented-assessment-'+method+'.json');receipt=json.loads(receipt_path.read_text());runtime=json.loads((REPO/receipt['runtime']).read_text())
   source_hash=sha256((root/'Entry/Main.xbsl').read_bytes()).hexdigest();assert receipt['status']=='PASS' and receipt['engine']=='SBSL' and runtime['status']=='EXECUTED';assert receipt['sourceHash']==source_hash and receipt['projectSourceHash']==model['sourceHash']
   p=plan_execution(root,model,check(method=method));q=p.queries[0];assert q['text'].strip()==r['text'].strip()
   r.update(ast=q['ast'],parsed=True,planned=True,executed=True,independentlyAssessed=True,unsupportedReason=None,fixtureStatus='executable-transferable-project',fixtureHash=sha256(fixture.read_bytes()).hexdigest(),referenceVersion='9.3',semantics=['adapter-storage-unions-nesting-042','element-reference-9.3','script-current'])
   r['criteria']=[{'criterionId':method,'direct':True,'status':'PASS','origin':'fresh-042-transferable','sourceHash':source_hash,'projectSourceHash':model['sourceHash'],'sourceFile':q['sourceFile'],'range':[q['start'],q['end']],'fixtureHash':r['fixtureHash']}];r['evidence']=[str(receipt_path.relative_to(REPO)),receipt['runtime']]
  elif r['family']=='documentation-index':r['unsupportedReason']='Documentation inventory has no exact executable source/criterion; index operations in this adapter are validated logical hints, native physical indexes and extended temporary field definitions remain outside the verified contract'
  elif r['parsed'] and not r['executed']:r['unsupportedReason']='Typed AST parsed; this exact source has no independently authored direct executable criterion; external XBQL bindings or additional source operations remain queued'
 archives=[{**a,'unchanged':sha256((REPO/a['file']).read_bytes()).hexdigest()==a['sha256']} for a in baseline['archives']];assert all(a['unchanged'] for a in archives)
 counts={k:sum(bool(r.get(k)) for r in records) for k in ('parsed','planned','executed','independentlyAssessed')};catalog['stage042Reference']=REFERENCE
 path.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n');path.with_suffix('.md').write_text(markdown(catalog))
 report={'stage':'042','scopeCount':len(records),'counts':counts,'archives':archives,'reference':REFERENCE,'contracts':records,'remainingCauses':dict(Counter(r.get('unsupportedReason') for r in records if r.get('unsupportedReason')))}
 write_json(EVIDENCE/'catalog-stage-042.json',report);write_json(REPO/'docs/query-stage-042-coverage.json',report);print(counts)
if __name__=='__main__':main()
