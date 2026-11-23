#!/usr/bin/env python3
"""Opt-in disposable Compose deployment, real API/worker/engine, no external callback.

Build the service image first. Uses only a unique test project, loopback gateway,
random test secrets and a temporary data directory; removes its volume on exit.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
import uuid
import httpx
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT/'deploy/autocheck/compose.yaml'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image',default='element-checker/service:local')
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--dump',type=Path,default=ROOT/'Dvizhok.xdump')
    parser.add_argument('--docker-socket',default='/var/run/docker.sock')
    parser.add_argument('--criteria',type=Path,help='Run a supplied complete criteria package instead of SQL smoke')
    parser.add_argument('--run-mode',choices=('isolated','integration'),default='integration')
    parser.add_argument('--expected-score',type=int)
    parser.add_argument('--expected-status',choices=('passed','failed','incomplete'),default='passed')
    parser.add_argument('--timeout',type=int,default=120)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    runtime=args.runtime.resolve()
    if not (runtime/'lib').is_dir():
        parser.error('Runtime lib/ is required')
    project='autocheck-smoke-'+uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix='autocheck-compose-',dir='/private/tmp' if Path('/private/tmp').exists() else '/tmp') as temporary:
        base=Path(temporary).resolve();data=base/'data';data.mkdir(mode=0o2777);data.chmod(0o2777)
        secret_dir=base/'secrets';secret_dir.mkdir(mode=0o700)
        token=secrets.token_hex(32)
        values={'db_password':secrets.token_hex(32),'integration_password':secrets.token_hex(32),
            'api_clients.json':json.dumps({'clients':[{'clientId':'smoke','allowedRunModes':['isolated','integration'],'tokens':[{'keyId':'smoke','enabled':True,'scopes':['checks:read','checks:write','settings:read','settings:write','delivery:retry'],'tokenSha256':hashlib.sha256(token.encode()).hexdigest()}]}]}),
            # No destination: the result is fetched through GET; no external event is sent.
            'webhook_clients.json':json.dumps({'clients':[{'clientId':'smoke','currentKeyId':'smoke','keys':{'smoke':secrets.token_hex(32)}}]})}
        for name,value in values.items():
            path=secret_dir/name;path.write_text(value);path.chmod(0o444)
        digest=subprocess.check_output(['docker','image','inspect',args.image,'--format','{{.Id}}'],text=True).strip()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env=dict(os.environ,SERVICE_IMAGE=args.image,SERVICE_DATA_ROOT=str(data),SCRIPT_RUNTIME_ROOT=str(runtime),
            SECRETS_ROOT=str(secret_dir),DOCKER_SOCKET_PATH=args.docker_socket,GATEWAY_PORT=str(port),ENGINE_IMAGE_DIGEST=digest)
        command=['docker','compose','-p',project,'-f',str(COMPOSE)]
        def compose(*arguments,**kwargs):
            return subprocess.run([*command,*arguments],env=env,check=True,**kwargs)
        try:
            compose('up','-d','--wait','--wait-timeout','120',stdout=subprocess.DEVNULL)
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'Authorization':'Bearer '+token},timeout=30,trust_env=False) as client:
                response=client.get('/health/ready');response.raise_for_status()
                import http.client
                probe=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
                probe.putrequest('POST','/v1/checks')
                probe.putheader('Content-Length',str(41*1024*1024))
                probe.endheaders()
                oversized=probe.getresponse()
                if oversized.status!=413 or oversized.getheader('Content-Type')!='application/json':
                    raise RuntimeError('Gateway must reject oversized requests with JSON')
                uuid.UUID(json.loads(oversized.read())['error']['requestId'])
                probe.close()
                if args.criteria:
                    body=args.criteria.read_bytes()
                else:
                    criteria=yaml.safe_load((ROOT/'assignments/poc-integration-environment/assignment.yaml').read_text())
                    body=yaml.safe_dump({'name':criteria['name'],'checks':criteria['checks'][:1]},allow_unicode=True).encode()
                criteria_path=base/'assignment.yaml';criteria_path.write_bytes(body)
                with args.dump.open('rb') as stream:
                    dump_hash=hashlib.file_digest(stream,'sha256').hexdigest()
                manifest={'submissionId':'smoke','studentId':'smoke','assignmentId':'profile' if args.criteria else 'sql','attempt':1,'runMode':args.run_mode,
                    'dump':{'format':'xdump','sizeBytes':args.dump.stat().st_size,'sha256':dump_hash},
                    'criteria':{'format':'yaml','sizeBytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}}
                response=client.post('/v1/reservations',json=manifest,headers={'Idempotency-Key':'reserve'});response.raise_for_status()
                reservation=response.json()
                with ExitStack() as stack:
                    files={name:(path.name,stack.enter_context(path.open('rb'))) for name,path in [('dump',args.dump),('criteria',criteria_path)]}
                    response=client.post('/v1/checks',files=files,headers={'Idempotency-Key':'upload','X-Reservation-Id':reservation['reservationId']})
                response.raise_for_status()
                started=time.monotonic();deadline=started+args.timeout
                while time.monotonic()<deadline:
                    response=client.get('/v1/checks/'+reservation['checkId']);response.raise_for_status();value=response.json()
                    if value['status'] in ('completed','rejected','execution_failed'):
                        break
                    time.sleep(0.5)
                if value['status']!='completed' or value['grading']['status']!=args.expected_status:
                    raise RuntimeError('Real check did not match expected status: '+str(value))
                if args.expected_score is not None and value['grading']['score']!=args.expected_score:
                    raise RuntimeError('Unexpected real score: '+str(value['grading']))
                if client.get('/v1/capacity').json()['occupiedSlots']!=0:
                    raise RuntimeError('Execution slot was not released')
                artifact=client.get(f'/v1/checks/{reservation["checkId"]}/artifacts/grading.json');artifact.raise_for_status()
                if artifact.headers.get('content-type')!='application/json' or artifact.json()['status']!=args.expected_status:
                    raise RuntimeError('Invalid JSON artifact')
                if any(a['kind'].endswith('.html') for a in value['artifacts']):
                    raise RuntimeError('Service must return JSON artifacts only')
                summary={'result':'passed','services':6,'checkStatus':value['grading']['status'],
                         'score':value['grading']['score'],'maxScore':value['grading']['maxScore'],
                         'unavailablePoints':value['grading']['unavailablePoints'],
                         'checks':len(value['grading']['feedback']),'sourceHash':value['grading']['sourceHash'],
                         'seconds':round(time.monotonic()-started,2),'artifacts':'passed','slotsAfter':0,
                         'imageDigest':digest}
                if args.report:
                    args.report.parent.mkdir(parents=True,exist_ok=True)
                    args.report.write_text(json.dumps({'summary':summary,'check':value},ensure_ascii=False,indent=2)+'\n')
                print(json.dumps(summary))
        except Exception:
            compose('ps')
            compose('logs','--tail','30','api','worker','notifier','migrate','gateway')
            raise
        finally:
            # Only this randomly named, disposable test project's volume is removed.
            compose('down','--volumes','--remove-orphans',stdout=subprocess.DEVNULL)


if __name__=='__main__':
    main()
