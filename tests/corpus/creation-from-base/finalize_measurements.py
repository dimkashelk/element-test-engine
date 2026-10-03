"""Record targeted acceptance and historical coverage provenance, without full regression."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
from jsonschema import Draft202012Validator

REPO=Path(__file__).resolve().parents[3]
OUT=REPO/'result/dvizhok-remaining-business-roots'
def read(path):return json.loads(path.read_text())
def write(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')

def main():
    prior=read(REPO/'docs/dvizhok-stage-036-measurements.json')
    after={name:sha256((REPO/name).read_bytes()).hexdigest() for name in prior['archiveHashes']}
    assert after==prior['archiveHashes']==read(OUT/'archive-hashes-before.json')
    write(OUT/'archive-hashes-after.json',after)
    results=[];roots=set()
    for command in ['test','run']:
        data=read(OUT/command/'result.json')
        assert len(data['checks'])==70 and all(c['status']=='PASS' for c in data['checks'])
        assert (data['score'],data['maxScore'],data['unavailablePoints'])==(70,70,0)
        results.append([(c['id'],c['status'],c['actual'],c['score']) for c in data['checks']])
        for item in read(OUT/command/'execution-plans.json'):
            e=item['plan']['entry'];roots.add((e['namespace'],e['owner'],e['declaration']))
    assert results[0]==results[1] and len(roots)==6
    public=[]
    for name in ['batch-first','batch-fresh']:
        data=read(OUT/name/'batch-result.json')
        assert data['counts']=={'passed':2,'failed':1,'incomplete':0,'inputError':0}
        assert all(not x['cacheHit'] for x in data['submissions'])
    for name in ['ledgers','accounts']:
        for backend in ['memory','postgres']:
            data=read(OUT/('portable-'+name+'-'+backend)/'result.json')
            assert len(data['checks'])==8 and all(c['status']=='PASS' for c in data['checks'])
    mutations=[]
    for path in sorted((OUT/'mutations').glob('*/result.json')):
        # Exclude superseded diagnostics from the first, pre-final implementation.
        if path.parent.name in {'stale','write','movements','wrong-owner'}:continue
        result=read(path);criterion=result['checks'][0]
        assert criterion['status']=='FAIL',path
        mutations.append({'name':path.parent.name,'status':criterion['status'],'result':str(path.relative_to(REPO))})
    write(OUT/'mutations.json',mutations)
    count={}
    excluded={'primary-evidence','batch-input','portable-smoke','portable-smoke2','transport-probes','register-probe'}
    for schema,filename in [('grading-v1.schema.json','grading.json'),('batch-result-v1.schema.json','batch-result.json'),('batch-manifest-v1.schema.json','batch-manifest.json')]:
        validator=Draft202012Validator(read(REPO/'docs'/schema));paths=[p for p in OUT.rglob(filename) if not excluded.intersection(p.relative_to(OUT).parts)]
        for path in paths:validator.validate(read(path))
        count[filename]=len(paths)
    validator=Draft202012Validator(read(REPO/'docs/batch-events-v1.schema.json'));count['events']=0
    for path in OUT.glob('batch-*/events.jsonl'):
        for line in path.read_text().splitlines():validator.validate(json.loads(line));count['events']+=1
    write(OUT/'schema-verification.json',{'successful':True,'validated':count})
    old=REPO/'result/dvizhok-form-effects'
    evidence=[old/name for name in ['test','run','previous-forms','previous-welcome','previous-business']]+[OUT/'test',OUT/'run']
    command=['python3','-m','element_test.coverage','--project','Dvizhok.xdump','--assignments','assignments']
    for directory in evidence:command+=['--evidence',str(directory)]
    command+=['--output',str(OUT/'coverage')]
    subprocess.run(command,cwd=REPO,check=True)
    coverage=read(OUT/'coverage/coverage.json')
    assert coverage['counts']['assessedMethods']==44
    provenance={'newRoots':6,'historicalRoots':38,'previousRootsReexecuted':False,
                'previousStage':'docs/dvizhok-stage-036-measurements.json',
                'reason':'User requested tests only for changed scope; full regression and historical roots are left to nightly CI.'}
    coverage['evidenceProvenance']=provenance;write(OUT/'coverage/coverage.json',coverage)
    for kind in ['json','md']:
        dest=REPO/('docs/dvizhok-coverage-after-037.'+kind);shutil.copyfile(OUT/('coverage/coverage.'+kind),dest)
        if kind=='md':
            dest.write_text(dest.read_text()+'\nНовые шесть корней проверены в №37. Прежние 38 подтверждены сохранёнными\nжурналами №36; свежий повтор и полная регрессия не запускались по указанию\nпользователя. Их проверяет ночной CI. 44/44 не означает покрытие YAML/UI.\n')
    summary={'stage':37,'date':'2026-10-03','publicChecks':70,'newDirectRoots':6,'previousIndependentMethods':38,
             'coverage':coverage['counts'],'coverageProvenance':provenance,'archiveHashes':after,'archivesUnchanged':4,
             'mutations':len(mutations),'portableProjects':['ledgers','accounts'],'portableChecks':32,
             'batchPassed':2,'batchMutatedFailed':1,'schemas':count,
             'fullRegression':{'executed':False,'reason':'Explicit user instruction: test only changed scope; nightly CI handles full regression.'},
             'targetedRun':read(OUT/'targeted-tests-final.json'),
             'followup':read(OUT/'targeted-followup.json') if (OUT/'targeted-followup.json').exists() else None}
    for backend in ['memory','postgres']:
        statuses=[c['status'] for c in read(OUT/('missing-'+backend)/'result.json')['checks']]
        assert statuses==['ERROR']*4+['PASS']
    repeat=read(OUT/'enum-dependency-repeat.json')
    assert repeat['status']=='passed' and not repeat['failures'] and not repeat['errors'] and not repeat['skipped']
    followup=summary['followup'];assert followup['status']=='passed' and not followup['failures'] and not followup['errors'] and not followup['skipped']
    failures=summary['targetedRun']['failures']
    assert len(failures)==2 and not summary['targetedRun']['errors'] and not summary['targetedRun']['skipped']
    assert any('tag=\'write\'' in item['test'] for item in failures)
    assert any('test_native_context_enum_parameter_and_void_handler' in item['test'] for item in failures)
    summary['enumDependencyRepeat']=repeat
    summary['targetedAcceptance']={'successful':True,'selectedTests':19,'primaryPassed':17,
        'resolvedFailures':2,'followupPassed':10,'enumRepeatPassed':8,'fullRegression':False,
        'resolution':{'writeMutation':'targeted-followup.json','objectContextEnum':'enum-dependency-repeat.json'}}
    summary['cleanup']=read(OUT/'cleanup.json')
    assert not summary['cleanup']['testContainers'] and not summary['cleanup']['testNetworks']
    summary['evidenceHashes']={str(p.relative_to(OUT)):sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.glob('*.log'))}
    write(OUT/'final-verification.json',summary);write(REPO/'docs/dvizhok-stage-037-measurements.json',summary)
    print(json.dumps({'publicChecks':70,'coverage':coverage['counts'],'mutations':len(mutations)},ensure_ascii=False))

if __name__=='__main__':main()
