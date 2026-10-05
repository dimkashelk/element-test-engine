"""Record saved task-44 receipts and refresh only nine evidenced catalog entries.

No discovery, source execution, business calculation or broad catalog reparse.
Historical task-44 measurements remain intact.
"""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import copy
import json
import re

from element_test.indexer import parse_module
from element_test.query_catalog import markdown

REPO=Path(__file__).resolve().parents[3]
OUT=REPO/'result/result-resources-completion'
FRESH=OUT/'fresh-test_query_results'

def read(path):return json.loads(path.read_text())
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def receipt(path):return {'file':str(path.relative_to(REPO)),'sha256':sha256(path.read_bytes()).hexdigest()}

def schemas(folders):
    from jsonschema import Draft202012Validator
    saved=[]
    def validate(path,schema,value=None):
        data=read(path) if value is None else value
        Draft202012Validator(read(REPO/'docs'/schema)).validate(data)
        saved.append(receipt(path))
    for folder in folders:
        for path in folder.rglob('grading.json'):validate(path,'grading-v1.schema.json')
        if (folder/'batch-result.json').exists():
            validate(folder/'batch-result.json','batch-result-v1.schema.json')
            for line in (folder/'events.jsonl').read_text().splitlines():
                validate(folder/'events.jsonl','batch-events-v1.schema.json',json.loads(line))
    validate(FRESH/'batch-manifest.json','batch-manifest-v1.schema.json')
    return {'status':'PASS','validatedArtifacts':len(saved),'receipts':saved}


def catalog_refresh(public,plans,archives):
    original=read(REPO/'docs/query-contract-catalog.json');catalog=copy.deepcopy(original)
    old=read(REPO/'docs/query-stage-044-coverage.json')
    selected={r['contractId']:r for r in old['contracts'] if r['independentlyAssessed']}
    refreshed=[]
    for r in catalog['contracts']:
        previous=selected.get(r['contractId'])
        if previous:
            assert r.get('sourceHash')==previous.get('sourceHash') and r.get('range')==previous.get('range')
            evidence=[p.replace('result/result-resources/','result/result-resources-completion/fresh-test_query_results/') for p in previous['evidence']]
            assert all((REPO/p).exists() for p in evidence)
            criteria=copy.deepcopy(previous['criteria'])
            if r['family']=='documented-form':
                assessment=read(REPO/evidence[0]);runtime=read(REPO/assessment['runtime'])
                assert assessment['status']=='PASS' and runtime['status']=='EXECUTED'
                assert assessment['sourceHash']==criteria[0]['projectSourceHash']
            else:
                for folder in evidence:
                    result=read(REPO/folder/'result.json');runtime=read(REPO/folder/'runtime-evidence.json')
                    assert result['sourceHash']==r['projectSourceHash']
                    for criterion in criteria:
                        assert next(x for x in result['checks'] if x['id']==criterion['criterionId'])['status']=='PASS'
                        assert next(x for x in runtime if x['criterionId']==criterion['criterionId'])['status']=='EXECUTED'
            for criterion in criteria:criterion['origin']='fresh-044-completion'
            r.update(criteria=criteria,evidence=evidence,completion044Mode='standalone-emulation',unsupportedReason=None)
            refreshed.append(copy.deepcopy(r))
        elif r['contractId']=='documented-dynamic-api':
            source=OUT/'new-public-project/Entry/Main.xbsl';text=source.read_text()
            exact=(REPO/r['fixture']).read_text().strip();assert text.count(exact)==1
            a=text.index(exact);b=a+len(exact)
            plan=next(p['plan'] for p in plans if p['criterionId']=='Legacy')
            q=next(q for q in plan['queries'] if q.get('platformSpelling')=='ЗапросСВыборкой')
            assert [a,b]==[q['start'],q['end']]
            assert next(c for c in public['checks'] if c['id']=='Legacy')['status']=='PASS'
            runtime=read(OUT/'new-public/runtime-evidence.json')
            assert next(c for c in runtime if c['criterionId']=='Legacy')['status']=='EXECUTED'
            r.update(parsed=True,planned=True,executed=True,independentlyAssessed=True,
                unsupportedReason=None,fixtureStatus='executable-emulation',nativeSignatureConfirmed=False,
                completion044Mode='explicit-historical-api-emulation',ast=q['ast'],
                fixtureHash=sha256((REPO/r['fixture']).read_bytes()).hexdigest(),
                criteria=[{'criterionId':'Legacy','direct':True,'status':'PASS','origin':'fresh-044-completion-emulation',
                    'sourceHash':sha256(source.read_bytes()).hexdigest(),'projectSourceHash':public['sourceHash'],
                    'sourceFile':'Entry/Main.xbsl','range':[a,b],'fixtureHash':sha256((REPO/r['fixture']).read_bytes()).hexdigest()}],
                evidence=['result/result-resources-completion/new-public'])
            refreshed.append(copy.deepcopy(r))
    ids={r['contractId'] for r in refreshed};assert len(ids)==9
    for before,after in zip(original['contracts'],catalog['contracts']):
        if before['contractId'] not in ids:assert before==after
    catalog['stage044CompletionReference']=read(REPO/'tests/corpus/result-resources/completion-reference-9.3.json')
    write(REPO/'docs/query-contract-catalog.json',catalog)
    rendered=markdown(catalog).replace('# Каталог контрактов запросов — №39/40/41/42/43', '# Каталог контрактов запросов — №39–45')
    rendered=rendered.replace('P — parsed', 'Дополнение №44: девять точных критериев получили свежие свидетельства. `ЗапросСВыборкой` проверен как явный эмулятор; native-сигнатура не заявляется. [Измерения дополнения](query-stage-044-completion-measurements.json).\n\nP — parsed')
    (REPO/'docs/query-contract-catalog.md').write_text(rendered)
    coverage={'stage':44,'mode':'completion-with-explicit-emulation','refreshedCount':9,
        'counts':{k:sum(bool(r[k]) for r in refreshed) for k in ('parsed','planned','executed','independentlyAssessed')},
        'unselectedRecordsPreserved':True,'fullCatalogExecuted':False,'archives':archives,'contracts':refreshed}
    write(REPO/'docs/query-stage-044-completion-coverage.json',coverage)
    return coverage


def main():
    baseline=read(REPO/'docs/query-stage-044-measurements.json')
    accepted=('completion-final-plan.stderr','completion-last-plan.stderr','emulation-first-docker.stderr',
        'completion-repeat-docker.stderr','completion-final-legacy.stderr','emulation-final-sql.stderr',
        'completion-repeat-public.stderr','completion-cast-regression.stderr')
    latest={};logs=[];failures=[]
    for log in sorted(OUT.glob('*.stderr'),key=lambda p:p.stat().st_mtime):
        text=log.read_text();summary=re.findall(r'^Ran \d+ tests?.*|^FAILED.*|^OK.*',text,re.M)
        if summary:logs.append({**receipt(log),'summary':summary,'selected':log.name in accepted})
        for name,status in re.findall(r'test_\w+ \(([^)]+)\) \.\.\. (ok|FAIL|ERROR)',text):
            if log.name in accepted:latest[name]={'status':status,'evidence':str(log.relative_to(REPO))}
            if status in ('FAIL','ERROR'):failures.append({'test':name,'status':status,'evidence':str(log.relative_to(REPO))})
    assert set(baseline['tests']['latestSuccessfulEvidence'])<=latest.keys()
    assert len(latest)==47 and all(v['status']=='ok' for v in latest.values()),latest
    public=read(OUT/'new-public/result.json');plans=read(OUT/'new-public/execution-plans.json')
    assert len(public['checks'])==17 and all(c['status']=='PASS' for c in public['checks'])
    source=OUT/'new-public-project/Entry/Main.xbsl';text=source.read_text()
    extensions=[]
    for check in public['checks']:
        plan=next(p['plan'] for p in plans if p['criterionId']==check['id'])
        node=next(n for n in parse_module(text)[0] if n.name==check['id'])
        extensions.append({'criterionId':check['id'],'status':'PASS','engine':'SBSL',
            'sourceFile':'Entry/Main.xbsl','sourceHash':sha256(source.read_bytes()).hexdigest(),
            'projectSourceHash':public['sourceHash'],'range':[node.start,node.end],
            'evidence':str((OUT/'new-public').relative_to(REPO)),
            'queryPlans':plan['queries'],'resources':plan['resources']})
    folders=[OUT/'new-public']+[FRESH/('public-real-'+mode) for mode in ('test','run')]+[FRESH/'public-control']
    batches=[]
    for label in read(FRESH/'batch-latest.json'):
        folder=REPO/label;folders.append(folder);r=read(folder/'batch-result.json')
        assert [s['score'] for s in r['submissions']]==[3,0,3] and not any(s['cacheHit'] for s in r['submissions'])
        batches.append({'folder':label,'counts':r['counts'],'scores':[s['score'] for s in r['submissions']]})
    schema=schemas(folders);write(OUT/'schemas.json',schema)
    archives=[{**a,'unchanged':sha256((REPO/a['file']).read_bytes()).hexdigest()==a['sha256']}
              for a in read(REPO/'docs/query-stage-044-baseline.json')['archives']]
    assert all(a['unchanged'] for a in archives)
    coverage=catalog_refresh(public,plans,archives)
    sql=[]
    for p in sorted(OUT.glob('sql-*.json'))+sorted(FRESH.glob('sql-*.json')):
        r=read(p);assert r['status']=='EXECUTED' and r['integration']['cleanup'] and 'source:commit' not in r['storageTrace']
        sql.append({**receipt(p),'integration':r['integration'],'storageDiagnostics':r['storageDiagnostics']})
    mutations=[]
    for p in sorted(OUT.glob('assessment-mutation-*.json'))+sorted(FRESH.glob('mutation-assessment-*.json')):
        r=read(p);assert r['status']=='FAIL' and r['engine']=='SBSL';mutations.append({**receipt(p),**r})
    source_paths=['element_test/'+p for p in ('bridge.py','execution_plan.py','generated_types.py','observations.py',
        'query_api.py','query_columns.py','query_construction.py','query_plan.py','query_projections.py','query_results.py',
        'query_runtime_text.py','query_sources.py','query_text.sbsl','renderer.py','storage_queries.py')]
    source_paths+=['tests/test_query_results_completion.py','pyproject.toml']
    sources=[receipt(REPO/p) for p in source_paths]
    sources+=[receipt(p) for p in sorted((REPO/'tests/corpus/result-resources').rglob('*')) if p.is_file() and '__pycache__' not in p.parts]
    report={'stage':44,'date':'2026-10-05','status':'completed-with-explicit-platform-emulation',
        'userScope':'Close remaining limitations using emulated/fake platform mechanisms without a platform app.',
        'historicalMeasurements':'docs/query-stage-044-measurements.json','nativePlatformRun':False,
        'reference':read(REPO/'tests/corpus/result-resources/completion-reference-9.3.json'),
        'tests':{'distinctSuccessfulScopedMethods':len(latest),'completionMethods':sum(k.startswith('test_query_results_completion.') for k in latest),
            'latestSuccessfulEvidence':latest,'logs':logs,'failuresAndRepeatsRetained':failures},
        'public':[{'folder':str(folder.relative_to(REPO)),**{k:r[k] for k in ('sourceHash','score','maxScore','unavailablePoints')},
            'checks':[c['status'] for c in r['checks']]} for folder in folders[:4] for r in [read(folder/'result.json')]],
        'batches':batches,'schemas':schema,'extensions':extensions,'catalog':coverage,
        'sql':sql,'mutations':mutations,'alternative':read(FRESH/'assessment-alternative.json'),
        'recovery':{'failed':receipt(FRESH/'infrastructure-failure.json'),'restored':receipt(FRESH/'assessment-infrastructure-recovery.json')},
        'native':read(OUT/'native-probes/results.json'),'archives':archives,'sourceArtifacts':sources,
        'finalSmoke':read(OUT/'smoke.json'),'packaging':{'status':'PASS','evidence':receipt(OUT/'completion-wheel.stdout'),'wheels':[receipt(p) for p in sorted((OUT/'wheels').glob('*.whl'))]},
        'savedEvidence':[receipt(p) for p in sorted(OUT.rglob('*.json')) if 'new-public-project' not in p.parts],
        'preserved':['four archives','previous expected','runtime/input limits','CI schedule'],
        'localExclusions':['full discovery','303 forms','44 previous roots','four archive validation passes','full regression suite','native platform run']}
    write(REPO/'docs/query-stage-044-completion-measurements.json',report)
    print({'tests':len(latest),'newCriteria':len(extensions),'mutations':len(mutations),'sql':len(sql),'schemas':schema['validatedArtifacts'],'refreshedContracts':9})

if __name__=='__main__':main()
