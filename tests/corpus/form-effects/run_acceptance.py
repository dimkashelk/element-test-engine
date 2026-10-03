"""Sequential task 36 acceptance; full regression is a separate final command."""
from pathlib import Path
import json,os,secrets,sys,time,unittest
REPO=Path(__file__).resolve().parents[3]
def main():
    os.environ['ELEMENT_TEST_DOCKER_TESTS']='1'
    os.environ['ELEMENT_TEST_INTEGRATION_TESTS']='1'
    os.environ['ELEMENT_TEST_INTEGRATION_PASSWORD']=secrets.token_hex(24)
    sys.path[:0]=[str(REPO),str(REPO/'tests')]
    out=REPO/'result/dvizhok-form-effects';out.mkdir(exist_ok=True)
    suite=unittest.defaultTestLoader.discover(str(REPO/'tests'),pattern='test_form_effects.py')
    started=time.monotonic()
    with (out/'target-tests.log').open('w') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    summary={'testsRun':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
             'skipped':len(result.skipped),'seconds':round(time.monotonic()-started,3),'successful':result.wasSuccessful(),
             'failureTests':[str(t) for t,_ in result.failures],'errorTests':[str(t) for t,_ in result.errors]}
    (out/'target-tests.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))
    return 0 if result.wasSuccessful() and not result.skipped else 1
if __name__=='__main__':sys.exit(main())
