"""Rebuild stage-38 evidence map from unchanged archive and real public journals."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from element_test.assignment import load_assignment
from element_test.coverage import inventory, markdown
from element_test.loader import open_project
from element_test.model import analyze

REPO=Path(__file__).resolve().parents[3]
OUT=REPO/'result/dvizhok-declarative-bindings'
ASSIGNMENT=REPO/'assignments/dvizhok-declarative-bindings'


def main():
    assignment=load_assignment(ASSIGNMENT)
    with open_project(REPO/'Dvizhok.xdump') as root:
        model=analyze(root)
        data=inventory(root,model,[ASSIGNMENT],[OUT/'test',OUT/'run',OUT/'properties-test-final',OUT/'properties-run-final'])
    previous=json.loads((REPO/'docs/dvizhok-coverage-after-037.json').read_text())
    assert previous['sourceHash']==data['sourceHash']
    data['methods']=deepcopy(previous['methods'])
    for method in data['methods']:
        method['evidenceOrigin']='Historical task 36/37; no full replay in task 38'
        method['freshChecks38']=[]
    for name in ['test','run']:
        results={c['id']:c for c in json.loads((OUT/name/'result.json').read_text())['checks']}
        for item in json.loads((OUT/name/'execution-plans.json').read_text()):
            plan=item.get('plan',{});entry=plan.get('entry')
            if not entry:continue
            calls={s['call']['method'] for s in plan.get('bindingSteps',[]) if 'call' in s}
            for method in data['methods']:
                if method['identity']['sourceFile']==entry['source_file'] and method['identity']['method'] in calls:
                    method['freshChecks38'].append({'criterionId':item['criterionId'],'status':results[item['criterionId']]['status'],
                        'evidence':str(OUT/name),'assessment':'binding value after explicitly invoked source method'})
    for key in ['directTargets','preparedDirectTargets','withoutPreparedDirectScenario','assessedMethods','withoutDirectScenario']:
        data['counts'][key]=previous['counts'][key]
    data.update(archive='Dvizhok.xdump',archiveSha256=sha256((REPO/'Dvizhok.xdump').read_bytes()).hexdigest(),
                methodHistory='docs/dvizhok-coverage-after-037.json')
    expressions=[e for f in data['files'] for e in f.get('yamlBindings',[])]
    commands=[e for e in expressions if e['yamlPath'][-1].isdigit() or e['yamlPath'][-1]=='ОсновнаяКоманда']
    values=[e for e in expressions if e not in commands]
    assert len(commands)==128 and len(values)==133
    assert all(any(c['independentlyAssessed'] for c in e['structure']) for e in commands)
    assert all(any(c['independentlyAssessed'] for c in e['runtime']) for e in values)
    data['counts'].update(forms=16,valueExpressions=133,commandReferences=128,tables=5,ownHandlerBindings=14,
        describedForms=16,assessedValueExpressions=133,assessedCommandReferences=128,
        freshMethods38=sum(bool(m['freshChecks38']) for m in data['methods']))
    (REPO/'docs/dvizhok-coverage-after-038.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    (REPO/'docs/dvizhok-coverage-after-038.md').write_text(markdown(data),encoding='utf-8')
    public=json.loads((OUT/'public-verification.json').read_text())
    measurements={'schemaVersion':1,'date':'2026-10-03','counts':data['counts'],'public':public,
       'checks':len(assignment['checks']),'normalizedRequirements':len({c['formRequirement']['requirement']['id'] for c in assignment['checks']}),
       'targetedTests':{'newTests':12,'newTestLog':'result/dvizhok-declarative-bindings/target-tests-complete.log',
                       'additionalPlannerTests':2,'finalPlannerTests':8,
                       'additionalPlannerLog':'result/dvizhok-declarative-bindings/planner-contract-final.log',
                       'affectedExistingTests':30,'affectedExistingLog':'result/dvizhok-declarative-bindings/affected-existing-tests.log'},
       'opening':{'backend':'PostgreSQL','status':'PASS','journal':'result/dvizhok-declarative-bindings/opening/runtime-evidence.json'},
       'initialFailures':[{'journal':'result/dvizhok-declarative-bindings/test-initial','pass':451,'unsupported':1,'fail':1,
            'causes':['Session renderer called twice','Union ownership dropped by direct native serialization']},
           {'journal':'result/dvizhok-declarative-bindings/retry','pass':1,'error':1,'cause':'Welcome fixture omitted explicit ru-RU locale'},
           {'journal':'result/dvizhok-declarative-bindings/target-tests-final.log','cause':'Constant division by zero compiled as an error; probe changed to runtime fixture divisor'}],
       'limitsChanged':False,'fullRegression':'NOT_RUN','all44MethodsReplay':'NOT_RUN','fourValidate':'NOT_RUN',
       'nativeBrowserUi':'NOT_CHECKED','batchScope':'Five portable criteria, not full Dvizhok assignment',
       'resourceCleanup':json.loads((OUT/'resource-cleanup.json').read_text())}
    (REPO/'docs/dvizhok-stage-038-measurements.json').write_text(json.dumps(measurements,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(data['counts'],ensure_ascii=False))

if __name__=='__main__':main()
