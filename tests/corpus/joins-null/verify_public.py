"""Optional artifact verifier, following previous corpora (requires jsonschema)."""
from pathlib import Path
import json
from jsonschema import Draft202012Validator
REPO=Path(__file__).resolve().parents[3]
EVIDENCE=REPO/'result/joins-null'

def main():
 count=0
 def validate(value,schema):
  nonlocal count
  Draft202012Validator(json.loads((REPO/'docs'/schema).read_text())).validate(value);count+=1
 for folder in [EVIDENCE/'public-test',EVIDENCE/'public-run',EVIDENCE/'public-control',EVIDENCE/'public-cases-test',EVIDENCE/'public-cases-run']+[EVIDENCE/f'batch-{n}' for n in (1,2)]:
  for p in folder.rglob('grading.json'):validate(json.loads(p.read_text()),'grading-v1.schema.json')
  if (folder/'batch-result.json').exists():
   validate(json.loads((folder/'batch-result.json').read_text()),'batch-result-v1.schema.json')
   for line in (folder/'events.jsonl').read_text().splitlines():validate(json.loads(line),'batch-events-v1.schema.json')
 validate(json.loads((EVIDENCE/'batch-manifest.json').read_text()),'batch-manifest-v1.schema.json')
 result={'validatedArtifacts':count,'status':'PASS'}
 (EVIDENCE/'schemas.json').write_text(json.dumps(result,indent=2)+'\n');print(result)
if __name__=='__main__':main()
