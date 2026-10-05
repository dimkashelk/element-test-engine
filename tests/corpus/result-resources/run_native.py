"""Native Script language probes; refusals explicitly bound adapter claims."""
from hashlib import sha256
from pathlib import Path
import json,subprocess
REPO=Path(__file__).resolve().parents[3];ROOT=Path(__file__).resolve().parent;OUT=REPO/'result/result-resources/native'
def main():
 OUT.mkdir(parents=True,exist_ok=True);records=[]
 for path in sorted((ROOT/'native-probes').glob('*.sbsl')):
  p=subprocess.run([str(REPO/'bin/script-runtime'),str(path)],capture_output=True,text=True,timeout=60)
  (OUT/(path.stem+'.stdout')).write_text(p.stdout);(OUT/(path.stem+'.stderr')).write_text(p.stderr)
  expected=path.stem in ('Constructors','Methods')
  assert (p.returncode==0)==expected,(path.stem,p.stdout,p.stderr)
  records.append({'name':path.stem,'source':str(path.relative_to(REPO)),'sha256':sha256(path.read_bytes()).hexdigest(),'exitCode':p.returncode,'expectedSuccess':expected,'stdout':str((OUT/(path.stem+'.stdout')).relative_to(REPO)),'stderr':str((OUT/(path.stem+'.stderr')).relative_to(REPO))})
 (OUT/'results.json').write_text(json.dumps({'executorVersion':'10.0.2-1','executionCompatibilityVersion':'current','nativeXBQL':False,'probes':records},ensure_ascii=False,indent=2)+'\n');print(len(records))
if __name__=='__main__':main()
