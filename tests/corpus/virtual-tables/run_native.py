from pathlib import Path
import json,subprocess
REPO=Path(__file__).resolve().parents[3];OUT=REPO/'result/virtual-tables/native'
def main():
 OUT.mkdir(parents=True,exist_ok=True);results=[]
 for label,args,success in [('version',['--version'],True),('values',['-c','current',str(REPO/'tests/corpus/virtual-tables/native-probes/Values.sbsl')],True),('query',['-c','current',str(REPO/'tests/corpus/joins-null/native-probes/Query.sbsl')],False)]:
  p=subprocess.run([str(REPO/'bin/script-runtime'),*args],capture_output=True,text=True,timeout=30)
  (OUT/(label+'.stdout')).write_text(p.stdout);(OUT/(label+'.stderr')).write_text(p.stderr)
  assert (p.returncode==0)==success,(label,p.stderr)
  if label=='values':assert json.loads(p.stdout)=={'before':True,'inclusive':True,'after':True,'minimum':'0001-01-01','count':2}
  results.append({'probe':label,'exitCode':p.returncode,'expectedSuccess':success})
 (OUT/'results.json').write_text(json.dumps({'executorVersion':'10.0.2-1','compatibility':'current','referenceVersion':'9.3','nativeXBQL':False,'probes':results},indent=2)+'\n')
if __name__=='__main__':main()
