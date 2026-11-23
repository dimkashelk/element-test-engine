#!/usr/bin/env python3
"""Build criteria, a synthetic reference dump and a secret-free service release."""
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import tarfile
import zipfile

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'output/programming'


def source_files():
    files=[]
    for name in ('element_test','element_check_service','src','bin','config','assignments/programming-practice','deploy/autocheck'):
        files.extend(p for p in (ROOT/name).rglob('*') if p.is_file() and not p.is_symlink()
                     and not {'.state', '__pycache__', '.DS_Store'}.intersection(p.relative_to(ROOT).parts)
                     and ('.env' not in p.name or p.name == '.env.example')
                     and ('secrets' not in p.parts or p.name == 'README.md'))
    files.extend(ROOT/p for p in ('pyproject.toml','README.md','docs/grading-v1.schema.json',
                                 'docs/autocheck-openapi.yaml','.dockerignore'))
    return sorted(set(files))


def main():
    sys.path.insert(0, str(ROOT/'assignments/programming-practice'))
    from export_criteria import dump_criteria

    OUT.mkdir(parents=True,exist_ok=True)
    for optional,name in ((False,'Критерии_Программирование'),(True,'Критерии_Программирование_с_дополнительным')):
        body=dump_criteria(optional)
        (OUT/(name+'.yaml')).write_bytes(body)
        with zipfile.ZipFile(OUT/(name+'.zip'),'w',zipfile.ZIP_DEFLATED) as archive:archive.writestr('assignment.yaml',body)
    buffer=io.BytesIO();reference=ROOT/'tests/corpus/programming/reference'
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(reference.rglob('*')):
            if p.is_file():archive.write(p,'src/Движок/Движок/'+p.relative_to(reference).as_posix())
    with zipfile.ZipFile(OUT/'Эталон_Программирование.xdump','w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('application.zip',buffer.getvalue())
    files=source_files();digest=sha256()
    for p in files:digest.update(p.relative_to(ROOT).as_posix().encode()+b'\0'+p.read_bytes()+b'\0')
    revision=digest.hexdigest();(OUT/'engine-revision.txt').write_text(revision+'\n')
    extras=[ROOT/'tools/ci'/name for name in ('package_programming.py','autocheck_smoke.py','autocheck_host_verify.py','autocheck_update_worker.py')]
    extras += [ROOT/'tests'/name for name in ('test_programming_profile.py','test_programming_practice.py',
                                             'test_declarative_assignment.py','test_autocheck_service.py')]
    extras += [p for p in reference.rglob('*') if p.is_file()]
    extras += [ROOT/'docs/autocheck-integration.md',OUT/'engine-revision.txt',OUT/'Критерии_Программирование.yaml',OUT/'Эталон_Программирование.xdump']
    extras += [ROOT/'docs/programming-service-update.md']
    release=OUT/'Сервис_Программирование.tar.gz'
    with tarfile.open(release,'w:gz') as archive:
        for p in sorted(set(files+extras)):archive.add(p,arcname=p.relative_to(ROOT).as_posix(),recursive=False)
    developer_files=[p for p in files if 'deploy' not in p.relative_to(ROOT).parts]
    with zipfile.ZipFile(OUT/'Тесты_Программирование.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(set(developer_files+extras)):archive.write(p,p.relative_to(ROOT).as_posix())
    manifest={'engineRevision':revision,'buildCommand':'docker build -f deploy/autocheck/Dockerfile --build-arg ENGINE_REVISION='+revision+' -t element-checker/service:local .',
              'requiredScore':320,'optionalScore':326,'files':{}}
    for p in sorted(OUT.iterdir()):
        if p.is_file() and p.name!='package-manifest.json':
            manifest['files'][p.name]={'sizeBytes':p.stat().st_size,'sha256':sha256(p.read_bytes()).hexdigest()}
    (OUT/'package-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'engineRevision':revision,'releaseFiles':len(set(files+extras)),'releaseBytes':release.stat().st_size}))


if __name__=='__main__':main()
