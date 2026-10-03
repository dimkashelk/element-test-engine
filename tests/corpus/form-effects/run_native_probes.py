"""Bounded standalone probes; absence of client UI is recorded, not simulated."""
from pathlib import Path
import json,subprocess
REPO=Path(__file__).resolve().parents[3]
def main():
    source=REPO/'tests/corpus/form-effects/native-probes'
    out=REPO/'result/dvizhok-form-effects/native-probes';out.mkdir(parents=True,exist_ok=True)
    result=[]
    for p in sorted(source.glob('*.sbsl')):
        process=subprocess.run([str(REPO/'bin/script-runtime'),'-c','9.0',str(p)],capture_output=True,text=True,timeout=30)
        (out/(p.stem+'.stdout')).write_text(process.stdout)
        (out/(p.stem+'.stderr')).write_text(process.stderr)
        result.append({'source':str(p.relative_to(REPO)),'exitCode':process.returncode,'stdout':process.stdout,'stderr':process.stderr})
    (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    assert next(r for r in result if 'Snapshot' in r['source'])['exitCode']==0
    assert all(r['exitCode']!=0 for r in result if 'Snapshot' not in r['source'])
    print([(Path(r['source']).stem,r['exitCode']) for r in result])
if __name__=='__main__':main()
