"""Schema, immutable archive, public parity and scoped batch verification."""
from hashlib import sha256
import json
from pathlib import Path
import sys
import yaml
from jsonschema import Draft202012Validator

REPO=Path(__file__).resolve().parents[3]
OUT=REPO/'result/dvizhok-declarative-bindings'
EXPECTED_SHA='c69ddf9247d0508207e3ab80c4ae87c782e8d876503487ee6fcc01105bccec97'

def validate(name,value):
    schema=json.loads((REPO/'docs'/name).read_text())
    Draft202012Validator(schema).validate(value)


def compared(left,right):
    a=json.loads((OUT/left/'result.json').read_text());b=json.loads((OUT/right/'result.json').read_text())
    fields=('id','status','expected','actual','points','score','reasonCode')
    normalize=lambda data:[{k:c.get(k) for k in fields} for c in data['checks']]
    assert normalize(a)==normalize(b),(left,right)
    assert all(c['status']=='PASS' for c in a['checks'])
    assert a['score']==b['score'] and a['unavailablePoints']==b['unavailablePoints']==0
    for directory in (left,right):validate('grading-v1.schema.json',json.loads((OUT/directory/'grading.json').read_text()))
    return {'checks':len(a['checks']),'score':a['score'],'status':'PASS','directories':[left,right]}


def main():
    for path in (REPO/'assignments/dvizhok-declarative-bindings/forms').glob('*.yaml'):
        validate('form-requirements-v1.schema.json',yaml.safe_load(path.read_text()))
    validate('form-requirements-v1.schema.json',yaml.safe_load((REPO/'tests/corpus/declarative-bindings/requirements.yaml').read_text()))
    validate('form-requirements-v1.schema.json',yaml.safe_load((OUT/'authoring-contract.yaml').read_text()))
    report={'core':compared('test','run'),'properties':compared('properties-test-final','properties-run-final')}
    validate('batch-manifest-v1.schema.json',json.loads((OUT/'batch-manifest.json').read_text()))
    batches=[]
    for name in ('batch-1','batch-2'):
        path=OUT/name;data=json.loads((path/'batch-result.json').read_text())
        validate('batch-result-v1.schema.json',data)
        for line in (path/'events.jsonl').read_text().splitlines():validate('batch-events-v1.schema.json',json.loads(line))
        assert [s['status'] for s in data['submissions']]==['passed','failed','passed']
        assert not any(s['cacheHit'] for s in data['submissions'])
        for grading in path.rglob('grading.json'):validate('grading-v1.schema.json',json.loads(grading.read_text()))
        batches.append({'name':name,'statuses':[s['status'] for s in data['submissions']],'cacheHit':False,'scope':'five portable declarative criteria'})
    report['batches']=batches
    actual=sha256((REPO/'Dvizhok.xdump').read_bytes()).hexdigest();assert actual==EXPECTED_SHA
    report.update(archiveSha256=actual,archiveUnchanged=True,jsonSchemas='PASS',fullRegression='NOT_RUN',fourValidate='NOT_RUN')
    (OUT/'public-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
