"""Recheck public JSON schemas and immutable source archive hashes."""
from pathlib import Path
from hashlib import sha256
import json
from jsonschema import Draft202012Validator

REPO=Path(__file__).resolve().parents[3]
OUT=REPO/'result/dvizhok-welcome-calendar'

def main():
    counts={}
    cases=[('grading-v1.schema.json','grading.json'),('batch-result-v1.schema.json','batch-result.json'),
           ('batch-manifest-v1.schema.json','batch-manifest.json')]
    for schema,filename in cases:
        validator=Draft202012Validator(json.loads((REPO/'docs'/schema).read_text()))
        validator.check_schema(validator.schema)
        paths=list(OUT.rglob(filename))
        for p in paths:validator.validate(json.loads(p.read_text()))
        counts[filename]=len(paths)
    validator=Draft202012Validator(json.loads((REPO/'docs/batch-events-v1.schema.json').read_text()))
    validator.check_schema(validator.schema)
    counts['events']=0
    for p in OUT.rglob('events.jsonl'):
        for line in p.read_text().splitlines():
            validator.validate(json.loads(line));counts['events']+=1
    (OUT/'schema-verification.json').write_text(json.dumps({'successful':True,'validated':counts},indent=2)+'\n')
    before=json.loads((OUT/'archive-hashes-before.json').read_text())
    after={p.name:sha256(p.read_bytes()).hexdigest() for p in REPO.glob('*.xdump')}
    (OUT/'archive-hashes-after.json').write_text(json.dumps(after,ensure_ascii=False,indent=2)+'\n')
    assert before==after, 'Source archive hashes changed'
    print(json.dumps({'schemas':counts,'archiveHashesUnchanged':True}))

if __name__=='__main__':main()
