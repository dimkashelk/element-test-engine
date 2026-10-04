from pathlib import Path
import subprocess,json
REPO=Path(__file__).resolve().parents[3];OUT=REPO/'result/unions-nesting/native'
def main():
 OUT.mkdir(parents=True,exist_ok=True);results=[]
 probes=[('version',['--version'],True),('arrays',['-c','current',str(REPO/'tests/corpus/unions-nesting/native-probes/Arrays.sbsl')],True),('native-query',['-c','current',str(REPO/'tests/corpus/joins-null/native-probes/Query.sbsl')],False)]
 for label,args,success in probes:
  p=subprocess.run([str(REPO/'bin/script-runtime'),*args],capture_output=True,text=True,timeout=30);(OUT/(label+'.stdout')).write_text(p.stdout);(OUT/(label+'.stderr')).write_text(p.stderr)
  assert (p.returncode==0)==success,(label,p.stderr);results.append({'probe':label,'exitCode':p.returncode,'args':args,'expectedSuccess':success})
  if label=='arrays':assert json.loads(p.stdout.strip())=={'calls':[1,2],'sum':3,'first':1,'empty':None,'size':3}
 (OUT/'results.json').write_text(json.dumps({'executorVersion':'10.0.2-1','elementReferenceVersion':'9.3','compatibility':'current','nativeXBQL':False,'probes':results},ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
