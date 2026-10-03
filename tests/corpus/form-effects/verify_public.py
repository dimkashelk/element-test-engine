"""Fresh previous 35 direct roots, four validates and rebuilt archive coverage."""
from pathlib import Path
import json,os,secrets,subprocess,sys
REPO=Path(__file__).resolve().parents[3];OUT=REPO/'result/dvizhok-form-effects'
def main():
    full=json.loads((OUT/'full-regression.json').read_text());assert full['successful'] and not full['skipped']
    env={**os.environ,'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}
    output=[]
    for assignment,name,expected in [('dvizhok-form-context','previous-forms',2),('dvizhok-welcome-calendar','previous-welcome',0),('dvizhok-runtime-regression','previous-business',0)]:
        target=OUT/name
        if target.exists():
            import shutil
            shutil.rmtree(target)
        command=[str(REPO/'bin/element-test'),'test','--project',str(REPO/'Dvizhok.xdump'),'--assignment',str(REPO/'assignments'/assignment),'--output',str(target)]
        p=subprocess.run(command,capture_output=True,text=True,timeout=900,env=env)
        (OUT/(name+'.stdout')).write_text(p.stdout);(OUT/(name+'.stderr')).write_text(p.stderr)
        assert p.returncode==expected,(assignment,p.returncode,p.stderr)
        print(name,'complete',flush=True)
    validate=OUT/'validate';validate.mkdir(exist_ok=True)
    for archive in sorted(REPO.glob('*.xdump')):
        p=subprocess.run([str(REPO/'bin/element-test'),'validate',str(archive),'--output',str(validate/(archive.stem+'.json'))],capture_output=True,text=True,timeout=60)
        output.append({'archive':archive.name,'exitCode':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
        assert p.returncode==0,(archive,p.stderr)
    (OUT/'validate.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    command=[sys.executable,'-m','element_test.coverage','--project',str(REPO/'Dvizhok.xdump'),'--assignments',str(REPO/'assignments')]
    for name in ['test','run','previous-forms','previous-welcome','previous-business']:command+=['--evidence',str(OUT/name)]
    command+=['--output',str(OUT/'coverage')]
    p=subprocess.run(command,capture_output=True,text=True,timeout=120)
    (OUT/'coverage.stdout').write_text(p.stdout);(OUT/'coverage.stderr').write_text(p.stderr)
    assert p.returncode==0,p.stderr
    containers=subprocess.run(['docker','ps','-a','--format','{{.Names}}'],capture_output=True,text=True,check=True).stdout.splitlines()
    networks=subprocess.run(['docker','network','ls','--format','{{.Name}}'],capture_output=True,text=True,check=True).stdout.splitlines()
    cleanup={'testContainers':[c for c in containers if c.startswith('element-test-')],
             'testNetworks':[n for n in networks if n.startswith('element-test-')],
             'preservedContainers':[c for c in containers if not c.startswith('element-test-')],
             'preservedNetworks':[n for n in networks if not n.startswith('element-test-')]}
    (OUT/'cleanup.json').write_text(json.dumps(cleanup,indent=2)+'\n')
    assert not cleanup['testContainers'] and not cleanup['testNetworks']
    print('Four validates, coverage and cleanup verified',flush=True)
if __name__=='__main__':main()
