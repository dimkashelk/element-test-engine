#!/usr/bin/env python3
"""Update the worker and, for HTTP contract changes, the API after pause/drain.

Run as root on the known deployment host. Never runs compose down, migrations,
or a compose up without explicit service names and --no-deps.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.request

COMPOSE='/opt/element-autocheck/compose'
ENV=Path('/srv/element-autocheck/config/.env')


def command(*args, **kwargs):
    return subprocess.run(args,check=True,**kwargs)


def containers(excluded):
    ids=subprocess.check_output(['docker','ps','-q'],text=True).split()
    result={}
    for c in json.loads(subprocess.check_output(['docker','inspect',*ids])):
        name=c['Name'].lstrip('/')
        if name in excluded:continue
        # Docker can return the same mounts in a different order on each inspect.
        configuration={k:c[k] for k in ('Config','HostConfig')}
        configuration['Mounts']=sorted(c['Mounts'],key=lambda mount: mount['Destination'])
        result[name]={'id':c['Id'],'startedAt':c['State']['StartedAt'],'restartCount':c['RestartCount'],
                      'configurationSha256':hashlib.sha256(json.dumps(configuration,sort_keys=True).encode()).hexdigest()}
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--image',required=True)
    parser.add_argument('--release',type=Path,required=True)
    operator_home = Path(os.path.expanduser('~' + os.environ.get('SUDO_USER', '')))
    parser.add_argument('--credentials', type=Path,
                        default=operator_home / '.config/element-autocheck/credentials.json')
    parser.add_argument('--include-api',action='store_true',help='Also replace API when the HTTP contract changes')
    parser.add_argument('--max-wall-seconds',type=int,help='Operator wall budget, without increasing CPU or memory')
    parser.add_argument('--output',type=Path,default=Path('/srv/element-autocheck/programming-deployment.json'))
    args=parser.parse_args()
    if os.geteuid()!=0 or not re.fullmatch('sha256:[0-9a-f]{64}',args.image):
        parser.error('Root and immutable image ID are required')
    if args.max_wall_seconds is not None and not 900<=args.max_wall_seconds<=3600:
        parser.error('Wall budget must be between 900 and 3600 seconds')
    release=args.release.resolve()
    if not release.is_relative_to('/opt/element-autocheck/releases') or not (release/'element_test/profiles.py').is_file():
        parser.error('Expected an installed programming release')
    image=json.loads(subprocess.check_output(['docker','inspect',args.image]))[0]
    revision=(release/'output/programming/engine-revision.txt').read_text().strip()
    if 'ENGINE_REVISION='+revision not in image['Config']['Env']:
        raise ValueError('Image revision differs from release')
    credentials=json.loads(args.credentials.read_text())
    def request(path,body=None,etag=None):
        token=credentials['operationsToken'] if path.startswith('/v1/admin/') else credentials['localTestingToken']
        headers={'Authorization':'Bearer '+token}
        if body is not None:headers['Content-Type']='application/json'
        if etag:headers['If-Match']=etag
        req=urllib.request.Request(credentials['baseUrl']+path,headers=headers,
                                   data=json.dumps(body).encode() if body is not None else None,
                                   method='PATCH' if body is not None else 'GET')
        with urllib.request.urlopen(req,timeout=20) as response:
            return json.loads(response.read()),response.headers.get('ETag')
    services = ('worker', 'api') if args.include_api else ('worker',)
    names = {service: f'element-autocheck-{service}-1' for service in services}
    settings,etag=request('/v1/admin/settings')
    backup=Path('/srv/element-autocheck/backups')/('programming-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    shutil.copy2(ENV,backup/'env')
    service_config=Path('/srv/element-autocheck/config/service.yaml')
    shutil.copy2(service_config,backup/'service.yaml')
    previous_link=os.readlink('/opt/element-autocheck/current')
    (backup/'previous-release.txt').write_text(previous_link)
    changed_env=False;changed_services=False;paused=False
    try:
        if settings['accepting']:
            request('/v1/admin/settings',{'accepting':False},etag);paused=True
        deadline=time.monotonic()+900
        while request('/v1/capacity')[0]['occupiedSlots']:
            if time.monotonic()>deadline:raise TimeoutError('Drain deadline reached before service replacement')
            time.sleep(2)
        before = containers(set(names.values()))
        original_images = {service: subprocess.check_output(
            ['docker', 'inspect', '--format', '{{.Image}}', name], text=True).strip()
            for service, name in names.items()}
        (backup/'containers.json').write_text(json.dumps(before))
        (backup/'service-images.json').write_text(json.dumps(original_images))
        with (backup/'database.dump').open('wb') as stream:
            command('docker','exec','element-autocheck-postgres-1','pg_dump','-U','autocheck','-d','autocheck','-Fc',stdout=stream)
        if args.max_wall_seconds is not None:
            text,count=re.subn(r'^([ \t]*maxCheckWallSeconds:[ \t]*)\d+[ \t]*$',
                               lambda m:m[1]+str(args.max_wall_seconds),service_config.read_text(),flags=re.M)
            if count!=1:raise ValueError('Expected one maxCheckWallSeconds setting')
            service_config.write_text(text)
        original=ENV.read_text();updated=original
        for key in ('SERVICE_IMAGE','ENGINE_IMAGE_DIGEST'):
            updated,count=re.subn(r'^'+key+r'=.*$',key+'='+args.image,updated,flags=re.M)
            if count!=1:raise ValueError('Expected exactly one '+key)
        temporary=ENV.with_name('.env.programming-update')
        temporary.write_text(updated);shutil.copymode(ENV,temporary);os.replace(temporary,ENV);changed_env=True
        config=json.loads(subprocess.check_output([COMPOSE,'config','--format','json']))
        if any(config['services'][service]['image'] != args.image for service in services):
            raise ValueError('Wrong service image in Compose')
        changed_services=True
        command(COMPOSE,'up','-d','--no-deps','--force-recreate','--wait',*services)
        current=Path('/opt/element-autocheck/current')
        link=current.with_name('current.programming-update')
        link.symlink_to(release);os.replace(link,current)
        after=containers(set(names.values()))
        if before!=after:raise ValueError('A container outside the selected services changed')
        result={'status':'updated','services':list(services),'imageDigest':args.image,'engineRevision':revision,
                'release':str(release),'backup':str(backup),'otherContainersUnchanged':True,
                'maxCheckWallSeconds':int(re.search(r'maxCheckWallSeconds:[ \t]*(\d+)',service_config.read_text())[1]),
                'containersBefore':before,'containersAfter':after}
        args.output.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k not in ('containersBefore','containersAfter')}),flush=True)
    except Exception:
        shutil.copy2(backup/'service.yaml',service_config)
        if changed_env:shutil.copy2(backup/'env',ENV)
        if changed_services:
            for service in services:
                env = {**os.environ, 'SERVICE_IMAGE': original_images[service]}
                command(COMPOSE,'up','-d','--no-deps','--force-recreate','--wait',service,env=env)
        current=Path('/opt/element-autocheck/current');link=current.with_name('current.programming-update')
        if link.is_symlink():link.unlink()
        link.symlink_to(previous_link);os.replace(link,current)
        raise
    finally:
        if paused:
            _,etag=request('/v1/admin/settings')
            request('/v1/admin/settings',{'accepting':True},etag)


if __name__=='__main__':main()
