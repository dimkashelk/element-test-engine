"""Record scoped evidence; never rerun discovery or infer PASS from parsing."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
import unittest

REPO=Path(__file__).resolve().parents[3]
OUT=REPO/'result/virtual-tables-completion'


def read(path):return json.loads(path.read_text())
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def evidence(path):return {'file':str(path.relative_to(REPO)),'sha256':sha256(path.read_bytes()).hexdigest()}
def cases(suite):
    for child in suite:
        if isinstance(child,unittest.TestSuite):yield from cases(child)
        else:yield child.id()


def main():
    from jsonschema import Draft202012Validator
    from element_test.query_catalog import markdown
    names=sys.argv[1:] or ('affected-regression.log','plan-regression.log','final-runtime.log','final-repeat.log')
    logs=[OUT/name for name in names]
    statuses={}
    for log in logs:
        for match in re.finditer(r'^test_\w+ \(([^)]+)\) \.\.\. (ok|FAIL|ERROR)',log.read_text(),re.M):
            statuses[match[1]]=match[2]
    selectors=['test_query_sources','test_virtual_extensions',
               'test_query_joins.JoinPlanTest','test_query_projections.ProjectionPlanTest',
               'test_query_composites.CompositePlanTest','test_query_results.ResultPlanTest',
               'test_query_composites.CompositeEdgeTest.test_hidden_null_column_collision_and_nullable_alias_comparison']
    required=set(cases(unittest.defaultTestLoader.loadTestsFromNames(selectors)))
    assert all(statuses.get(name)=='ok' for name in required),{name:statuses.get(name) for name in required if statuses.get(name)!='ok'}
    baseline=read(REPO/'docs/query-stage-043-baseline.json')
    archives=[{**entry,'unchanged':sha256((REPO/entry['file']).read_bytes()).hexdigest()==entry['sha256']} for entry in baseline['archives']]
    assert all(a['unchanged'] for a in archives)
    catalog_path=REPO/'docs/query-contract-catalog.json';catalog=read(catalog_path)
    selected={r['contractId']:r for r in baseline['contracts']}
    attached=[]
    for index in range(3):
        path=OUT/('assessment-external-'+str(index)+'.json');receipt=read(path)
        entry=next(r for r in catalog['contracts'] if r['contractId']==receipt['contractId'])
        frozen=selected[entry['contractId']]
        for key in ('sourceHash','projectSourceHash','file','range'):
            assert entry[key]==frozen[key]==receipt[key]
        assert receipt['status']=='PASS' and receipt['engine']=='SBSL' and receipt['storageRowsUnchanged']
        assert read(REPO/receipt['runtime'])['status']=='EXECUTED'
        criterion={'criterionId':'external-'+str(index),'status':'PASS','direct':True,
                   'origin':'fresh-043-completion-query-api',
                   **{k:receipt[k] for k in ('sourceHash','projectSourceHash','range','executionProjectSourceHash','wrapperSourceHash','mode','nativeReportLifecycle')},'sourceFile':receipt['file']}
        entry.update(ast=receipt['ast'],parsed=True,planned=True,executed=True,independentlyAssessed=True,unsupportedReason=None,
                     criteria=[criterion],evidence=[str(path.relative_to(REPO)),receipt['runtime']],
                     semantics=['exact-external-query-through-api','element-reference-9.3','script-current','native-report-lifecycle-unavailable'])
        attached.append(entry['contractId'])
    # Family support does not assess a documentation-only inventory occurrence.
    # Remove its obsolete no-parameter/non-null limitation, retaining all flags.
    saved_inventory=next(r for r in catalog['contracts'] if r['contractId']=='documentation-table-virtualtablename_ru')
    assert saved_inventory['contractId'] in selected and not saved_inventory['independentlyAssessed']
    saved_inventory['unsupportedReason']='Documentation inventory has no exact executable source/range/criterion; portable family tests do not assess this inventory occurrence'
    scope=[r for r in catalog['contracts'] if r['contractId'] in selected]
    coverage={'stage':'043-completion','scopeCount':len(scope),
              'counts':{key:sum(bool(r.get(key)) for r in scope) for key in ('parsed','planned','executed','independentlyAssessed')},
              'freshExternalQueries':attached,'archives':archives,'contracts':scope,
              'remainingCauses':dict(Counter(r.get('unsupportedReason') for r in scope if r.get('unsupportedReason')))}
    write(catalog_path,catalog);catalog_path.with_suffix('.md').write_text(markdown(catalog))
    write(REPO/'docs/query-stage-043-completion-coverage.json',coverage)
    folders=[OUT/'new-public-test',OUT/'new-public-run',OUT/'new-public-control',
             OUT/'previous-contracts/public-real-test',OUT/'previous-contracts/public-real-run',OUT/'previous-contracts/public-control']
    batches=[]
    for pointer in (OUT/'new-batch-latest.json',OUT/'previous-contracts/batch-latest.json'):
        for name in read(pointer):
            folder=REPO/name;folders.append(folder);value=read(folder/'batch-result.json')
            assert value['status']=='completed' and not any(s['cacheHit'] for s in value['submissions'])
            batches.append({'folder':name,'scores':[s['score'] for s in value['submissions']]})
    count=0
    def validate(path,schema):
        nonlocal count
        Draft202012Validator(read(REPO/'docs'/schema)).validate(read(path));count+=1
    for folder in folders:
        for path in folder.rglob('grading.json'):validate(path,'grading-v1.schema.json')
        if (folder/'batch-result.json').exists():
            validate(folder/'batch-result.json','batch-result-v1.schema.json')
            validator=Draft202012Validator(read(REPO/'docs/batch-events-v1.schema.json'))
            for line in (folder/'events.jsonl').read_text().splitlines():validator.validate(json.loads(line));count+=1
    validate(OUT/'previous-contracts/batch-manifest.json','batch-manifest-v1.schema.json')
    primary=[p for p in OUT.rglob('*.json') if 'first-attempt' not in p.parts and (p.name.startswith('assessment-') or p.name.startswith('mutation-assessment-') or p.name.startswith('sql-') or p.name.startswith('native-history'))]
    report={'stage':'043-completion','date':'2026-10-05','status':'executor-expanded-native-acceptance-unavailable',
            'referenceVersion':'9.3','executorVersion':'10.0.2-1','compatibility':'current',
            'targetedTests':{'count':len(required),'status':'PASS','methods':sorted(required),'logs':[evidence(p) for p in logs]},
            'catalogCounts':coverage['counts'],'freshExactExternalQueryIds':attached,
            'batches':batches,'schemaValidation':{'status':'PASS','validatedArtifacts':count},
            'archives':archives,'nativeHistoryLimits':read(OUT/'native-history-limits.json'),
            'evidence':[evidence(p) for p in sorted(primary)],
            'preservedFailures':[evidence(OUT/name) for name in ('first-runtime.log','repeat-runtime.log','extra-runtime.log','affected-regression.log','final-runtime.log') if (OUT/name).exists()],
            'limitations':['No test application on Element: native exchange/settings/cleanup/access lifecycle not verified',
                           'Entity-contract tables removed after platform 7.0',
                           'BalanceAndTurnovers with registrar/record periodization and nullable boundary rows unsupported',
                           'Periodic constants, register key structures, map/set collection sources and user locales pending',
                           'Runtime-computed periodicity, saved query defaults, fill/batch saved definitions and correlated source filters unsupported',
                           'First slices and second/moment slice profiles are explicit executor extensions of the 9.3 Date slice reference',
                           'Calendar week begins Monday in the deterministic executor profile'],
            'localExclusions':['full discovery','303 forms','all 44 former roots','four validate','full regression','nightly execution','native platform compilation']}
    write(REPO/'docs/query-stage-043-completion-measurements.json',report)
    print(json.dumps({'tests':len(required),'catalog':coverage['counts'],'validatedArtifacts':count},ensure_ascii=False))


if __name__=='__main__':main()
