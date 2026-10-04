"""Validate public grading, batch and event schemas independently."""
from pathlib import Path
import json
from jsonschema import Draft202012Validator
REPO=Path(__file__).resolve().parents[3];OUT=REPO/'result/unions-nesting'
def main():
 count=0
 def validate(value,schema):
  nonlocal count
  Draft202012Validator(json.loads((REPO/'docs'/schema).read_text())).validate(value);count+=1
 for folder in [OUT/('public-real-'+mode) for mode in ('test','run')]+[OUT/'public-control']+[REPO/folder for folder in json.loads((OUT/'batch-latest.json').read_text())]:
  for p in folder.rglob('grading.json'):validate(json.loads(p.read_text()),'grading-v1.schema.json')
  if (folder/'batch-result.json').exists():
   validate(json.loads((folder/'batch-result.json').read_text()),'batch-result-v1.schema.json')
   for line in (folder/'events.jsonl').read_text().splitlines():validate(json.loads(line),'batch-events-v1.schema.json')
 validate(json.loads((OUT/'batch-manifest.json').read_text()),'batch-manifest-v1.schema.json')
 (OUT/'schemas.json').write_text(json.dumps({'status':'PASS','validatedArtifacts':count},indent=2)+'\n');print(count)
if __name__=='__main__':main()
