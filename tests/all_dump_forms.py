"""Frozen reference form corpus. Expected is authored offline, never at runtime."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from element_test.bridge import write_json
from element_test.declarative_bindings import preflight
from element_test.form_requirements import expand_requirements
from element_test.runtime import execute_engine, run_pure

REPO=Path(__file__).resolve().parents[1]
CORPUS=REPO/'tests/corpus/all-dump-forms'
MANIFEST=json.loads((CORPUS/'manifest.json').read_text())


def load_fixture(path):
    return json.loads((CORPUS/path).read_text())


def find_form(model,identity):
    matches=[e for e in model['elements'] if e['elementType']=='КомпонентИнтерфейса'
             and all(e[k]==v for k,v in identity.items())]
    if len(matches)!=1:raise AssertionError('Missing or ambiguous reference form: '+str(identity))
    return matches[0]


def checks_for(data):
    checks=deepcopy(data['existingChecks'])
    if data['runtimeContract']:checks+=expand_requirements(data['runtimeContract'])
    return checks


def declaration_fact(model,data):
    form=find_form(model,data['form'])
    module=next((m for m in model['modules'] if m['sourceFile']==form['sourceFile'].removesuffix('.yaml')+'.xbsl'),None)
    signatures=[{k:v for k,v in method.items() if k!='line'} for method in module['methods']] if module else []
    return {'declaration':form['properties'],'moduleSignatures':signatures}


def grade_form(root,model,data,output,*,execute_bindings=True):
    """SBSL compares both the fixed declaration baseline and runtime values."""
    output.mkdir(parents=True,exist_ok=True)
    entries=[{'id':'reference-declaration','type':'runtime','points':1,
              'expected':{k:deepcopy(data[k]) for k in ('declaration','moduleSignatures')},
              'execution':{'status':'EXECUTED','actual':declaration_fact(model,data)}}]
    evidence=[];plans=[]
    if execute_bindings:
        for check in checks_for(data):
            sink=[]
            check['execution']=preflight(root,model,check) or run_pure(root,model,check,output,plan_sink=sink)
            plans.extend({'criterionId':check['id'],'plan':plan} for plan in sink)
            evidence.append({'criterionId':check['id'],**check['execution']})
            entries.append(check)
    write_json(output/'model.json',model)
    write_json(output/'assignment.json',{'name':data['description'],'checks':entries})
    result=execute_engine('test',output/'model.json',output/'assignment.json',output)
    write_json(output/'result.json',result)
    write_json(output/'runtime-evidence.json',evidence)
    write_json(output/'execution-plans.json',plans)
    write_json(output/'coverage.json',{'form':data['form'],'declaration':'CHECKED',
       'status':'passed' if all(c['status']=='PASS' for c in result['checks']) else 'failed',
       'gradedChecks':len(result['checks']),
       'runtimeChecksExecuted':len(evidence),'expressions':len(data['expressions']),
       'notRuntimeChecked':data['unavailable'],'nativeBrowserUi':'NOT_CHECKED'})
    return result


def coverage_summary():
    projects=[]
    for project in MANIFEST['projects']:
        forms=[load_fixture(path) for path in project['forms']]
        checks=[c for f in forms for c in checks_for(f)]
        unavailable=[{'form':f['form'],**e} for f in forms for e in f['unavailable']]
        projects.append({k:project[k] for k in ('dump','project','archiveSha256')} | {
          'forms':len(forms),'declarationChecks':len(forms),
          'runtimeValueScenarios':sum(c.get('formRequirement',{}).get('requirement',{}).get('kind')=='binding' for c in checks),
          'structuralRequirements':sum(c.get('formRequirement',{}).get('requirement',{}).get('kind')=='structure' for c in checks),
          'expressions':sum(len(f['expressions']) for f in forms),'notRuntimeChecked':unavailable})
    return {'schemaVersion':1,'forms':sum(p['forms'] for p in projects),'projects':projects,'execution':'NOT_RUN',
            'nativeBrowserUi':'NOT_CHECKED','sourceOfExpected':'Frozen offline contracts; no reference execution to derive answers'}
