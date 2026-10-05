"""Refresh the frozen task-44 selection only; parsing never implies assessment."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import copy,json
from element_test.bridge import write_json
from element_test.generated_types import ProjectTypes
from element_test.indexer import parse_module,method_call_expressions
from element_test.local_structures import scalar_structures
from element_test.loader import open_project
from element_test.model import analyze
from element_test.query_api import proven_api_call,normalized_text,dynamic_queries
from element_test.query_catalog import attach_evidence,markdown
from element_test.query_plan import parse_storage_query,query_literals
from element_test.execution_plan import plan_execution
from element_test.yaml_io import InputError
from result_fixtures import REPO,CORPUS,EVIDENCE,check
REFERENCE=json.loads((CORPUS/'reference-9.3.json').read_text())
NO_CRITERION='Typed contract resolved; no independently authored direct criterion for this exact sourceHash/range'

def contracts(root,model,module,source,file):
 c=ProjectTypes(model,module['namespace'] if module else '',module.get('imports',[]) if module else [])
 c.rename_collisions=True;c.reference_id_type='Ууид';c.current_source=file;c.query_root=root
 c.local_by_source={file:scalar_structures(source)}
 return c

def main():
 baseline=json.loads((REPO/'docs/query-stage-044-baseline.json').read_text())
 path=REPO/'docs/query-contract-catalog.json';catalog=json.loads(path.read_text());original=copy.deepcopy(catalog)
 selected={r['contractId'] for r in baseline['contracts']};records=[r for r in catalog['contracts'] if r['contractId'] in selected]
 assert len(records)==len(selected)==271
 frozen={r['contractId']:r for r in baseline['contracts']};groups={}
 for r in records:
  for key in ('sourceHash','projectSourceHash','range','text','file','target'):
   assert r.get(key)==frozen[r['contractId']].get(key),(r['contractId'],key)
  r.update(parsed=False,planned=False,executed=False,independentlyAssessed=False,criteria=[],evidence=[],ast=None,reference044='stage044Reference')
  if r.get('archive'):
   p=r['projectIdentity'];groups.setdefault((r['archive'],p['Поставщик']+'::'+p['Имя']),[]).append(r)
 for (archive,project),group in groups.items():
  with open_project(REPO/archive,project) as root:
   model=analyze(root);modules={m['sourceFile']:m for m in model['modules']}
   for r in group:
    file=r['file'];source=(root/file).read_text(encoding='utf-8-sig');a,b=r['range']
    assert sha256((root/file).read_bytes()).hexdigest()==r['sourceHash']
    assert model['sourceHash']==r['projectSourceHash']
    assert source[r['bodyStart']:b-(r['family']=='literal')]==r['text']
    module=modules.get(file);c=contracts(root,model,module,source,file)
    try:
     if frozen[r['contractId']].get('unsupportedReason')=='Затенённый владелец литерала Запрос':
      raise InputError('Затенённый владелец литерала Запрос')
     if r['family']=='api-candidate':
      if r['apiBinding']['resolution']=='project-declaration':
       r['apiResolution044']='excluded-project-declaration'
       raise InputError('Excluded: project API with the same method spelling')
      node=next(n for n in parse_module(source)[0] if n.start<=a<n.end)
      body=source[node.start:node.end];symbol=SimpleNamespace(source=body,owner=module,start=node.start,local_structures=tuple(scalar_structures(source)))
      calls=method_call_expressions(body,parse_module(body)[0][0])
      call=next(x for x in calls if node.start+(x.receiver_start if x.receiver_start is not None else x.start)==a and node.start+x.end==b)
      if not proven_api_call(symbol,model,call):
       r['apiResolution044']='unproven-receiver'
       raise InputError('No proven typed query receiver/signature; project callbacks and unrelated Выполнить are excluded')
      queries=[parse_storage_query(t,c).to_dict() for _,_,_,t in query_literals(body)]
      queries += [parse_storage_query(normalized_text(d.text),c).to_dict() for d in dynamic_queries(body,model,module,symbol.local_structures)]
      if not queries:raise InputError('No constant typed query associated with this API receiver')
      r['ast']={'mode':'typed-receiver-044','operation':call.name,'receiver':call.receiver,
                'queryProofs':[{'mode':q['mode'],'astHash':sha256(json.dumps(q,sort_keys=True).encode()).hexdigest()} for q in queries]}
      r['apiResolution044']='typed-query-receiver'
     else:r['ast']=parse_storage_query(normalized_text(r['text']) if r['family']=='xbql-file' else r['text'],c).to_dict()
     r['parsed']=True;r['unsupportedReason']=NO_CRITERION
     r['semantics']=['adapter-result-resources-044','element-reference-9.3','script-current']
    except InputError as e:r['unsupportedReason']=str(e)
 attach_evidence(records,[EVIDENCE/('public-real-'+m) for m in ('test','run')])
 # Match API occurrences against actual capability bindings, not token names.
 for mode in ('test','run'):
  folder=EVIDENCE/('public-real-'+mode);result=json.loads((folder/'result.json').read_text())
  checks={x['id']:x for x in result['checks']};runtime={x['criterionId']:x for x in json.loads((folder/'runtime-evidence.json').read_text())}
  for item in json.loads((folder/'execution-plans.json').read_text()):
   p=item['plan'];entry=p['entry'];criterion=item['criterionId']
   for binding in p['bindings']:
    if binding['category']!='query-api':continue
    for r in records:
     if r['family']!='api-candidate' or r['projectSourceHash']!=result['sourceHash'] or r['file']!=binding['source_file'] or r['range']!=[binding['start'],binding['end']]:continue
     direct=entry['declaration']==r['target']['method'] and entry['source_file']==r['file']
     executed=runtime[criterion]['status']=='EXECUTED'
     r.update(planned=True,executed=executed,independentlyAssessed=bool(executed and direct and checks[criterion]['status'] in ('PASS','FAIL')))
     r['criteria'].append({'criterionId':criterion,'direct':direct,'status':checks[criterion]['status'],'origin':'fresh-044','sourceHash':r['sourceHash'],'projectSourceHash':r['projectSourceHash'],'sourceFile':r['file'],'range':r['range']})
     r['evidence'].append(str(folder.relative_to(REPO)))
 model=analyze(CORPUS/'documented')
 for r in records:
  if r['family']=='documented-form':
   fixture=REPO/r['fixture'];assert fixture.read_text().strip()==r['text'].strip()
   if r['contractId']=='documented-dynamic-api':
    r['unsupportedReason']='Historical ЗапросСВыборкой(Text) template has no confirmed 9.3/native signature; supported ПроизвольныйЗапрос constant text has separate portable criteria'
    continue
   method=r['contractId'].removeprefix('documented-').replace('-','_')
   receipt_path=EVIDENCE/('assessment-documented-'+method+'.json');receipt=json.loads(receipt_path.read_text());runtime=json.loads((REPO/receipt['runtime']).read_text())
   assert receipt['status']=='PASS' and receipt['engine']=='SBSL' and runtime['status']=='EXECUTED' and receipt['sourceHash']==model['sourceHash']
   c=check('daily' if method=='combined_fill_slice_order' else 'ordinary',method);c['args']=['2026-09-30'] if method=='combined_fill_slice_order' else []
   if method=='result_resources':c['storage']['initial']=c['storage']['initial'][:1]
   p=plan_execution(CORPUS/'documented',model,c);q=p.queries[0];source=(CORPUS/'documented'/q['sourceFile']).read_text()
   exact=r['text'].strip();assert source.count(exact)==1
   template_start=source.index(exact);template_end=template_start+len(exact)
   assert source[template_start:template_end]==exact
   if method!='result_resources':assert q['text'].strip()==exact
   r.update(ast=q['ast'],parsed=True,planned=True,executed=True,independentlyAssessed=True,unsupportedReason=None,fixtureStatus='executable-transferable-project',fixtureHash=sha256(fixture.read_bytes()).hexdigest(),referenceVersion='9.3',semantics=['adapter-result-resources-044','element-reference-9.3','script-current'])
   r['criteria']=[{'criterionId':method,'direct':True,'status':'PASS','origin':'fresh-044-transferable','sourceHash':sha256((CORPUS/'documented'/q['sourceFile']).read_bytes()).hexdigest(),'projectSourceHash':model['sourceHash'],'sourceFile':q['sourceFile'],'range':[template_start,template_end],'queryRange':[q['start'],q['end']],'fixtureHash':r['fixtureHash']}]
   r['evidence']=[str(receipt_path.relative_to(REPO)),receipt['runtime']]
  elif r['family']=='documentation-index':r['unsupportedReason']='Documentation inventory has no exact executable source/criterion; confirmed finite API signatures and unsupported operations are recorded in the stage-44 contract'
  if r['independentlyAssessed']:
   r['unsupportedReason']=None
   for criterion in r['criteria']:
    criterion.update(origin='fresh-044-transferable' if r['family']=='documented-form' else 'fresh-044')
    if r.get('sourceHash'):criterion.update(sourceHash=r['sourceHash'],projectSourceHash=r['projectSourceHash'],sourceFile=r['file'],range=r['range'])
 for before,after in zip(original['contracts'],catalog['contracts']):
  if before['contractId'] not in selected:assert before==after
 archives=[{**a,'unchanged':sha256((REPO/a['file']).read_bytes()).hexdigest()==a['sha256']} for a in baseline['archives']];assert all(x['unchanged'] for x in archives)
 counts={k:sum(bool(r[k]) for r in records) for k in ('parsed','planned','executed','independentlyAssessed')}
 catalog['stage044Reference']=REFERENCE;path.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n')
 md=markdown(catalog).replace('№39/40/41/42/43','№39/40/41/42/43/44').replace('№40–43','№40–44')
 path.with_suffix('.md').write_text(md)
 report={'stage':'044','scopeCount':len(records),'familyCounts':dict(Counter(r['family'] for r in records)),'counts':counts,'archives':archives,'reference':REFERENCE,'contracts':records,'remainingCauses':dict(Counter(r['unsupportedReason'] for r in records if r['unsupportedReason'])),'unselectedRecordsPreserved':True}
 write_json(EVIDENCE/'catalog-stage-044.json',report);write_json(REPO/'docs/query-stage-044-coverage.json',report);print(counts)

if __name__=='__main__':main()
