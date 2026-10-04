"""Independent native values; native Query and -c 9.3 remain unavailable."""
from pathlib import Path
import json
import subprocess
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[2];OUT=REPO/'result/projections-aggregates/native'
def main():
 OUT.mkdir(parents=True,exist_ok=True);results=[]
 for label,args,success in [('version',['--version'],True),('scalars',['-c','current',str(ROOT/'native-probes/Scalars.sbsl')],True),('moment',['-c','current',str(ROOT/'native-probes/Moment.sbsl')],True),('modulus',['-c','current',str(ROOT/'native-probes/Modulus.sbsl')],True),('unsupported-93',['-c','9.3',str(ROOT/'native-probes/Scalars.sbsl')],False),('native-query',['-c','current',str(REPO/'tests/corpus/joins-null/native-probes/Query.sbsl')],False)]:
  p=subprocess.run([str(REPO/'bin/script-runtime'),*args],capture_output=True,text=True,timeout=30)
  (OUT/(label+'.stdout')).write_text(p.stdout);(OUT/(label+'.stderr')).write_text(p.stderr)
  results.append({'probe':label,'args':args,'exitCode':p.returncode,'expectedSuccess':success});assert (p.returncode==0)==success
 (OUT/'results.json').write_text(json.dumps({'referenceVersion':'9.3','executorVersion':'10.0.2-1','compatibility':'current','probes':results},ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
