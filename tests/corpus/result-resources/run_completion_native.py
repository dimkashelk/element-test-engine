"""Installed Script probes, kept separate from deliberately emulated XBQL."""
from hashlib import sha256
from pathlib import Path
import json
import subprocess

REPO=Path(__file__).resolve().parents[3]
ROOT=Path(__file__).resolve().parent
OUT=REPO/'result/result-resources-completion/native-probes'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    probes=[]
    sources=[(p,True) for p in sorted((ROOT/'completion-native').glob('*.sbsl'))]
    sources.append((ROOT/'native-probes/Query.sbsl',False))
    for path,success in sources:
        p=subprocess.run([str(REPO/'bin/script-runtime'),str(path)],capture_output=True,text=True,timeout=60)
        for kind,content in [('stdout',p.stdout),('stderr',p.stderr)]:
            (OUT/(path.stem+'.'+kind)).write_text(content)
        assert (p.returncode==0)==success,(path,p.stderr)
        probes.append({'source':str(path.relative_to(REPO)),'sha256':sha256(path.read_bytes()).hexdigest(),
                       'exitCode':p.returncode,'expectedSuccess':success,
                       'stdout':str((OUT/(path.stem+'.stdout')).relative_to(REPO)),
                       'stderr':str((OUT/(path.stem+'.stderr')).relative_to(REPO))})
    (OUT/'results.json').write_text(json.dumps({'executorVersion':'10.0.2-1',
        'compatibility':'current','nativeXBQL':False,'probes':probes},ensure_ascii=False,indent=2)+'\n')
    print({'probes':len(probes),'nativeXBQL':False})

if __name__=='__main__':main()
