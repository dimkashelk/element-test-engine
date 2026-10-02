"""Record verified stage 35 measurements and the rebuilt coverage snapshot."""
from pathlib import Path
from hashlib import sha256
import json
import shutil

REPO=Path(__file__).resolve().parents[3]
OUT=REPO/'result/dvizhok-welcome-calendar'

def read(name):return json.loads((OUT/name).read_text())
def main():
    full=read('full-regression.json')
    assert full['successful'] and full['testsRun']==246 and full['skipped']==0
    coverage=read('coverage/coverage.json')
    assert coverage['counts']['assessedMethods']==35
    assert (coverage['counts']['files'],coverage['counts']['methods'])==(75,44)
    previous=read('previous-forms/result.json');business=read('previous-business/result.json')
    assert (previous['score'],previous['maxScore'],previous['unavailablePoints'])==(74,74,3)
    assert (business['score'],business['maxScore'],business['unavailablePoints'])==(22,22,0)
    roots=set()
    for command in ['test','run']:
        result=read(command+'/result.json')
        assert len(result['checks'])==70 and all(c['status']=='PASS' for c in result['checks'])
        assert (result['score'],result['maxScore'],result['unavailablePoints'])==(70,70,0)
        for p in read(command+'/execution-plans.json'):
            s=p['plan']['entry'];roots.add((s['namespace'],s['owner'],s['declaration']))
    assert len(roots)==8
    mutations=read('mutations.json')
    assert len(mutations)==13 and sum(len(m['result']['checks']) for m in mutations)==19
    assert all(c['status']=='FAIL' for m in mutations for c in m['result']['checks'])
    for name in ['batch-first','batch-fresh']:
        batch=read(name+'/batch-result.json')
        assert batch['counts']=={'passed':2,'failed':1,'incomplete':0,'inputError':0}
        assert not any(i['cacheHit'] for i in batch['submissions'])
    assert read('archive-hashes-before.json')==read('archive-hashes-after.json')
    assert all(i['exitCode']==0 for i in read('validate.json'))
    assert read('schema-verification.json')['successful']
    cleanup=read('cleanup.json')
    assert not cleanup['testContainers'] and not cleanup['testNetworks']
    assert 'check-moodle-rest-1' in cleanup['preservedContainers']
    assert 'check_default' in cleanup['preservedNetworks']
    summary={'stage':35,'date':'2026-10-02','publicChecks':70,'newDirectRoots':8,
      'previousFormChecks':77,'previousBusinessChecks':22,'mutations':13,'mutationFailures':19,
      'portableProjects':{'options':3,'preferences':3},'batchPassed':2,'batchMutatedFailed':1,
      'schemas':read('schema-verification.json')['validated'],'archivesUnchanged':4,
      'coverage':coverage['counts'],'regression':full,'archiveHashes':read('archive-hashes-after.json'),
      'evidenceHashes':{p:sha256((OUT/p).read_bytes()).hexdigest() for p in [
        'full-regression.log','target-tests.log','mutations.json','public-equivalence.json',
        'schema-verification.json','cleanup.json','baseline-planner.json']}}
    (OUT/'final-verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    (REPO/'docs/dvizhok-stage-035-measurements.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    for kind in ['json','md']:
        shutil.copyfile(OUT/('coverage/coverage.'+kind), REPO/('docs/dvizhok-coverage-after-035.'+kind))
    print(json.dumps(summary,ensure_ascii=False))

if __name__=='__main__':main()
