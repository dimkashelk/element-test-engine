"""Refresh only the saved task 40 scope; never run full query discovery locally."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from element_test.bridge import write_json
from element_test.generated_types import ProjectTypes
from element_test.loader import open_project
from element_test.model import analyze
from element_test.query_catalog import REFERENCE040, attach_evidence, markdown
from element_test.query_plan import parse_storage_query
from element_test.yaml_io import InputError
REPO=Path(__file__).resolve().parents[3]
BASE=REPO/'docs/query-stage-040-baseline.json'
CATALOG=REPO/'docs/query-contract-catalog.json'
OUT=REPO/'result/joins-null'

def main():
 baseline=json.loads(BASE.read_text());catalog=json.loads(CATALOG.read_text());selected={r['contractId'] for r in baseline['contracts']}
 records=[r for r in catalog['contracts'] if r['contractId'] in selected]
 versions=REFERENCE040
 groups={}
 for r in records:
  if r.get('archive'):
   project=r['projectIdentity'];key=(r['archive'],project['Поставщик']+'::'+project['Имя']);groups.setdefault(key,[]).append(r)
 for (archive,project),group in groups.items():
  with open_project(REPO/archive,project) as root:
   model=analyze(root);modules={m['sourceFile']:m for m in model['modules']}
   for r in group:
    # Exact baseline occurrence and source identity, including external XBQL.
    assert sha256((root/r['file']).read_bytes()).hexdigest()==r['sourceHash']
    assert model['sourceHash']==r['projectSourceHash']
    if r['family']!='literal':continue
    module=modules[r['file']];c=ProjectTypes(model,module['namespace'],module.get('imports',[]));c.rename_collisions=True;c.reference_id_type='Ууид'
    try:
     r['ast']=parse_storage_query(r['text'],c).to_dict();r['parsed']=True;r['unsupportedReason']=None
     r['semantics']=['adapter-storage-relational-040','element-reference-9.3','script-current']
    except InputError as e:r['ast']=None;r['parsed']=False;r['unsupportedReason']=str(e)
    r['reference040']=versions
 # Rebuild only fresh task-40 evidence; historical stages remain untouched.
 for r in records:
  if r.get('archive'):
   r.update(planned=False,executed=False,independentlyAssessed=False,criteria=[],evidence=[])
 attach_evidence(records,[OUT/'public-test',OUT/'public-run',OUT/'public-cases-test',OUT/'public-cases-run'])
 catalog['documentationReferenceVersions']=['9.1','9.3'];catalog['stage040Reference']=versions
 CATALOG.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n');CATALOG.with_suffix('.md').write_text(markdown(catalog))
 counts={k:sum(bool(r.get(k)) for r in records) for k in ('parsed','planned','executed','independentlyAssessed')}
 archives=[{**a,'unchanged':sha256((REPO/a['file']).read_bytes()).hexdigest()==a['sha256']} for a in baseline['archives']]
 assert all(a['unchanged'] for a in archives)
 report={'stage':'040','scopeCount':len(records),'counts':counts,'archives':archives,'reference':versions,'contracts':records,
         'remainingCauses':dict(Counter(r.get('unsupportedReason') for r in records if r.get('unsupportedReason')))}
 write_json(OUT/'catalog-stage-040.json',report)
 write_json(REPO/'docs/query-stage-040-coverage.json',report)
 print(counts)
if __name__=='__main__':main()
