"""Two fresh public batches, scoped to the portable task-38 criteria."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import yaml

REPO=Path(__file__).resolve().parents[3]
CORPUS=Path(__file__).resolve().parent
OUT=REPO/'result/dvizhok-declarative-bindings'

def main():
    mutant=OUT/'batch-mutant'
    if mutant.exists():shutil.rmtree(mutant)
    shutil.copytree(CORPUS/'first',mutant)
    file=mutant/'Alpha/Panel.yaml';data=yaml.safe_load(file.read_text())
    data['Наследует']['Содержимое']['Содержимое'][1]['Колонки'][0]['Значение']='=RowData.Credit'
    file.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False))
    manifest={'schemaVersion':'1.0','assignment':str(CORPUS/'assignment.yaml'),'assignmentId':'bindings-38',
              'submissions':[{'studentId':'original','project':str(CORPUS/'first')},
                             {'studentId':'mutated','project':str(mutant)},
                             {'studentId':'neighbor','project':str(CORPUS/'second')}]}
    path=OUT/'batch-manifest.json';path.write_text(json.dumps(manifest,indent=2))
    for name in ['batch-1','batch-2']:
        output=OUT/name
        if output.exists():raise RuntimeError('Evidence folder must be fresh: '+name)
        with (OUT/(name+'.log')).open('w') as log:
            result=subprocess.run([str(REPO/'bin/element-test'),'batch','--manifest',str(path),'--output',str(output),'--workers','1'],stdout=log,stderr=log,timeout=300)
        if result.returncode!=1:raise RuntimeError('Batch must report the mutation failure')
    print('Two scoped public batches completed')

if __name__=='__main__':main()
