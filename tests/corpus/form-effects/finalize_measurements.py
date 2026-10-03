"""Finalize verified observations; never infer business passes from source coverage."""
from pathlib import Path
from hashlib import sha256
import json,shutil,subprocess
from jsonschema import Draft202012Validator
REPO=Path(__file__).resolve().parents[3]
OUT=REPO/'result/dvizhok-form-effects'
def read(name):return json.loads((OUT/name).read_text())
def main():
    full=read('full-regression.json')
    assert full['successful'] and full['testsRun']>=246 and not full['skipped'] and not full['failures'] and not full['errors']
    counts={}
    for schema,filename in [('grading-v1.schema.json','grading.json'),('batch-result-v1.schema.json','batch-result.json'),('batch-manifest-v1.schema.json','batch-manifest.json')]:
        validator=Draft202012Validator(json.loads((REPO/'docs'/schema).read_text()))
        paths=list(OUT.rglob(filename))
        for p in paths:validator.validate(json.loads(p.read_text()))
        counts[filename]=len(paths)
    validator=Draft202012Validator(json.loads((REPO/'docs/batch-events-v1.schema.json').read_text()));counts['events']=0
    for p in OUT.rglob('events.jsonl'):
        for line in p.read_text().splitlines():validator.validate(json.loads(line));counts['events']+=1
    write=lambda name,data:(OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    write('schema-verification.json',{'successful':True,'validated':counts})
    after={p.name:sha256(p.read_bytes()).hexdigest() for p in REPO.glob('*.xdump')}
    assert read('archive-hashes-before.json')==after
    write('archive-hashes-after.json',after)
    assert all(i['exitCode']==0 for i in read('validate.json'))
    coverage=read('coverage/coverage.json');assert coverage['counts']['assessedMethods']==38
    assert (coverage['counts']['files'],coverage['counts']['methods'])==(75,44)
    roots=set();public=[]
    for name in ['test','run']:
        data=read(name+'/result.json');assert len(data['checks'])==46
        assert all(c['status']=='PASS' for c in data['checks'])
        assert (data['score'],data['maxScore'],data['unavailablePoints'])==(46,46,0)
        public.append([(c['id'],c['status'],c['actual'],c['score']) for c in data['checks']])
        for p in read(name+'/execution-plans.json'):
            e=p['plan']['entry'];roots.add((e['namespace'],e['owner'],e['declaration']))
    assert public[0]==public[1] and len(roots)==3
    for name in ['batch-first','batch-fresh']:
        data=read(name+'/batch-result.json');assert data['counts']=={'passed':2,'failed':1,'incomplete':0,'inputError':0}
        assert all(not i['cacheHit'] for i in data['submissions'])
    for name in ['previous-forms','previous-welcome','previous-business']:
        data=read(name+'/result.json')
        expected=(74,74,3) if name=='previous-forms' else (70,70,0) if name=='previous-welcome' else (22,22,0)
        assert (data['score'],data['maxScore'],data['unavailablePoints'])==expected
    mutations=read('mutations.json');assert all(m['status']=='FAIL' for m in mutations)
    assert not read('cleanup.json')['testContainers'] and not read('cleanup.json')['testNetworks']
    files=['full-regression.log','target-tests-primary.log','target-repeat.log','final-focused.log','full-regression-primary.log','regression-focused-repeat.log','mutations.json','public-equivalence.json','schema-verification.json','cleanup.json','baseline.json']
    summary={'stage':36,'date':'2026-10-03','publicChecks':46,'newDirectRoots':3,
             'previousIndependentMethods':35,'previousFormChecks':77,'previousWelcomeChecks':70,'previousBusinessChecks':22,
             'mutations':len(mutations),'portableProjects':list(['ledgers','accounts']),'batchPassed':2,'batchMutatedFailed':1,
             'schemas':counts,'archivesUnchanged':4,'coverage':coverage['counts'],'regression':full,'archiveHashes':after,
             'primaryAcceptance':read('target-tests-primary.json'),'focusedRepeat':read('final-focused.json'),
             'primaryRegression':read('full-regression-primary.json'),'regressionFocusedRepeat':read('regression-focused-repeat.json'),
             'evidenceHashes':{name:sha256((OUT/name).read_bytes()).hexdigest() for name in files}}
    write('final-verification.json',summary)
    (REPO/'docs/dvizhok-stage-036-measurements.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    for kind in ['json','md']:shutil.copyfile(OUT/('coverage/coverage.'+kind),REPO/('docs/dvizhok-coverage-after-036.'+kind))
    print(json.dumps({k:summary[k] for k in ['stage','publicChecks','coverage','regression']},ensure_ascii=False))
if __name__=='__main__':main()
