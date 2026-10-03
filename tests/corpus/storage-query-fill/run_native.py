"""Trusted native probes: capture original refusals without claiming native XBQL."""
from pathlib import Path
import json, subprocess
REPO=Path(__file__).resolve().parents[3];ROOT=Path(__file__).resolve().parent;OUT=REPO/'result/storage-query-fill/native'
def main():
 OUT.mkdir(parents=True,exist_ok=True);results=[]
 # Preserve initial exploratory refusals too, before the corrected syntax.
 initial=[('InvalidName','fill-native.sbsl','пер Нужно: Число обязательное'),('InvalidModifier','InvalidModifier.sbsl','пер Нужно: Число обязательное')]
 base=(ROOT/'native-probes/Defaults.sbsl').read_text()
 for label,filename,modifier in initial:
  path=OUT/filename;path.write_text(base.replace('обз пер Нужно: Число',modifier));run(label,path,False,results)
 for path in sorted((ROOT/'native-probes').glob('*.sbsl')):
  run(path.stem,path,path.stem in ('Defaults','ReferenceDefault','Widening','NarrowingFlow','NarrowingValue'),results)
 (OUT/'results.json').write_text(json.dumps({'executorVersion':'10.0.2-1','compatibilityVersion':'9.0','probes':results},ensure_ascii=False,indent=2)+'\n')
 print('Native probes:',len(results))
def run(label,path,success,results):
 p=subprocess.run([str(REPO/'bin/script-runtime'),'-c','9.0',str(path)],capture_output=True,text=True,timeout=30)
 (OUT/(label+'.stdout')).write_text(p.stdout);(OUT/(label+'.stderr')).write_text(p.stderr)
 assert (p.returncode==0)==success,(label,p.stdout,p.stderr)
 results.append({'name':label,'source':str(path.relative_to(REPO)),'exitCode':p.returncode,'expectedSuccess':success,'stdout':str((OUT/(label+'.stdout')).relative_to(REPO)),'stderr':str((OUT/(label+'.stderr')).relative_to(REPO))})
if __name__=='__main__':main()
