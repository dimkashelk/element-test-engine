"""Record the installed executor and native boundaries without emulating XBQL."""
from pathlib import Path
import json
import subprocess
REPO=Path(__file__).resolve().parents[3];ROOT=Path(__file__).resolve().parent
OUT=REPO/'result/joins-null/native'
def main():
 OUT.mkdir(parents=True,exist_ok=True);results=[]
 for label,args,success in [('Version',['--version'],True),('Unsupported93',['-c','9.3',str(ROOT/'native-probes/Values.sbsl')],False),('Values',['-c','current',str(ROOT/'native-probes/Values.sbsl')],True),('JsonReaderCurrent',['-c','current',str(ROOT/'native-probes/JsonReader.sbsl')],True),('JsonReader90',['-c','9.0',str(ROOT/'native-probes/JsonReader.sbsl')],True),('Query',['-c','current',str(ROOT/'native-probes/Query.sbsl')],False)]:
  p=subprocess.run([str(REPO/'bin/script-runtime'),*args],capture_output=True,text=True,timeout=30)
  (OUT/(label+'.stdout')).write_text(p.stdout);(OUT/(label+'.stderr')).write_text(p.stderr)
  results.append({'probe':label,'args':args,'exitCode':p.returncode,'expectedSuccess':success})
  assert (p.returncode==0)==success,(label,p.stdout,p.stderr)
 (OUT/'results.json').write_text(json.dumps({'elementReferenceVersion':'9.3','executorVersion':'10.0.2-1','executionCompatibilityVersion':'current','probes':results},ensure_ascii=False,indent=2)+'\n');print(results)
if __name__=='__main__':main()
