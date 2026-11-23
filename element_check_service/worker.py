"""Bounded process supervisor; jobs never automatically execute twice."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import zipfile
import jsonschema
from psycopg.types.json import Jsonb
from . import ARTIFACTS, VERSION
from .common import now, sync_dir, write_json
from .config import ROOT, secret
from .docker_control import DockerControl, advisory, real_docker
from element_test.runtime_images import digest_reference
from .observability import event


def failure(code):
    return {'status':'execution_failed','grading':None,'error':{'code':code,'message':code.replace('_',' '),'retryable':True},'engine':provenance()}


def provenance():
    revision,digest = os.environ.get('ENGINE_REVISION',''),os.environ.get('ENGINE_IMAGE_DIGEST','')
    import re
    if re.fullmatch('[0-9a-f]{40,64}',revision) and re.fullmatch('sha256:[0-9a-f]{64}',digest):
        return {'version':VERSION,'revision':revision,'imageDigest':digest}
    return None


def preflight(config):
    if provenance() is None:
        raise ValueError('Build revision and service image digest are required')
    if not shutil.which('java'):
        raise ValueError('Java unavailable')
    runtime_file = Path(os.environ.get('ELEMENT_TEST_RUNTIMES',ROOT/'config/runtimes.json'))
    runtimes = json.loads(runtime_file.read_text())
    if set(runtimes)!={'9.0','9.3','10.0'}:
        raise ValueError('Required runtime profiles missing')
    images = set()
    for runtime in runtimes.values():
        home = Path(os.environ.get('ELEMENT_SCRIPT_HOME',ROOT/runtime['directory']))
        if not (home/'lib').is_dir() or not (home/'config/logback.xml').is_file():
            raise ValueError('Runtime missing')
        images.add(runtime['image'])
    if 'integration' in config.get('execution','allowedRunModes'):
        integration = json.loads(Path(os.environ.get('ELEMENT_TEST_INTEGRATION_CONFIG',ROOT/'config/integration.json')).read_text())
        images.add(integration['image'])
        jars = list((home/'lib').glob('postgresql-*.jar'))
        if not jars or not list((home/'lib').glob('com.e1c.g5rt.appliedobjects.sql.xbsl.runtime-*.jar')):
            raise ValueError('SQL runtime missing')
        driver_found = False
        for jar in jars:
            with zipfile.ZipFile(jar) as archive:
                driver_found |= 'org/postgresql/Driver.class' in archive.namelist()
        if not driver_found:
            raise ValueError('JDBC missing')
        secret('INTEGRATION_PASSWORD')
    if real_docker(['info','--format','{{.ServerVersion}}'],timeout=5).returncode:
        raise ValueError('Docker unavailable')
    for image in images:
        if '@sha256:' not in image or real_docker(['image','inspect',digest_reference(image)],timeout=5).returncode:
            raise ValueError('Pinned runtime image missing')


def stop_process(process):
    if process is None:
        return
    # The process leader may have exited leaving Java/Docker descendants in its group.
    try:
        os.killpg(process.pid,signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def artifacts_from(directory, config, cid):
    result,total = [],0
    for kind in ARTIFACTS:
        path = directory/kind
        if not path.is_file() or path.is_symlink():
            raise ValueError('Missing artifact')
        size = path.stat().st_size
        total += size
        if total>64*1024*1024:
            raise ValueError('Output limit')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream,'sha256').hexdigest()
        result.append({'kind':kind,'relative_path':str(Path('checks')/str(cid)/'output'/kind),'size':size,'sha256':digest})
        with path.open('rb') as stream:
            os.fsync(stream.fileno())
    return result


class Worker:
    def __init__(self, db, config, *, dependency_check=preflight):
        self.db,self.config,self.dependency_check = db,config,dependency_check
        self.owner = str(uuid.uuid4())
        self.container = os.environ.get('HOSTNAME')
        self.stop = threading.Event()
        self.processes = {}
        self.mutex = threading.Lock()

    def execute(self, row):
        cid,fence = row['id'],row['fence']
        base = self.config.data/'checks'/str(cid)
        attempt = base/'attempts'/str(fence)
        work = self.config.data/'work'/str(cid)/str(fence)
        attempt.mkdir(parents=True,mode=0o2770)
        work.mkdir(parents=True,mode=0o2770)
        control,process,outcome = None,None,failure('engine_failure')
        artifacts = []
        try:
            if now()-row['accepted_at'] > timedelta(seconds=self.config.get('admission','acceptedStartDeadlineSeconds')):
                outcome = failure('start_deadline_exceeded')
            else:
                control = DockerControl(self.db,row)
                control.start()
                shim = work/'bin'
                shim.mkdir(mode=0o700)
                (shim/'docker').write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' -m element_check_service.docker_proxy "$@"\n')
                (shim/'docker').chmod(0o700)
                m = row['manifest']
                job = {'checkId':str(cid),'manifest':m,'data':str(self.config.data),'config':self.config.values,
                    'dump':str(base/'input/dump.xdump'),'criteria':str(base/'input'/('criteria.'+m['criteria']['format'])),
                    'assignment':str(base/'assignment'),'output':str(attempt),'engine':provenance()}
                job_path = work/'job.json'
                write_json(job_path,job)
                env = {k:v for k,v in os.environ.items() if k in ('LANG','LC_ALL','JAVA_HOME','ELEMENT_SCRIPT_HOME','ELEMENT_TEST_RUNTIMES','ELEMENT_TEST_INTEGRATION_CONFIG','ELEMENT_TEST_RUNTIME_TIMEOUT','TZ')}
                env.update(PATH=str(shim)+os.pathsep+os.environ.get('PATH','/usr/bin:/bin'),PYTHONPATH=str(ROOT),TMPDIR=str(work),
                    AUTOCHECK_CONTROL_SOCKET=control.socket,AUTOCHECK_CONTROL_TOKEN=control.token)
                if m['runMode']=='integration':
                    env['ELEMENT_TEST_INTEGRATION_PASSWORD'] = secret('INTEGRATION_PASSWORD')
                with (attempt/'operator.log').open('wb') as log:
                    process = subprocess.Popen([sys.executable,'-m','element_check_service.engine_child',str(job_path)],env=env,
                        cwd=ROOT,start_new_session=True,stdout=log,stderr=log)
                    with self.mutex:
                        self.processes[str(cid)] = process
                    deadline = row['accepted_at']+timedelta(seconds=self.config.get('limits','maxCheckWallSeconds'))
                    heartbeat = 0
                    while process.poll() is None:
                        if self.stop.is_set():
                            outcome = failure('worker_lost')
                            break
                        if now()>=deadline:
                            outcome = failure('check_timeout')
                            break
                        if (attempt/'operator.log').stat().st_size>2*1024*1024:
                            break
                        if time.monotonic()-heartbeat>=self.config.get('execution','checkHeartbeatSeconds'):
                            if not self.db.heartbeat(cid,self.owner,fence):
                                outcome = failure('worker_lost')
                                break
                            heartbeat = time.monotonic()
                        time.sleep(0.1)
                    else:
                        if process.returncode==0:
                            path = attempt/'outcome.json'
                            if path.stat().st_size>32*1024*1024:
                                raise ValueError('Result size')
                            outcome = json.loads(path.read_text())
                            if outcome['status']=='completed':
                                jsonschema.validate(outcome['grading'],json.loads((ROOT/'docs/grading-v1.schema.json').read_text()))
                                if outcome['grading']['runId']!=str(cid) or outcome['grading']['studentId']!=m['studentId'] or outcome['grading']['assignmentId']!=m['assignmentId']:
                                    raise ValueError('Grading identity mismatch')
                                artifacts = artifacts_from(attempt,self.config,cid)
                            elif outcome['status'] not in ('rejected','execution_failed') or outcome.get('grading') is not None or not isinstance(outcome.get('error'),dict):
                                raise ValueError('Invalid outcome')
                stop_process(process)
        except Exception as exc:
            event('check.supervisor_error',checkId=str(cid),errorType=type(exc).__name__)
            outcome,artifacts = failure('environment_unavailable'),[]
        finally:
            stop_process(process)
            if control:
                control.close()
            with self.mutex:
                self.processes.pop(str(cid),None)
        with self.db.transaction() as c:
            c.execute('UPDATE checks SET supervisor_stopped=true WHERE id=%s AND lease_owner=%s',(cid,self.owner))
        # Fencing occurs before publication. A lost worker cannot release capacity.
        with self.db.transaction() as c:
            current = c.execute("UPDATE checks SET state='finalizing',candidate=%s WHERE id=%s AND fence=%s AND lease_owner=%s AND lease_until>clock_timestamp() AND state='running' RETURNING id",
                (Jsonb({'outcome':outcome,'artifacts':artifacts}),cid,fence,self.owner)).fetchone()
        if not current:
            return
        if artifacts:
            target = base/'output'
            write_json(attempt/'manifest.json',{'fence':fence,'artifacts':artifacts})
            sync_dir(attempt)
            os.rename(attempt,target)
            sync_dir(base)
        self.finish(row,outcome,artifacts,control)
        shutil.rmtree(work,ignore_errors=True)

    def finish(self, row, outcome, artifacts, control=None):
        with self.db.transaction() as c:
            proof = c.execute('SELECT * FROM checks WHERE id=%s',(row['id'],)).fetchone()
        stopped = proof['supervisor_stopped']
        if not stopped and proof['supervisor_container']:
            try:
                result = real_docker(['inspect',proof['supervisor_container']],timeout=5)
                value = json.loads(result.stdout)[0]
                labels = value.get('Config',{}).get('Labels',{})
                started = __import__('datetime').datetime.fromisoformat(value['State']['StartedAt'].replace('Z','+00:00'))
                stopped = labels.get('com.docker.compose.service')=='worker' and (not value['State']['Running'] or started>proof['started_at'])
            except Exception:
                import re
                if re.fullmatch('[0-9a-f]{12,64}',proof['supervisor_container']):
                    listing = real_docker(['ps','-aq','--no-trunc'],timeout=5)
                    stopped = listing.returncode==0 and not any(x.startswith(proof['supervisor_container']) for x in listing.stdout.decode().splitlines())
        if not stopped:
            with self.db.transaction() as c:
                c.execute("UPDATE checks SET cleanup_blocked=true WHERE id=%s AND fence=%s AND state='finalizing'",(row['id'],row['fence']))
            return
        owns_control = control is None
        control = control or DockerControl(self.db,row)
        try:
            clean = control.cleanup()
        except Exception:
            clean = False
        finally:
            if owns_control:
                control.server.server_close()
                Path(control.socket).unlink(missing_ok=True)
        if not clean:
            with self.db.transaction() as c:
                c.execute("UPDATE checks SET cleanup_blocked=true WHERE id=%s AND fence=%s AND state='finalizing'", (row['id'],row['fence']))
            return
        if artifacts:
            output = self.config.data/'checks'/str(row['id'])/'output'
            try:
                if artifacts_from(output,self.config,row['id'])!=artifacts:
                    raise ValueError('Artifact mismatch')
            except Exception:
                outcome,artifacts = failure('result_invalid'),[]
        if self.db.finalize(row['id'],row['fence'],outcome,artifacts):
            event('check.finished',checkId=str(row['id']),status=outcome['status'],reasonCode=(outcome.get('error') or {}).get('code'))

    def recover(self):
        with self.db.transaction() as c:
            expired = c.execute("UPDATE checks SET state='finalizing',fence=fence+1,supervisor_stopped=true,candidate=%s WHERE state='accepted' AND accepted_at<clock_timestamp()-(%s * interval '1 second') RETURNING *",(Jsonb({'outcome':failure('start_deadline_exceeded'),'artifacts':[]}),self.config.get('admission','acceptedStartDeadlineSeconds'))).fetchall()
        for row in expired:
            self.finish(row,failure('start_deadline_exceeded'),[])
        with self.db.transaction() as c:
            rows = c.execute("SELECT id FROM checks WHERE (state='running' AND lease_until<=clock_timestamp()) OR (state='finalizing' AND (lease_until<=clock_timestamp() OR cleanup_blocked))").fetchall()
        for item in rows:
            with self.db.transaction() as c:
                advisory(c,item['id'])
                row = c.execute("SELECT * FROM checks WHERE id=%s AND ((state='running' AND lease_until<=clock_timestamp()) OR state='finalizing') FOR UPDATE",(item['id'],)).fetchone()
                if not row:
                    continue
                if row['state']=='running':
                    # No second engine run. Proxy refuses subsequent operations after this fence.
                    row = c.execute("UPDATE checks SET state='finalizing',fence=fence+1,candidate=%s WHERE id=%s RETURNING *", (Jsonb({'outcome':failure('worker_lost'),'artifacts':[]}),row['id'])).fetchone()
                candidate = row['candidate'] or {'outcome':failure('worker_lost'),'artifacts':[]}
            self.finish(row,candidate['outcome'],candidate['artifacts'])

    def housekeeping(self):
        config = self.config
        with self.db.transaction(lock=True) as c:
            self.db.expire_reserved(c)
            uploads = c.execute("SELECT * FROM reservations WHERE state='uploading' AND (upload_deadline<=clock_timestamp() OR heartbeat_at<clock_timestamp()-(%s * interval '1 second'))",(config.get('admission','uploadIdleTimeoutSeconds'),)).fetchall()
            for row in uploads:
                directory = config.data/'staging'/f'{row["id"]}-{row["fence"]}'
                lock_path = directory/'.lock'
                if not lock_path.exists():
                    lock_path = config.data/'checks'/str(row['check_id'])/'input/.lock'
                try:
                    with lock_path.open('rb') as lock:
                        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        c.execute("UPDATE reservations SET state='expired',fence=fence+1 WHERE id=%s AND state='uploading'",(row['id'],))
                except BlockingIOError:
                    continue  # Live API writer owns the lock until abort/commit.
                except FileNotFoundError:
                    c.execute("UPDATE reservations SET state='expired',fence=fence+1 WHERE id=%s AND state='uploading'",(row['id'],))
            self.db.expire_reserved(c)
        self.retention()
        grace = config.get('retention','stagingOrphanGraceSeconds')
        for parent in ('staging','checks','work'):
            for directory in (config.data/parent).iterdir():
                if directory.is_symlink() or not directory.is_dir() or time.time()-directory.stat().st_mtime<grace:
                    continue
                try:
                    candidate = uuid.UUID(directory.name.split('-')[0]) if parent=='staging' else uuid.UUID(directory.name)
                except ValueError:
                    # Staging names are full UUID + fence, not an arbitrary client path.
                    try:
                        candidate = uuid.UUID(directory.name.rsplit('-',1)[0])
                    except ValueError:
                        continue
                with self.db.transaction() as c:
                    if parent=='staging':
                        live = c.execute("SELECT 1 FROM reservations WHERE id=%s AND state='uploading'",(candidate,)).fetchone()
                    elif parent=='work':
                        live = c.execute("SELECT 1 FROM checks WHERE id=%s AND state NOT IN ('completed','rejected','execution_failed')",(candidate,)).fetchone()
                    else:
                        live = c.execute('SELECT 1 FROM checks WHERE id=%s',(candidate,)).fetchone()
                if not live:
                    shutil.rmtree(directory,ignore_errors=True)

    def retention(self):
        with self.db.transaction() as c:
            # Extend undelivered artifacts, bounded by the configured terminal retention.
            c.execute("UPDATE artifacts a SET expires_at=ch.finished_at+(%s * interval '1 day') FROM checks ch JOIN outbox o ON o.check_id=ch.id WHERE a.check_id=ch.id AND a.deleted_at IS NULL AND o.state<>'delivered' AND a.expires_at<ch.finished_at+(%s * interval '1 day')",(self.config.get('retention','undeliveredArtifactsDays'),)*2)
            rows = c.execute('SELECT * FROM artifacts WHERE deleted_at IS NULL AND expires_at<=clock_timestamp() FOR UPDATE SKIP LOCKED').fetchall()
            for row in rows:
                path = self.config.data/row['relative_path']
                if path.is_relative_to(self.config.data/'checks') and not path.is_symlink():
                    path.unlink(missing_ok=True)
                    c.execute('UPDATE artifacts SET deleted_at=clock_timestamp() WHERE check_id=%s AND kind=%s',(row['check_id'],row['kind']))
            c.execute("UPDATE outbox SET payload=NULL,state=CASE WHEN state='pending' THEN 'expired' ELSE state END WHERE created_at<clock_timestamp()-(%s * interval '1 day') AND (lease_until IS NULL OR lease_until<=clock_timestamp())",(self.config.get('retention','eventPayloadDays'),))
            c.execute('DELETE FROM idempotency WHERE expires_at<=clock_timestamp()')
            expired_files = c.execute("SELECT id FROM checks WHERE state IN ('completed','rejected','execution_failed') AND finished_at<clock_timestamp()-(%s * interval '1 day') LIMIT %s",(self.config.get('retention','undeliveredArtifactsDays'),self.config.get('retention','cleanupBatchSize'))).fetchall()
            for item in expired_files:
                path = self.config.data/'checks'/str(item['id'])
                if not path.is_symlink():
                    shutil.rmtree(path,ignore_errors=True)
            # Accepted identity and grading survive for metadataDays; remove dependents together.
            old = c.execute("SELECT id,reservation_id FROM checks WHERE state IN ('completed','rejected','execution_failed') AND finished_at<clock_timestamp()-(%s * interval '1 day') FOR UPDATE SKIP LOCKED",(self.config.get('retention','checkMetadataDays'),)).fetchall()
            for row in old:
                for table in ('docker_resources','artifacts','outbox','capacity_claims'):
                    c.execute(f'DELETE FROM {table} WHERE check_id=%s',(row['id'],))
                c.execute('DELETE FROM checks WHERE id=%s',(row['id'],))
                c.execute('DELETE FROM reservations WHERE id=%s',(row['reservation_id'],))
            c.execute("DELETE FROM capacity_claims WHERE released_at IS NOT NULL AND check_id IS NULL AND reservation_id IN (SELECT id FROM reservations WHERE created_at<clock_timestamp()-(%s * interval '1 day'))",(self.config.get('retention','reservationMetadataDays'),))
            c.execute("DELETE FROM reservations WHERE state IN ('expired','cancelled') AND created_at<clock_timestamp()-(%s * interval '1 day') AND NOT EXISTS (SELECT 1 FROM capacity_claims WHERE reservation_id=reservations.id)",(self.config.get('retention','reservationMetadataDays'),))

    def run(self):
        self.config.prepare()
        tasks = set()
        with ThreadPoolExecutor(max_workers=self.config.hard_limit) as pool:
            try:
                while not self.stop.is_set():
                    try:
                        self.recover()
                        self.housekeeping()
                        self.dependency_check(self.config)
                        tasks = {task for task in tasks if not task.done()}
                        with self.db.transaction() as c:
                            cap = c.execute('SELECT max_concurrent_checks FROM service_settings WHERE id=1').fetchone()['max_concurrent_checks']
                            blocked = c.execute('SELECT 1 FROM checks WHERE cleanup_blocked LIMIT 1').fetchone()
                        ready = cap<=self.config.hard_limit and not blocked
                        self.db.presence(self.owner,'worker',ready)
                        if ready:
                            while len(tasks)<self.config.hard_limit and not self.stop.is_set():
                                row = self.db.claim(self.owner,self.container)
                                if not row:
                                    break
                                tasks.add(pool.submit(self.execute,row))
                    except Exception:
                        try:
                            self.db.presence(self.owner,'worker',False,'dependency_unavailable')
                        except Exception:
                            pass
                    self.stop.wait(self.config.get('admission','workerHeartbeatSeconds'))
            finally:
                self.stop.set()
                self.db.presence(self.owner,'worker',False,'stopping')
                with self.mutex:
                    for process in self.processes.values():
                        stop_process(process)
