"""Record exact scoped receipts; do not run discovery or infer assessment from AST."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

REPO=Path(__file__).resolve().parents[3]
OUT=REPO/'result/query-040-042-completion'


def read(path):return json.loads(path.read_text())
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def evidence(path):return {'file':str(path.relative_to(REPO)),'sha256':sha256(path.read_bytes()).hexdigest()}


def main():
    from jsonschema import Draft202012Validator
    from element_test.execution_plan import plan_execution
    from element_test.model import analyze
    from element_test.query_catalog import markdown
    from query_completion_fixtures import CORPUS,JOIN_ANSWERS,config
    from projection_fixtures import documented_check
    from composite_fixtures import check

    log_names=sys.argv[1:] or ['acceptance-final.stderr','boundaries-final.stderr','affected-final.stderr','results-plan-final.stderr','real-sales-sql-final.stderr','temporary-null-repeat-final.stderr','date-boundary-final.stderr']
    methods={}
    for name in log_names:
        body=(OUT/name).read_text()
        assert re.search(r'^OK$',body,re.M),name
        for match in re.finditer(r'^test_\w+ \(([^)]+)\) \.\.\. (ok|FAIL|ERROR)',body,re.M):
            methods[match[1]]={'status':match[2],'evidence':str((OUT/name).relative_to(REPO))}
    assert all(v['status']=='ok' for v in methods.values())
    assert sum(k.startswith('test_query_completion.') for k in methods)==13
    assert len(methods)>=48

    baselines={s:read(REPO/f'docs/query-stage-{s}-baseline.json') for s in ('040','041','042')}
    selected={r['contractId'] for b in baselines.values() for r in b['contracts']}
    archives=[{**a,'unchanged':sha256((REPO/a['file']).read_bytes()).hexdigest()==a['sha256']} for a in baselines['040']['archives']]
    assert all(a['unchanged'] for a in archives)
    catalog_path=REPO/'docs/query-contract-catalog.json';catalog=read(catalog_path)
    untouched={r['contractId']:json.dumps(r,sort_keys=True) for r in catalog['contracts'] if r['contractId'] not in selected}
    records={r['contractId']:r for r in catalog['contracts']}
    added=[]

    def attach_template(record,root,criterion,receipt_path,runtime_path,configuration):
        fixture=REPO/record['fixture'];receipt=read(receipt_path);runtime=read(runtime_path)
        model=analyze(root);plan=plan_execution(root,model,configuration);q=plan.queries[0]
        assert fixture.read_text().strip()==record['text'].strip()==q['text'].strip()
        source_hash=sha256((root/q['sourceFile']).read_bytes()).hexdigest()
        assert receipt['engine']=='SBSL' and receipt['status']=='PASS' and runtime['status']=='EXECUTED'
        assert receipt['sourceHash']==source_hash and receipt['projectSourceHash']==model['sourceHash']
        # Legacy task-42 receipts contain a historical hard-coded runtime path.
        # Correct only the freshly written pointer, retaining historical outputs.
        receipt['runtime']=str(runtime_path.relative_to(REPO));write(receipt_path,receipt)
        fixture_hash=sha256(fixture.read_bytes()).hexdigest()
        record.update(ast=q['ast'],parsed=True,planned=True,executed=True,independentlyAssessed=True,
            unsupportedReason=None,fixtureStatus='executable-transferable-project',fixtureHash=fixture_hash,
            referenceVersion='9.3',semantics=['executor-040-042-completion','element-reference-9.3','script-current'],
            criteria=[{'criterionId':criterion,'direct':True,'status':'PASS','origin':'fresh-040-042-completion',
                'sourceHash':source_hash,'projectSourceHash':model['sourceHash'],'sourceFile':q['sourceFile'],
                'range':[q['start'],q['end']],'fixtureHash':fixture_hash}],
            evidence=[str(receipt_path.relative_to(REPO)),str(runtime_path.relative_to(REPO))])

    for label in JOIN_ANSWERS:
        criterion='join_'+label.replace('-','_');record=records['documented-'+label]
        attach_template(record,CORPUS/'ordinary',criterion,OUT/('assessment-ordinary-'+criterion+'.json'),
                        OUT/('ordinary-'+criterion+'.json'),config(criterion))
        added.append(record['contractId'])
    for label,criterion in {'computed-projection':'ComputedProjection','group':'Group','having':'Having',
                            'case':'Case','combined-join-group-null':'CombinedJoinGroupNull'}.items():
        attach_template(records['documented-'+label],REPO/'tests/corpus/projections-aggregates/documented',criterion,
                        OUT/'fresh-041'/('documented-assessment-'+criterion+'.json'),
                        OUT/'fresh-041'/('documented-'+criterion+'.json'),documented_check(criterion))
    for label in ('distinct','union','union-all','nested','compound'):
        criterion=label.replace('-','_')
        attach_template(records['documented-'+label],REPO/'tests/corpus/unions-nesting/documented',criterion,
                        OUT/'fresh-042'/('documented-assessment-'+criterion+'.json'),
                        OUT/'fresh-042'/('documented-'+criterion+'.json'),check(method=criterion))

    receipt_path=OUT/'assessment-external-sales.json';receipt=read(receipt_path);record=records[receipt['contractId']]
    frozen=next(r for r in baselines['041']['contracts'] if r['contractId']==record['contractId'])
    for key in ('sourceHash','projectSourceHash','file','range'):assert record[key]==frozen[key]==receipt[key]
    for suffix in ('','-empty'):
        value=read(OUT/('assessment-external-sales'+suffix+'.json'))
        assert value['engine']=='SBSL' and value['status']=='PASS' and read(REPO/value['runtime'])['status']=='EXECUTED'
    record.update(ast=receipt['ast'],parsed=True,planned=True,executed=True,independentlyAssessed=True,unsupportedReason=None,
        criteria=[{'criterionId':'external-sales'+suffix,'status':'PASS','direct':True,'origin':'fresh-041-completion',
            **{k:receipt[k] for k in ('sourceHash','projectSourceHash','range','executionProjectSourceHash','wrapperSourceHash','mode','nativeReportLifecycle')},
            'sourceFile':receipt['file']} for suffix in ('','-empty')],
        evidence=[str((OUT/('assessment-external-sales'+suffix+'.json')).relative_to(REPO)) for suffix in ('','-empty')]
                 +[read(OUT/('assessment-external-sales'+suffix+'.json'))['runtime'] for suffix in ('','-empty')])
    added.append(record['contractId'])

    public_folders=[OUT/('public-new-'+m) for m in ('test','run')]+[OUT/'public-control']
    public_folders += [OUT/'fresh-041'/('public-'+label+'-'+mode) for label in ('tasks','max') for mode in ('test','run')]
    public_folders += [OUT/'fresh-041/public-control']+[OUT/'fresh-042'/('public-real-'+m) for m in ('test','run')]
    public_folders += [OUT/'fresh-042/public-control']+[OUT/'fresh-040'/('public-cases-'+m) for m in ('test','run')]
    public=[]
    for folder in public_folders:
        result=read(folder/'result.json')
        public.append({'folder':str(folder.relative_to(REPO)),**{k:result[k] for k in ('sourceHash','score','maxScore','unavailablePoints')},
                       'checks':[c['status'] for c in result['checks']]})
        if not (folder/'execution-plans.json').exists():continue
        results={c['id']:c for c in result['checks']};runtimes={r.get('criterionId'):r for r in read(folder/'runtime-evidence.json')}
        for item in read(folder/'execution-plans.json'):
            plan=item.get('plan',item);criterion=item.get('criterionId')
            for q in plan.get('queries',[]):
                for record in records.values():
                    if record['contractId'] not in selected or not record.get('target'):continue
                    if record.get('projectSourceHash')!=result['sourceHash'] or record['file']!=q['sourceFile'] or record['range']!=[q['start'],q['end']]:continue
                    assert sha256(record['text'].encode()).hexdigest()==q['sourceHash'] and runtimes[criterion]['status']=='EXECUTED' and results[criterion]['status']=='PASS'
                    entry=plan.get('entry',{})
                    assert entry.get('declaration')==record['target']['method'] and entry.get('source_file')==record['file']
                    entry={'criterionId':criterion,'status':'PASS','direct':True,'origin':'fresh-040-042-completion-public',
                           'sourceHash':record['sourceHash'],'projectSourceHash':result['sourceHash'],'sourceFile':q['sourceFile'],'range':record['range'],
                           'evidence':str(folder.relative_to(REPO))}
                    record['criteria']=[c for c in record['criteria'] if not (c.get('origin')==entry['origin'] and c.get('evidence')==entry['evidence'] and c['criterionId']==criterion)]+[entry]
                    folder_name=str(folder.relative_to(REPO))
                    if folder_name not in record['evidence']:record['evidence'].append(folder_name)

    for record in records.values():
        if record['contractId'] in selected and record['family']=='documentation-index':
            record['unsupportedReason']='Documentation inventory has no exact executable source/range/criterion; supported signatures and temporary field semantics are recorded in the 040-042 completion contract'
    reference={'version':'9.3','executorVersion':'10.0.2-1','executionCompatibilityVersion':'current','nativeXBQL':False,
               'sources':['https://1cmycloud.com/console/help/element/9.3/docs/topics/'+s+'/' for s in
                         ('first-not-null-function','full-match-function','uuid-function','math-and-trigonometric-functions','create-temporary-table-statement')]}
    catalog['stage040042CompletionReference']=reference
    assert untouched=={r['contractId']:json.dumps(r,sort_keys=True) for r in catalog['contracts'] if r['contractId'] not in selected}
    coverage={}
    for stage,baseline in baselines.items():
        ids={r['contractId'] for r in baseline['contracts']};scope=[r for r in catalog['contracts'] if r['contractId'] in ids]
        report={'stage':stage+'-completion','scopeCount':len(scope),'counts':{k:sum(bool(r.get(k)) for r in scope) for k in ('parsed','planned','executed','independentlyAssessed')},
                'archives':archives,'reference':reference,'newExactAssessments':[i for i in added if i in ids],
                'contracts':scope,'remainingCauses':dict(Counter(r.get('unsupportedReason') for r in scope if r.get('unsupportedReason')))}
        coverage[stage]={k:report[k] for k in ('scopeCount','counts','newExactAssessments')}
        write(REPO/f'docs/query-stage-{stage}-completion-coverage.json',report)
    write(catalog_path,catalog)
    rendered=markdown(catalog)
    notes='Дополнение №40–42: шесть точных JOIN/NULL templates и внешний ПродажиПоНеделям получили независимую оценку; расширены функции и временные поля. [Измерения](query-stage-040-042-completion-measurements.json).\n\n'
    notes+='Дополнение №44: девять точных критериев получили свежие свидетельства. `ЗапросСВыборкой` проверен как явный эмулятор; native-сигнатура не заявляется. [Измерения дополнения](query-stage-044-completion-measurements.json).\n\n'
    rendered=rendered.replace('P — parsed',notes+'P — parsed');catalog_path.with_suffix('.md').write_text(rendered)

    count=0
    def validate(path,schema):
        nonlocal count
        Draft202012Validator(read(REPO/'docs'/schema)).validate(read(path));count+=1
    batch_folders=[REPO/p for p in read(OUT/'batch-latest.json')];batches=[]
    for folder in batch_folders:
        result=read(folder/'batch-result.json');assert result['status']=='completed'
        scores=[s['score'] for s in result['submissions']];assert scores==[3,0,3] and not any(s['cacheHit'] for s in result['submissions'])
        batches.append({'folder':str(folder.relative_to(REPO)),'scores':scores,'cacheHits':[s['cacheHit'] for s in result['submissions']]})
        validate(folder/'batch-result.json','batch-result-v1.schema.json')
        validator=Draft202012Validator(read(REPO/'docs/batch-events-v1.schema.json'))
        for line in (folder/'events.jsonl').read_text().splitlines():validator.validate(json.loads(line));count+=1
    for folder in public_folders+batch_folders:
        for path in folder.rglob('grading.json'):validate(path,'grading-v1.schema.json')
    validate(OUT/'batch-manifest.json','batch-manifest-v1.schema.json')
    sql=[]
    for path in sorted(OUT.glob('sql-*.json')):
        r=read(path);assert r['status']=='EXECUTED' and r['integration']['cleanup'] and 'source:commit' not in r['storageTrace']
        sql.append({**evidence(path),'status':'PASS','audit':'independent PostgreSQL read and SBSL expected','cleanup':True})
    mutations=[]
    for path in sorted(OUT.glob('assessment-mutation-*.json')):
        r=read(path);assert r=={'engine':'SBSL','status':'FAIL'}
        runtime=OUT/path.name.removeprefix('assessment-');assert read(runtime)['status']=='EXECUTED'
        mutations.append({'assessment':evidence(path),'runtime':evidence(runtime),'status':'FAIL'})
    assert len(mutations)==6 and len(sql)==4
    logs=sorted(OUT.glob('*.stderr'),key=lambda p:p.stat().st_mtime)
    primary=[p for p in OUT.glob('*.json') if p.name.startswith(('assessment-','sql-'))]
    report={'stages':[40,41,42],'date':'2026-10-05','status':'completed-within-explicit-executor-contract',
        'reference':reference,'historicalMeasurements':[f'docs/query-stage-{s}-measurements.json' for s in baselines],
        'tests':{'distinctSuccessfulMethods':len(methods),'newMethods':13,'affectedLegacyMethods':len(methods)-13,
                 'latestSuccessfulEvidence':methods,'logs':[evidence(OUT/n) for n in log_names]},
        'catalog':coverage,'newExactAssessments':added,'public':public,'batches':batches,'sql':sql,'mutations':mutations,
        'schemaValidation':{'status':'PASS','validatedArtifacts':count},'archives':archives,
        'evidence':[evidence(p) for p in sorted(primary)],
        'initialFailuresAndRepeats':[evidence(p) for p in logs if p.name not in log_names],
        'nativeProbes':[evidence(OUT/n) for n in ('native-functions.stdout','native-functions.stderr','native-pattern.stdout','native-pattern.stderr')],
        'limitations':['Native XBQL and platform report/file/resource lifecycle not executed',
                       'SQL NULL and Undefined have one JSON null representation outside the relational AST',
                       'Math power uses nine fraction digits for bases without a statically known fraction qualifier',
                       'Temporary union types, default depending on auto field and nondeterministic/default captured expressions unsupported',
                       'Temporary auto counter is sequential per Execute; no concurrent/native insertion claim',
                       'Query-only typed snapshot omits implicit Files; object loading and Files queries remain unsupported',
                       'Physical indexes and complete query catalog remain outside verified scope'],
        'localExclusions':['full discovery','303 forms','44 former roots','four validate','full regression','nightly execution'],
        'preserved':['four source archives','previous assignment expected','runtime/input limits','CI schedule','historical measurement files']}
    report['evidenceRouting']={'tasks040042':'all affected receipts redirected to fresh folders',
        'initialLegacyDeviation':'Initial affected task-44 plan and task-45 state tests wrote their original evidence paths; later runner also redirects 044/045. Historical measurement files and their logs remain unchanged.'}
    write(REPO/'docs/query-stage-040-042-completion-measurements.json',report)
    print(json.dumps({'tests':len(methods),'catalog':coverage,'schemaArtifacts':count},ensure_ascii=False))


if __name__=='__main__':main()
