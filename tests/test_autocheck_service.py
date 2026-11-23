"""Service regression tests. PostgreSQL tests use a disposable, isolated schema.

TEST_AUTOCHECK_DSN enables real transactions. TEST_AUTOCHECK_ENGINE=1 also enables
real engine/Docker execution (never substitutes fake grades).
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import hmac
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import uuid
import zipfile
import yaml

try:
    import psycopg
    from psycopg.conninfo import make_conninfo
    from psycopg import sql
    import httpx
    from starlette.testclient import TestClient
    from element_check_service.api import create_app
    from element_check_service.auth import Registry
    from element_check_service.common import ServiceError, encoded
    from element_check_service.config import Config, ROOT
    from element_check_service.db import Database
    from element_check_service.inputs import prepare_criteria
    from element_check_service.notifier import Notifier, signed_headers
    from element_check_service.webhooks import webhook_url, pinned_destination, UnsafeWebhook
    from element_check_service.uploads import UploadParser
    from element_check_service.worker import Worker, failure, artifacts_from
    from element_check_service.docker_control import DockerControl, PREFIX
except ImportError:
    psycopg = None

DSN = os.environ.get('TEST_AUTOCHECK_DSN')
TOKEN = 'test-token-only-never-a-production-secret'
CRITERIA = b'name: Example\nchecks:\n- id: exists\n  type: exists\n  target: Catalog\n'


def manifest(submission='sub-1',dump=b'dump',criteria=CRITERIA,format='yaml'):
    return {'submissionId':submission,'studentId':'student-1','assignmentId':'task-1','attempt':1,'runMode':'isolated',
            'dump':{'format':'xdump','sizeBytes':len(dump),'sha256':sha256(dump).hexdigest()},
            'criteria':{'format':format,'sizeBytes':len(criteria),'sha256':sha256(criteria).hexdigest()}}


def registry_file(directory):
    path = directory/'clients.json'
    path.write_text(json.dumps({'clients':[{'clientId':'lk','allowedRunModes':['isolated','integration'],'tokens':[{'keyId':'test','enabled':True,
        'scopes':['checks:read','checks:write','settings:read','settings:write','delivery:retry'],'tokenSha256':sha256(TOKEN.encode()).hexdigest()}]}]}))
    return path


@unittest.skipIf(psycopg is None,'Install service dependencies')
class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = Config(data=self.root)

    def test_registry_rotation_keeps_last_valid_and_enforces_scopes(self):
        path = registry_file(self.root)
        registry = Registry(path)
        self.assertEqual(registry.authorize('Bearer '+TOKEN,'checks:write')['clientId'],'lk')
        path.write_text('{broken')
        self.assertEqual(registry.authorize('Bearer '+TOKEN,'checks:read')['clientId'],'lk')
        with self.assertRaises(ServiceError):
            registry.authorize('Bearer wrong','checks:read')

    def test_webhook_destination_validation_and_dns_pinning(self):
        for value in ('http://example.org', 'https://user:pass@example.org', 'https://example.org/#',
                      'https://127.0.0.1', 'https://10.0.0.1', 'https://169.254.169.254',
                      'https://[::1]', 'https://[::ffff:127.0.0.1]', 'https://[fe80::1%25eth0]',
                      'https://localhost', 'https://example.org/\n', 'https://example.org:0',
                      'https://224.0.0.1', 'https://[ff02::1]', 'https://example.org:65536'):
            with self.subTest(value=value), self.assertRaises(UnsafeWebhook):
                webhook_url(value)
        public = [(2, 1, 6, '', ('93.184.216.34', 443))]
        with patch('element_check_service.webhooks.getaddrinfo', return_value=public) as dns:
            target, host, sni = pinned_destination('https://system.example:8443/events?job=1')
            self.assertEqual(str(target), 'https://93.184.216.34:8443/events?job=1')
            self.assertEqual(host, 'system.example:8443')
            self.assertEqual(sni, 'system.example')
            dns.assert_called_once()
        with patch('element_check_service.webhooks.getaddrinfo', return_value=public+[(2, 1, 6, '', ('10.0.0.1', 443))]):
            with self.assertRaises(UnsafeWebhook):
                pinned_destination('https://system.example/events')

    def test_webhook_registry_accepts_signing_keys_without_default_url(self):
        path = self.root/'webhooks.json'
        path.write_text(json.dumps({'clients': [{'clientId': 'system-a', 'currentKeyId': 'v1', 'keys': {'v1': 'k'*32}}]}))
        self.assertIn('system-a', Registry(path, webhook=True).clients)


    def test_read_only_token_cannot_write(self):
        path=registry_file(self.root)
        value=json.loads(path.read_text())
        value['clients'][0]['tokens'][0]['scopes']=['checks:read']
        path.write_text(json.dumps(value))
        registry=Registry(path)
        self.assertEqual(registry.authorize('Bearer '+TOKEN,'checks:read')['clientId'],'lk')
        with self.assertRaises(ServiceError) as error:
            registry.authorize('Bearer '+TOKEN,'checks:write')
        self.assertEqual(error.exception.status,403)

    def test_transient_containers_share_operator_budget(self):
        from element_check_service.docker_control import resource_parent
        with patch.dict(os.environ, {'AUTOCHECK_CGROUP_PARENT': 'autocheck.slice'}):
            for role in ('executor', 'database'):
                result = resource_parent(['create', '--name', 'element-'+role, 'image'])
                self.assertEqual(result[1:3], ['--cgroup-parent', 'autocheck.slice'])
            for override in (['--cgroup-parent', 'system.slice'], ['--cgroup-parent=system.slice']):
                with self.assertRaises(ValueError):
                    resource_parent(['create', *override, 'image'])
            self.assertEqual(resource_parent(['network', 'create', 'element-net']), ['network', 'create', 'element-net'])
        with patch.dict(os.environ, {'AUTOCHECK_CGROUP_PARENT': '../system.slice'}):
            with self.assertRaises(ValueError):
                resource_parent(['create', 'image'])
        with patch.dict(os.environ, {'AUTOCHECK_CGROUP_PARENT': ''}):
            self.assertEqual(resource_parent(['create', 'image']), ['create', 'image'])

    def test_signature_is_over_original_bytes_and_attempt_timestamp(self):
        payload = b'{ "eventId": "x" }'
        headers = signed_headers(payload,'x',123,'key','a'*32)
        expected = hmac.new(b'a'*32,b'123.x.'+payload,sha256).hexdigest()
        self.assertEqual(headers['X-Autocheck-Signature'],'v1='+expected)
        self.assertNotEqual(headers,signed_headers(payload,'x',124,'key','a'*32))

    def test_multipart_arbitrary_chunk_boundaries_and_ignored_filename(self):
        content = (b'--bound\r\nContent-Disposition: form-data; name="dump"; filename="../../evil"\r\n\r\ndump\r\n'
            b'--bound\r\nContent-Disposition: form-data; name="criteria"\r\n\r\n'+CRITERIA+b'\r\n--bound--\r\n')
        parser = UploadParser('multipart/form-data; boundary=bound',self.root,manifest())
        for index in range(0,len(content),3):
            parser.parser.write(content[index:index+3])
        parser.finish()
        self.assertEqual((self.root/'dump.xdump').read_bytes(),b'dump')
        self.assertEqual((self.root/'criteria.yaml').read_bytes(),CRITERIA)

    def test_multipart_truncation_rejected(self):
        parser = UploadParser('multipart/form-data; boundary=bound',self.root,manifest())
        parser.parser.write(b'--bound\r\nContent-Disposition: form-data; name="dump"\r\n\r\ndump')
        with self.assertRaises(ServiceError):
            parser.finish()
        parser.close()

    def test_zip_traversal_duplicate_symlink_and_expansion_rejected(self):
        from element_test.yaml_io import InputError
        for case in ('traversal','duplicate','symlink','expansion'):
            with self.subTest(case=case):
                source = self.root/(case+'.zip')
                with zipfile.ZipFile(source,'w') as z:
                    z.writestr('assignment.yaml',CRITERIA)
                    if case=='traversal':
                        z.writestr('../escape','x')
                    elif case=='duplicate':
                        z.writestr('assignment.yaml',CRITERIA)
                    elif case=='symlink':
                        info = zipfile.ZipInfo('link');info.external_attr=(stat.S_IFLNK|0o777)<<16
                        z.writestr(info,'/etc/passwd')
                    else:
                        z.writestr('big','a'*1000)
                        self.config.values['limits']['maxCriteriaExpandedBytes']=100
                with self.assertRaises(InputError):
                    prepare_criteria(source,self.root/case,self.config)

    def test_yaml_external_requirements_alias_depth_and_overrides_rejected(self):
        from element_test.yaml_io import InputError
        for i,body in enumerate([b'name: x\nformRequirements: ../outside.yaml\n',b'name: &a x\nchecks: [*a]\n',
            b'name: x\nchecks: '+b'['*60+b'0'+b']'*60,b'name: x\nchecks: [{id: x, type: runtime, command: hello}]\n']):
            source = self.root/'criteria.yaml';source.write_bytes(body)
            with self.assertRaises(InputError):
                prepare_criteria(source,self.root/str(i),self.config)


@unittest.skipUnless(psycopg and DSN,'Set TEST_AUTOCHECK_DSN for PostgreSQL service tests')
class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = 'autocheck_test_'+uuid.uuid4().hex
        with psycopg.connect(DSN) as c:
            c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(cls.schema)))
        cls.dsn = make_conninfo(DSN,options='-c search_path='+cls.schema)

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(DSN) as c:
            c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(cls.schema)))

    def setUp(self):
        self.temp = TemporaryDirectory(prefix='autocheck-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = Config(data=self.root)
        self.config.values = deepcopy(self.config.values)
        self.config.values['limits']['minFreeDiskBytes']=0
        self.config.prepare()
        self.db = Database(self.config,self.dsn)
        self.db.migrate()
        with self.db.transaction() as c:
            c.execute('TRUNCATE reservations,checks,capacity_claims,idempotency,docker_resources,artifacts,outbox,worker_presence,delivery_clients,audit_log CASCADE')
            c.execute('UPDATE service_settings SET max_concurrent_checks=2,accepting=true,revision=1')
            c.execute("INSERT INTO delivery_clients VALUES ('lk',clock_timestamp())")
        self.db.presence('ready-worker','worker',True)
        self.db.presence('ready-notifier','notifier',True)
        self.registry = Registry(registry_file(self.root))
        self.client = TestClient(create_app(self.config,self.db,self.registry))
        self.client.headers['Authorization']='Bearer '+TOKEN
        self.addCleanup(self.client.close)
        self.identity = self.registry.clients['lk']
        self.dns = patch('element_check_service.webhooks.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
        self.dns.start()
        self.addCleanup(self.dns.stop)

    def reserve(self,m=None,key=None):
        response = self.client.post('/v1/reservations',json=m or manifest(),headers={'Idempotency-Key':key or str(uuid.uuid4())})
        self.assertIn(response.status_code,(200,201),response.text)
        return response.json()

    def upload(self,reservation,dump=b'dump',criteria=CRITERIA,key='upload-1'):
        return self.client.post('/v1/checks',headers={'X-Reservation-Id':reservation['reservationId'],'Idempotency-Key':key},
            files={'dump':('dump.xdump',dump),'criteria':('assignment.yaml',criteria)})

    def accepted(self,m=None,dump=b'dump',criteria=CRITERIA):
        r = self.reserve(m)
        response = self.upload(r,dump,criteria,key='upload-'+r['reservationId'])
        self.assertEqual(response.status_code,202,response.text)
        return r

    def terminal(self,m=None):
        r = self.accepted(m)
        worker = Worker(self.db,self.config)
        row = self.db.claim(worker.owner)
        with self.db.transaction() as c:
            c.execute("UPDATE checks SET state='finalizing',supervisor_stopped=true WHERE id=%s",(row['id'],))
        self.db.finalize(row['id'],row['fence'],failure('engine_failure'),[])
        return r

    def test_criteria_version_is_optional_in_status_and_webhook(self):
        for submission, fields in [('current', {}), ('legacy', {'criteriaVersion': 'programming-v1'})]:
            with self.subTest(submission=submission):
                check_id = self.terminal(dict(manifest(submission), **fields))['checkId']
                response = self.client.get('/v1/checks/'+check_id)
                self.assertEqual(response.status_code, 200)
                with self.db.transaction() as c:
                    row = c.execute('SELECT payload FROM outbox WHERE check_id=%s',(check_id,)).fetchone()
                for snapshot in (response.json(), json.loads(bytes(row['payload']))['check']):
                    self.assertEqual(snapshot['status'], 'execution_failed')
                    self.assertEqual(snapshot.get('criteriaVersion'), fields.get('criteriaVersion'))
                    self.assertEqual('criteriaVersion' in snapshot, bool(fields))
                    self.assertEqual(snapshot['inputHashes']['criteriaSha256'], sha256(CRITERIA).hexdigest())

    def test_100_concurrent_reservations_obey_global_cap_and_no_hidden_queue(self):
        self.db.settings({'maxConcurrentChecks':4},'"1"','operator')
        def reserve(i):
            try:
                return self.db.reserve(self.identity,str(i),manifest('sub-'+str(i)))[1]
            except ServiceError as e:
                return e.status
        with ThreadPoolExecutor(max_workers=16) as pool:
            values = list(pool.map(reserve,range(100)))
        self.assertEqual(values.count(201),4)
        self.assertEqual(values.count(429),96)
        self.assertEqual(self.db.capacity()['occupiedSlots'],4)

    def test_upload_commit_replay_before_reading_body_and_cross_client_404(self):
        r = self.accepted()
        response = self.client.post('/v1/checks',content=b'not multipart',headers={'X-Reservation-Id':r['reservationId'],'Idempotency-Key':'upload-'+r['reservationId']})
        self.assertEqual(response.status_code,202,response.text)
        with self.assertRaises(ServiceError) as error:
            self.db.get_check('other',uuid.UUID(r['checkId']))
        self.assertEqual(error.exception.status,404)
        with self.assertRaises(ServiceError) as error:
            self.db.get_reservation('other',uuid.UUID(r['reservationId']))
        self.assertEqual(error.exception.status,404)
        with self.db.transaction() as c:
            self.assertEqual(c.execute('SELECT count(*) AS n FROM checks').fetchone()['n'],1)
        with self.assertRaises(ServiceError):
            self.db.reserve(self.identity,'new-key',manifest())

    def test_reject_headers_does_not_read_multipart(self):
        response = self.client.post('/v1/checks',content=b'invalid',headers={'Authorization':'Bearer wrong'})
        self.assertEqual(response.status_code,401)
        self.assertEqual(self.db.capacity()['occupiedSlots'],0)

    def test_hash_mismatch_aborts_upload_and_releases_claim(self):
        r = self.reserve()
        response = self.upload(r,b'bad!')
        self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.db.capacity()['occupiedSlots'],0)
        self.assertEqual(self.client.get('/v1/checks/'+r['checkId']).status_code,404)
        self.assertFalse((self.root/'checks'/r['checkId']).exists())

    def test_settings_etag_pause_and_reduction_do_not_revoke_reservations(self):
        a,b = self.reserve(),self.reserve(manifest('sub-2'))
        response = self.client.patch('/v1/admin/settings',json={'maxConcurrentChecks':1,'accepting':False},headers={'If-Match':'"1"'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.headers['etag'],'"2"')
        self.assertEqual(self.client.patch('/v1/admin/settings',json={'accepting':True},headers={'If-Match':'"1"'}).status_code,412)
        self.assertEqual(self.client.patch('/v1/admin/settings',json={'accepting':True}).status_code,428)
        self.assertEqual(self.upload(a).status_code,202)
        self.assertEqual(self.db.capacity()['occupiedSlots'],2)
        self.db.migrate()
        self.assertEqual(self.db.settings()['maxConcurrentChecks'],1)

    def test_idempotency_conflict_and_expiration_tombstone(self):
        r = self.reserve(key='same')
        self.assertEqual(self.reserve(key='same')['reservationId'],r['reservationId'])
        self.assertEqual(self.client.post('/v1/reservations',json=manifest('other'),headers={'Idempotency-Key':'same'}).status_code,409)
        self.assertEqual(self.client.delete('/v1/reservations/'+r['reservationId']).status_code,204)
        self.assertEqual(self.client.post('/v1/reservations',json=manifest(),headers={'Idempotency-Key':'same'}).status_code,410)
        self.reserve(key='different')

    def test_running_has_one_claim_and_stale_worker_never_executes_again(self):
        r = self.accepted()
        row = self.db.claim('lost')
        self.assertIsNone(self.db.claim('other'))
        with self.db.transaction() as c:
            c.execute("UPDATE checks SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(row['id'],))
        worker = Worker(self.db,self.config)
        worker.recover()
        value = self.db.get_check('lk',row['id'])
        self.assertEqual(value['status'],'finalizing')
        self.assertEqual(self.db.capacity()['occupiedSlots'],1)
        self.assertEqual(self.db.capacity()['cleanupBlocked'],1)
        # A live old supervisor acknowledges it has killed/reaped its child group.
        with self.db.transaction() as c:
            c.execute('UPDATE checks SET supervisor_stopped=true WHERE id=%s',(row['id'],))
        worker.recover()
        self.assertEqual(self.db.get_check('lk',row['id'])['status'],'execution_failed')
        self.assertEqual(self.db.capacity()['occupiedSlots'],0)
        self.assertFalse(self.db.finalize(row['id'],row['fence'],failure('engine_failure'),[]))

    def test_terminal_and_outbox_atomic_and_delivery_retries_keep_bytes(self):
        r = self.terminal()
        config_path = self.root/'webhooks.json'
        config_path.write_text(json.dumps({'clients':[{'clientId':'lk','url':'https://lk.example.edu/events','currentKeyId':'k','keys':{'k':'a'*32}}]}))
        seen=[]
        def handler(request):
            seen.append((request.content,request.headers))
            return httpx.Response(503 if len(seen)==1 else 204)
        notifier = Notifier(self.db,self.config,Registry(config_path,webhook=True),httpx.MockTransport(handler))
        row = notifier.claim();self.assertIsNotNone(row)
        self.assertIsNone(notifier.claim())
        notifier.send(row)
        with self.db.transaction() as c:
            c.execute('UPDATE outbox SET next_attempt_at=clock_timestamp()')
        notifier.send(notifier.claim())
        self.assertEqual(seen[0][0],seen[1][0])
        self.assertEqual(json.loads(seen[0][0])['check']['status'],'execution_failed')
        self.assertEqual(self.db.get_check('lk',uuid.UUID(r['checkId']))['delivery']['status'],'delivered')
        self.assertEqual(self.db.capacity()['occupiedSlots'],0)

    def test_request_webhook_is_durable_idempotent_and_used_for_retries(self):
        body = dict(manifest(), webhookUrl='https://system-a.example/results?attempt=1')
        r = self.reserve(body, key='webhook-request')
        self.assertEqual(self.reserve(body, key='webhook-request')['reservationId'], r['reservationId'])
        changed = dict(body, webhookUrl='https://system-b.example/results')
        response = self.client.post('/v1/reservations', json=changed, headers={'Idempotency-Key': 'webhook-request'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.upload(r).status_code, 202)
        row = self.db.claim('worker')
        with self.db.transaction() as c:
            c.execute("UPDATE checks SET state='finalizing',supervisor_stopped=true WHERE id=%s", (row['id'],))
        self.db.finalize(row['id'], row['fence'], failure('engine_failure'), [])
        path = self.root/'webhooks.json'
        path.write_text(json.dumps({'clients': [{'clientId': 'lk', 'url': 'https://legacy.example/events',
                                                'currentKeyId': 'v1', 'keys': {'v1': 's'*32}}]}))
        seen = []
        def receiver(request):
            seen.append(request)
            return httpx.Response(503 if len(seen) == 1 else 204)
        n = Notifier(self.db, self.config, Registry(path, webhook=True), httpx.MockTransport(receiver))
        n.send(n.claim())
        with self.db.transaction() as c:
            c.execute('UPDATE outbox SET next_attempt_at=clock_timestamp()')
        # Recreate notifier to demonstrate retries do not depend on process memory.
        n = Notifier(self.db, self.config, Registry(path, webhook=True), httpx.MockTransport(receiver))
        n.send(n.claim())
        self.assertEqual(len(seen), 2)
        for request in seen:
            self.assertEqual(str(request.url), 'https://93.184.216.34/results?attempt=1')
            self.assertEqual(request.headers['host'], 'system-a.example')
            self.assertEqual(request.extensions['sni_hostname'], 'system-a.example')
            headers = signed_headers(request.content, request.headers['x-autocheck-event-id'],
                                     int(request.headers['x-autocheck-timestamp']), 'v1', 's'*32)
            self.assertEqual(request.headers['x-autocheck-signature'], headers['X-Autocheck-Signature'])
        self.assertEqual(seen[0].content, seen[1].content)
        self.assertEqual(self.db.get_check('lk', row['id'])['delivery']['status'], 'delivered')

    def test_two_clients_same_submission_have_isolated_callbacks_and_signatures(self):
        clients = []
        webhook_clients = []
        reservations = []
        for index in range(2):
            identity, token = 'system-'+str(index), TOKEN+str(index)
            client = deepcopy(self.identity)
            client['clientId'] = identity
            client['tokens'][0]['tokenSha256'] = sha256(token.encode()).hexdigest()
            clients.append(client)
            webhook_clients.append({'clientId': identity, 'currentKeyId': 'v1', 'keys': {'v1': str(index)*32}})
            with self.db.transaction() as c:
                c.execute('INSERT INTO delivery_clients(client_id) VALUES (%s)', (identity,))
        self.registry.path.write_text(json.dumps({'clients': clients}))
        for index in range(2):
            self.client.headers['Authorization'] = 'Bearer '+TOKEN+str(index)
            r = self.accepted(dict(manifest(), webhookUrl='https://system-'+str(index)+'.example/events'))
            reservations.append(r)
        for index, r in enumerate(reservations):
            other = self.client.get('/v1/checks/'+r['checkId'], headers={'Authorization': 'Bearer '+TOKEN+str(1-index)})
            self.assertEqual(other.status_code, 404)
            row = self.db.claim('worker')
            with self.db.transaction() as c:
                c.execute("UPDATE checks SET state='finalizing',supervisor_stopped=true WHERE id=%s", (row['id'],))
            self.db.finalize(row['id'], row['fence'], failure('engine_failure'), [])
        path = self.root/'webhooks.json'
        path.write_text(json.dumps({'clients': webhook_clients}))
        seen = []
        n = Notifier(self.db, self.config, Registry(path, webhook=True),
                     httpx.MockTransport(lambda request: (seen.append(request), httpx.Response(204))[1]))
        for _ in range(2):
            n.send(n.claim())
        self.assertEqual({r.headers['host'] for r in seen}, {'system-0.example', 'system-1.example'})
        for request in seen:
            index = int(request.headers['host'][7])
            expected = signed_headers(request.content, request.headers['x-autocheck-event-id'],
                                      int(request.headers['x-autocheck-timestamp']), 'v1', str(index)*32)
            self.assertEqual(request.headers['x-autocheck-signature'], expected['X-Autocheck-Signature'])

    def test_webhook_rejects_unsafe_requests_and_dns_without_sending(self):
        for url in ('http://example.org', 'https://127.0.0.1', 'https://user:pass@example.org'):
            response = self.client.post('/v1/reservations', json=dict(manifest(), webhookUrl=url),
                                        headers={'Idempotency-Key': str(uuid.uuid4())})
            self.assertEqual(response.status_code, 422)
        r = self.terminal()
        path = self.root/'webhooks.json'
        path.write_text(json.dumps({'clients': [{'clientId': 'lk', 'url': 'https://system.example/events',
                                                'currentKeyId': 'v1', 'keys': {'v1': 's'*32}}]}))
        seen = []
        n = Notifier(self.db, self.config, Registry(path, webhook=True),
                     httpx.MockTransport(lambda request: seen.append(request)))
        with patch('element_check_service.webhooks.getaddrinfo', return_value=[(2, 1, 6, '', ('10.0.0.1', 443))]):
            n.send(n.claim())
        self.assertEqual(seen, [])
        with self.db.transaction() as c:
            row = c.execute('SELECT * FROM outbox').fetchone()
        self.assertEqual((row['state'], row['last_error']), ('failed', 'callback_url_forbidden'))

    def test_delivery_http_400_fails_manual_retry_preserves_event(self):
        r = self.terminal()
        path = self.root/'webhooks.json'
        path.write_text(json.dumps({'clients':[{'clientId':'lk','url':'https://lk.example.edu/events','currentKeyId':'k','keys':{'k':'a'*32}}]}))
        n = Notifier(self.db,self.config,Registry(path,webhook=True),httpx.MockTransport(lambda _:httpx.Response(400)))
        row=n.claim();n.send(row)
        result=self.db.retry_delivery(uuid.UUID(r['checkId']))
        self.assertEqual(result['eventId'],str(row['event_id']))
        self.assertEqual(bytes(n.claim()['payload']),bytes(row['payload']))

    def test_artifact_and_event_retention_keep_grading_metadata(self):
        r = self.terminal()
        cid=uuid.UUID(r['checkId'])
        with self.db.transaction() as c:
            c.execute("UPDATE outbox SET created_at=clock_timestamp()-interval '38 days',deadline=clock_timestamp()-interval '1 day'")
        Worker(self.db,self.config).retention()
        self.assertEqual(self.db.get_check('lk',cid)['status'],'execution_failed')
        with self.assertRaises(ServiceError) as error:
            self.db.retry_delivery(cid)
        self.assertEqual(error.exception.status,410)



    def asgi(self, chunks, headers, delay=0):
        import asyncio
        messages=[]
        iterator=iter(chunks)
        async def receive():
            if delay:
                await asyncio.sleep(delay)
            try:
                block=next(iterator)
            except StopIteration:
                return {'type':'http.request','body':b'','more_body':False}
            return {'type':'http.request','body':block,'more_body':True}
        async def send(message):
            messages.append(message)
        scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'POST','scheme':'http','path':'/v1/checks',
            'raw_path':b'/v1/checks','root_path':'','query_string':b'','server':('test',80),'client':('127.0.0.1',1),
            'headers':[(k.lower().encode(),v.encode()) for k,v in headers.items()]}
        asyncio.run(self.client.app(scope,receive,send))
        return next(m['status'] for m in messages if m['type']=='http.response.start')

    def test_20_mib_chunked_upload_has_bounded_python_memory(self):
        import tracemalloc
        block=b'x'*65536;count=320
        m=manifest();m['dump']['sizeBytes']=len(block)*count
        digest=sha256()
        for _ in range(count):
            digest.update(block)
        m['dump']['sha256']=digest.hexdigest()
        r=self.reserve(m)
        def chunks():
            yield b'--bnd\r\nContent-Disposition: form-data; name="dump"\r\n\r\n'
            yield from (block for _ in range(count))
            yield b'\r\n--bnd\r\nContent-Disposition: form-data; name="criteria"\r\n\r\n'+CRITERIA+b'\r\n--bnd--\r\n'
        tracemalloc.start()
        try:
            status=self.asgi(chunks(),{'Authorization':'Bearer '+TOKEN,'Content-Type':'multipart/form-data; boundary=bnd',
                'X-Reservation-Id':r['reservationId'],'Idempotency-Key':'streamed'})
            _,peak=tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(status,202)
        self.assertLess(peak,8*1024*1024)
        self.assertEqual((self.root/'checks'/r['checkId']/'input/dump.xdump').stat().st_size,20*1024*1024)

    def test_body_limit_counts_chunked_multipart_overhead_and_releases_slot(self):
        r=self.reserve()
        self.config.values['limits']['maxRequestBytes']=100
        status=self.asgi([b'--bnd\r\nContent-Disposition: form-data; name="dump"\r\n\r\n',b'dump',b'\r\n--bnd\r\nContent-Disposition: form-data; name="criteria"\r\n\r\n'+CRITERIA+b'\r\n--bnd--\r\n'],{'Authorization':'Bearer '+TOKEN,'Content-Type':'multipart/form-data; boundary=bnd',
            'X-Reservation-Id':r['reservationId'],'Idempotency-Key':'too-big'})
        self.assertEqual(status,413)
        self.assertEqual(self.db.capacity()['occupiedSlots'],0)


    def test_unauthorized_upload_never_calls_receive(self):
        def chunks():
            raise AssertionError('Unauthorized request body was read')
            yield b''
        self.assertEqual(self.asgi(chunks(),{'Authorization':'Bearer invalid'}),401)

    def test_absolute_upload_deadline_is_independent_of_idle_timeout(self):
        r=self.reserve()
        self.config.values['admission']['uploadMaxDurationSeconds']=0.01
        status=self.asgi([b'data'],{'Authorization':'Bearer '+TOKEN,'Content-Type':'multipart/form-data; boundary=bnd',
            'X-Reservation-Id':r['reservationId'],'Idempotency-Key':'deadline'},delay=0.03)
        self.assertEqual(status,422)
        self.assertEqual(self.db.capacity()['occupiedSlots'],0)

    def test_idle_upload_timeout_fences_writer_and_releases_slot(self):
        r=self.reserve()
        self.config.values['admission']['uploadIdleTimeoutSeconds']=0.01
        status=self.asgi([b'data'],{'Authorization':'Bearer '+TOKEN,'Content-Type':'multipart/form-data; boundary=bnd',
            'X-Reservation-Id':r['reservationId'],'Idempotency-Key':'idle'},delay=0.03)
        self.assertEqual(status,422)
        self.assertEqual(self.db.capacity()['occupiedSlots'],0)

    def test_response_lost_after_upload_commit_preserves_input_and_replays(self):
        r=self.reserve()
        commit=self.db.commit_upload
        def lost(*args):
            commit(*args)
            raise psycopg.OperationalError('simulated lost commit response')
        with patch.object(self.db,'commit_upload',side_effect=lost):
            response=self.upload(r)
        self.assertEqual(response.status_code,503)
        self.assertTrue((self.root/'checks'/r['checkId']/'input/dump.xdump').is_file())
        self.assertEqual(self.client.get('/v1/checks/'+r['checkId']).json()['status'],'accepted')
        self.assertEqual(self.upload(r).status_code,202)
        self.assertEqual(self.db.capacity()['occupiedSlots'],1)

    def test_duplicate_multipart_parts_cancel_upload(self):
        r=self.reserve()
        response=self.client.post('/v1/checks',headers={'X-Reservation-Id':r['reservationId'],'Idempotency-Key':'duplicate'},
            files=[('dump',('first',b'dump')),('dump',('second',b'dump')),('criteria',('criteria',CRITERIA))])
        self.assertEqual(response.status_code,422)
        self.assertEqual(self.db.capacity()['occupiedSlots'],0)

    def test_single_live_upload_and_reservation_ttl_does_not_abort_it(self):
        r=self.reserve()
        row,_=self.db.begin_upload('lk',uuid.UUID(r['reservationId']),'key')
        with self.assertRaises(ServiceError) as conflict:
            self.db.begin_upload('lk',row['id'],'another')
        self.assertEqual(conflict.exception.code,'upload_in_progress')
        with self.db.transaction() as c:
            c.execute("UPDATE reservations SET expires_at=clock_timestamp()-interval '1 day' WHERE id=%s",(row['id'],))
        self.assertEqual(self.db.get_reservation('lk',row['id'])['status'],'uploading')
        self.db.upload_heartbeat(row['id'],row['fence'])
        self.db.abort_upload(row['id'],row['fence'])
        r=self.reserve()
        self.assertIsNone(self.db.begin_upload('lk',uuid.UUID(r['reservationId']),'key')[1])

    def test_live_upload_file_lock_prevents_cleanup_until_writer_releases_it(self):
        import fcntl
        r=self.reserve();row,_=self.db.begin_upload('lk',uuid.UUID(r['reservationId']),'key')
        directory=self.root/'staging'/f'{row["id"]}-{row["fence"]}';directory.mkdir()
        with (directory/'.lock').open('wb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            with self.db.transaction() as c:
                c.execute("UPDATE reservations SET heartbeat_at=clock_timestamp()-interval '1 day',upload_deadline=clock_timestamp()-interval '1 second' WHERE id=%s",(row['id'],))
            Worker(self.db,self.config).housekeeping()
            self.assertEqual(self.db.get_reservation('lk',row['id'])['status'],'uploading')
        Worker(self.db,self.config).housekeeping()
        self.assertEqual(self.db.get_reservation('lk',row['id'])['status'],'expired')
        self.assertEqual(self.db.capacity()['occupiedSlots'],0)

    def test_expired_reservation_reuses_slot_and_keeps_idempotency_tombstone(self):
        a=self.reserve(key='old')
        with self.db.transaction() as c:
            c.execute("UPDATE reservations SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(uuid.UUID(a['reservationId']),))
        self.reserve(manifest('sub-2'))
        self.assertEqual(self.db.capacity()['occupiedSlots'],1)
        self.assertEqual(self.client.post('/v1/reservations',json=manifest(),headers={'Idempotency-Key':'old'}).status_code,410)

    def test_start_deadline_failure_does_not_execute_engine(self):
        r=self.accepted()
        with self.db.transaction() as c:
            c.execute("UPDATE checks SET accepted_at=clock_timestamp()-interval '11 seconds' WHERE id=%s",(uuid.UUID(r['checkId']),))
        Worker(self.db,self.config).recover()
        value=self.db.get_check('lk',uuid.UUID(r['checkId']))
        self.assertEqual(value['error']['code'],'start_deadline_exceeded')
        self.assertEqual(self.db.capacity()['occupiedSlots'],0)
        self.assertIsNone(self.db.claim('worker'))

    def test_terminal_transaction_rolls_back_state_and_release_when_outbox_fails(self):
        self.accepted();row=self.db.claim('worker')
        with self.db.transaction() as c:
            c.execute("UPDATE checks SET state='finalizing',supervisor_stopped=true WHERE id=%s",(row['id'],))
        with patch('element_check_service.db.encoded',side_effect=ValueError('serialization failure')):
            with self.assertRaises(ValueError):
                self.db.finalize(row['id'],row['fence'],failure('engine_failure'),[])
        self.assertEqual(self.db.get_check('lk',row['id'])['status'],'finalizing')
        self.assertEqual(self.db.capacity()['occupiedSlots'],1)
        with self.db.transaction() as c:
            self.assertEqual(c.execute('SELECT count(*) AS n FROM outbox').fetchone()['n'],0)

    def test_docker_intent_precedes_create_and_stale_fence_cannot_start(self):
        self.accepted();row=self.db.claim('worker');seen=[]
        def runner(args,**kwargs):
            seen.append(args)
            with self.db.transaction() as c:
                intent=c.execute('SELECT * FROM docker_resources WHERE check_id=%s',(row['id'],)).fetchone()
            self.assertEqual(intent['operation_state'],'creating')
            self.assertIn(PREFIX+'.check='+str(row['id']),args)
            return subprocess.CompletedProcess(args,0,b'fake-id\n',b'')
        control=DockerControl(self.db,row,runner=runner)
        try:
            control.execute({'args':['create','--name','element-test-one','--ulimit','cpu=5:5','image'],'input':None})
            with self.assertRaises(ValueError):
                control.execute({'args':['create','--name','element-test-two','image'],'input':None})
            with self.db.transaction() as c:
                c.execute('UPDATE checks SET fence=fence+1 WHERE id=%s',(row['id'],))
            with self.assertRaises(ValueError):
                control.execute({'args':['start','element-test-one']})
            self.assertEqual(len(seen),1)
        finally:
            control.close()

    def test_unknown_create_outcome_retains_capacity(self):
        self.accepted();row=self.db.claim('worker')
        name='element-test-uncertain'
        with self.db.transaction() as c:
            c.execute("INSERT INTO docker_resources(intent_name,check_id,fence,kind,operation_state) VALUES (%s,%s,%s,'container','uncertain')",(name,row['id'],row['fence']))
        def runner(args,**kwargs):
            return subprocess.CompletedProcess(args,1 if args[0]=='inspect' else 0,b'',b'')
        control=DockerControl(self.db,row,runner=runner)
        try:
            self.assertFalse(control.cleanup())
            self.assertEqual(self.db.capacity()['occupiedSlots'],1)
        finally:
            control.close()


    def test_failed_undelivered_payload_counts_toward_backpressure(self):
        self.terminal()
        with self.db.transaction() as c:
            c.execute("UPDATE outbox SET state='failed'")
        self.config.values['limits']['maxUndeliveredResultBytes']=1
        response=self.client.post('/v1/reservations',json=manifest('next'),headers={'Idempotency-Key':'next'})
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.db.capacity()['occupiedSlots'],0)

    def test_disabled_delivery_does_not_claim_or_send_pending_event(self):
        self.terminal()
        self.config.values['delivery']['enabled'] = False
        notifier = Notifier(self.db, self.config, registry=object())
        self.assertIsNone(notifier.claim())
        with self.db.transaction() as c:
            row = c.execute('SELECT state,attempt_count,lease_owner FROM outbox').fetchone()
        self.assertEqual(row['state'], 'pending')
        self.assertEqual(row['attempt_count'], 0)
        self.assertIsNone(row['lease_owner'])

    def test_cleanup_never_removes_resource_with_foreign_labels(self):
        self.accepted();row=self.db.claim('worker');seen=[]
        with self.db.transaction() as c:
            c.execute("INSERT INTO docker_resources(intent_name,check_id,fence,kind,operation_state) VALUES ('element-test-foreign',%s,%s,'container','created')",(row['id'],row['fence']))
        def runner(args,**kwargs):
            seen.append(args)
            if args[0]=='ps':
                return subprocess.CompletedProcess(args,0,b'foreign-id\n',b'')
            if args[0]=='inspect':
                return subprocess.CompletedProcess(args,0,encoded([{'Config':{'Labels':{PREFIX+'.instance':'another-instance'}}}]),b'')
            return subprocess.CompletedProcess(args,0,b'',b'')
        control=DockerControl(self.db,row,runner=runner)
        try:
            self.assertFalse(control.cleanup())
            self.assertFalse(any(args[0]=='rm' for args in seen))
        finally:
            control.close()

    def test_health_and_api_responses_match_openapi(self):
        from copy import deepcopy
        import jsonschema
        spec=yaml.safe_load((ROOT/'docs/autocheck-openapi.yaml').read_text())
        def convert(value):
            if isinstance(value,list):
                return [convert(x) for x in value]
            if not isinstance(value,dict):
                return value
            value={k:convert(v) for k,v in value.items()}
            if value.pop('nullable',False):
                return {'anyOf':[value,{'type':'null'}]}
            return value
        components=convert(spec['components'])
        def validate(name,data):
            jsonschema.Draft7Validator({'$ref':'#/components/schemas/'+name,'components':components},format_checker=jsonschema.FormatChecker()).validate(data)
        validate('Health',self.client.get('/health/ready').json())
        missing=self.client.get('/missing')
        self.assertEqual(missing.headers['content-type'],'application/json')
        validate('ErrorResponse',missing.json())
        validate('Settings',self.client.get('/v1/admin/settings').json())
        validate('Capacity',self.client.get('/v1/capacity').json())
        metrics=self.client.get('/metrics')
        self.assertEqual(metrics.status_code,200)
        self.assertIn('element_autocheck_slots',metrics.text)
        r=self.accepted();validate('Reservation',r)
        validate('Check',self.client.get('/v1/checks/'+r['checkId']).json())
        validate('ChecksPage',self.client.get('/v1/checks',params={'submissionId':'sub-1'}).json())
        with self.db.transaction() as c:
            c.execute('DELETE FROM worker_presence')
        response=self.client.get('/health/ready')
        self.assertEqual(response.status_code,503);validate('Health',response.json())

    @unittest.skipUnless(os.environ.get('TEST_AUTOCHECK_ENGINE'),'Set TEST_AUTOCHECK_ENGINE=1 for real engine/Docker tests')
    def test_real_engine_rejected_dump_and_independent_sql_checks(self):
        r=self.accepted()
        worker=Worker(self.db,self.config)
        row=self.db.claim(worker.owner)
        worker.execute(row)
        self.assertEqual(self.db.get_check('lk',row['id'])['status'],'rejected')
        # Use real project and engine criteria from the repository, no mock scores.
        dump=(ROOT/'Dvizhok.xdump').read_bytes()
        source=yaml.safe_load((ROOT/'assignments/demo/assignment.yaml').read_text())
        body=yaml.safe_dump({'name':source['name'],'checks':source['checks'][:1]},allow_unicode=True).encode()
        r=self.accepted(manifest('real',dump,body),dump,body)
        row=self.db.claim(worker.owner);worker.execute(row)
        value=self.db.get_check('lk',row['id'])
        self.assertEqual(value['status'],'completed',value)
        self.assertIsNotNone(value['grading'])
        self.assertEqual(value['grading']['status'],'passed',value['grading'])
        self.assertEqual(self.db.capacity()['occupiedSlots'],0)


    @unittest.skipUnless(os.environ.get('TEST_AUTOCHECK_ENGINE'),'Set TEST_AUTOCHECK_ENGINE=1 for real engine/Docker tests')
    def test_pure_runtime_works_with_service_umask_and_filtered_environment(self):
        source=yaml.safe_load((ROOT/'assignments/poc-fio/assignment.yaml').read_text())
        body=yaml.safe_dump({'name':source['name'],'checks':source['checks'][:1]},allow_unicode=True).encode()
        dump=(ROOT/'Dvizhok.xdump').read_bytes()
        self.accepted(manifest('pure',dump,body),dump,body)
        worker=Worker(self.db,self.config);row=self.db.claim(worker.owner)
        original=subprocess.Popen;environments=[]
        def launch(args,**kwargs):
            if 'element_check_service.engine_child' in args:
                environments.append(kwargs['env'])
            return original(args,**kwargs)
        old_umask=os.umask(0o007)
        try:
            with patch.dict(os.environ,{'DB_PASSWORD_FILE':'/not-in-child','WEBHOOK_CLIENTS_FILE':'/not-in-child','API_CLIENTS_FILE':'/not-in-child','ELEMENT_TEST_RUNTIME_TIMEOUT':'30s'}),patch('element_check_service.worker.subprocess.Popen',side_effect=launch):
                worker.execute(row)
        finally:
            os.umask(old_umask)
        value=self.db.get_check('lk',row['id'])
        self.assertEqual(value['status'],'completed',value)
        self.assertEqual(value['grading']['status'],'passed',value['grading'])
        self.assertFalse(list((self.root/'checks'/str(row['id'])).rglob('*.html')))
        self.assertTrue(all(a['kind'].endswith('.json') for a in value['artifacts']))
        artifact=self.client.get('/v1/checks/'+str(row['id'])+'/artifacts/grading.json')
        self.assertEqual(artifact.headers['content-type'],'application/json')
        self.assertEqual(artifact.json(),value['grading'])
        html=self.client.get('/v1/checks/'+str(row['id'])+'/artifacts/report.html')
        self.assertEqual(html.status_code,404)
        self.assertEqual(html.headers['content-type'],'application/json')
        self.assertEqual(len(environments),1)
        self.assertEqual(environments[0]['ELEMENT_TEST_RUNTIME_TIMEOUT'], '30s')
        self.assertFalse({'DB_PASSWORD_FILE','WEBHOOK_CLIENTS_FILE','API_CLIENTS_FILE','ELEMENT_TEST_INTEGRATION_PASSWORD'}.intersection(environments[0]))

    @unittest.skipUnless(os.environ.get('TEST_AUTOCHECK_ENGINE'),'Set TEST_AUTOCHECK_ENGINE=1 for real engine/Docker tests')
    def test_whole_check_timeout_kills_real_child_and_releases_slot(self):
        dump=(ROOT/'Dvizhok.xdump').read_bytes()
        self.accepted(manifest('timeout',dump),dump)
        self.config.values['limits']['maxCheckWallSeconds']=0.01
        worker=Worker(self.db,self.config);row=self.db.claim(worker.owner)
        worker.execute(row)
        self.assertEqual(self.db.get_check('lk',row['id'])['error']['code'],'check_timeout')
        self.assertEqual(self.db.capacity()['occupiedSlots'],0)
        with self.db.transaction() as c:
            self.assertTrue(c.execute('SELECT supervisor_stopped FROM checks WHERE id=%s',(row['id'],)).fetchone()['supervisor_stopped'])

    @unittest.skipUnless(os.environ.get('TEST_AUTOCHECK_ENGINE'),'Set TEST_AUTOCHECK_ENGINE=1 for real engine/Docker tests')
    def test_two_configurations_sql_checks_run_concurrently_with_separate_databases(self):
        source = yaml.safe_load((ROOT/'assignments/poc-integration-environment/assignment.yaml').read_text())
        body = yaml.safe_dump({'name':source['name'],'checks':source['checks'][:1]},allow_unicode=True).encode()
        password = self.root/'sql-password'
        password.write_text('test-only-'+uuid.uuid4().hex)
        worker = Worker(self.db,self.config)
        rows = []
        for i,name in enumerate(('Dvizhok.xdump','autocheck-2026-09-24-16-14.xdump')):
            dump=(ROOT/name).read_bytes()
            m=manifest('sql-'+str(i),dump,body);m['runMode']='integration'
            self.accepted(m,dump,body)
            rows.append(self.db.claim(worker.owner))
        with patch.dict(os.environ,{'INTEGRATION_PASSWORD_FILE':str(password)}):
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(worker.execute,rows))
        for row in rows:
            value=self.db.get_check('lk',row['id'])
            self.assertEqual(value['status'],'completed',value)
            self.assertEqual(value['grading']['status'],'passed',value['grading'])
            self.assertEqual(value['grading']['feedback'][0]['actual']['initialCount'],0)
            with self.db.transaction() as c:
                resources=c.execute('SELECT * FROM docker_resources WHERE check_id=%s',(row['id'],)).fetchall()
            self.assertEqual(len(resources),3)
            self.assertTrue(all(r['deleted_at'] for r in resources))
        self.assertEqual(self.db.capacity()['occupiedSlots'],0)


if __name__=='__main__':
    unittest.main()
