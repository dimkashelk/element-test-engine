"""Short PostgreSQL transactions. The singleton serializes capacity mutations."""
from contextlib import contextmanager
import os
from pathlib import Path
import uuid
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from . import SCHEMA_REVISION
from .common import ServiceError, fingerprint, stamp, encoded
from .config import secret


class Database:
    def __init__(self, config, dsn=None):
        self.config, self.dsn = config, dsn

    def connect(self):
        if self.dsn:
            return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=3)
        return psycopg.connect(host=os.environ.get('DB_HOST', 'postgres'),
            port=os.environ.get('DB_PORT', '5432'), dbname=os.environ.get('DB_NAME', 'autocheck'),
            user=os.environ.get('DB_USER', 'autocheck'), password=secret('DB_PASSWORD'),
            row_factory=dict_row, connect_timeout=3, options='-c statement_timeout=5000 -c lock_timeout=3000')

    @contextmanager
    def transaction(self, *, lock=False):
        with self.connect() as connection:
            if lock:
                connection.execute('SELECT id FROM service_settings WHERE id=1 FOR UPDATE').fetchone()
            yield connection

    def migrate(self):
        with self.connect() as c:
            c.execute('SELECT pg_advisory_xact_lock(37281015)')
            exists = c.execute("SELECT to_regclass('service_instance') AS t").fetchone()['t']
            if exists:
                row = c.execute('SELECT schema_revision FROM service_instance WHERE id=1').fetchone()
                if row and row['schema_revision'] != SCHEMA_REVISION:
                    raise ValueError('Unsupported schema revision; explicit upgrade required')
            c.execute((Path(__file__).parent / 'migrations/001.sql').read_text())
            c.execute('INSERT INTO service_instance VALUES (1,%s,%s) ON CONFLICT DO NOTHING', (uuid.uuid4(), SCHEMA_REVISION))
            c.execute('INSERT INTO service_settings(id,max_concurrent_checks) VALUES (1,%s) ON CONFLICT DO NOTHING', (self.config.default_cap,))

    def expire_reserved(self, c):
        c.execute("UPDATE reservations SET state='expired',fence=fence+1 WHERE state='reserved' AND expires_at<=clock_timestamp()")
        c.execute("UPDATE capacity_claims cl SET released_at=clock_timestamp() FROM reservations r WHERE cl.reservation_id=r.id AND cl.released_at IS NULL AND r.state IN ('expired','cancelled')")

    def dependencies(self, c, client=None):
        seconds = self.config.get('admission', 'workerStaleAfterSeconds')
        workers = c.execute("SELECT role,hard_limit FROM worker_presence WHERE ready AND heartbeat_at>clock_timestamp()-(%s * interval '1 second')", (seconds,)).fetchall()
        if not any(w['role']=='worker' and w['hard_limit']==self.config.hard_limit for w in workers) or not any(w['role']=='notifier' for w in workers):
            raise ServiceError(503, 'admission_unavailable')
        clients = c.execute("SELECT client_id FROM delivery_clients WHERE refreshed_at>clock_timestamp()-(%s * interval '1 second')", (seconds,)).fetchall()
        if not clients or (client and client not in {x['client_id'] for x in clients}):
            raise ServiceError(503, 'admission_unavailable')
        limits = self.config.values['limits']
        backlog = c.execute("SELECT count(*) FILTER (WHERE state='pending') AS n,COALESCE(sum(octet_length(payload)) FILTER (WHERE state<>'delivered'),0) AS size FROM outbox").fetchone()
        if backlog['n'] >= limits['maxPendingWebhookEvents'] or backlog['size'] >= limits['maxUndeliveredResultBytes']:
            raise ServiceError(503, 'admission_unavailable')
        if c.execute("SELECT 1 FROM checks WHERE cleanup_blocked LIMIT 1").fetchone():
            raise ServiceError(503, 'admission_unavailable')
        revision = c.execute('SELECT schema_revision FROM service_instance WHERE id=1').fetchone()
        if not revision or revision['schema_revision'] != SCHEMA_REVISION:
            raise ServiceError(503, 'admission_unavailable')

    def settings(self, changes=None, etag=None, actor=None):
        with self.transaction(lock=changes is not None) as c:
            row = c.execute('SELECT * FROM service_settings WHERE id=1').fetchone()
            if changes is not None:
                if etag is None:
                    raise ServiceError(428, 'precondition_required')
                if etag != '"'+str(row['revision'])+'"':
                    raise ServiceError(412, 'settings_conflict')
                row = c.execute('UPDATE service_settings SET max_concurrent_checks=%s,accepting=%s,revision=revision+1,updated_at=clock_timestamp(),updated_by=%s WHERE id=1 RETURNING *',
                    (changes.get('maxConcurrentChecks', row['max_concurrent_checks']), changes.get('accepting', row['accepting']), actor)).fetchone()
                c.execute("INSERT INTO audit_log(event,actor) VALUES ('settings.changed',%s)", (actor,))
            return {'maxConcurrentChecks': row['max_concurrent_checks'], 'accepting': row['accepting'], 'revision': row['revision'], 'updatedAt': stamp(row['updated_at'])}

    def reserve(self, client, key, manifest):
        identity, digest = client['clientId'], fingerprint(manifest)
        with self.transaction(lock=True) as c:
            self.expire_reserved(c)
            previous = c.execute("SELECT * FROM idempotency WHERE client_id=%s AND operation='reserve' AND key=%s", (identity,key)).fetchone()
            if previous:
                if previous['fingerprint'] != digest:
                    raise ServiceError(409, 'idempotency_conflict')
                r = c.execute('SELECT * FROM reservations WHERE id=%s', (previous['resource_id'],)).fetchone()
                if not r or r['state'] in ('expired','cancelled'):
                    raise ServiceError(410, 'reservation_expired')
                return reservation(r), 200
            check = c.execute('SELECT id FROM checks WHERE client_id=%s AND submission_id=%s AND attempt=%s', (identity,manifest['submissionId'],manifest['attempt'])).fetchone()
            if check:
                raise ServiceError(409, 'attempt_exists', existingCheckId=str(check['id']))
            live = c.execute("SELECT id FROM reservations WHERE client_id=%s AND submission_id=%s AND attempt=%s AND state IN ('reserved','uploading')", (identity,manifest['submissionId'],manifest['attempt'])).fetchone()
            if live:
                raise ServiceError(409, 'attempt_exists')
            self.dependencies(c, identity)
            settings = c.execute('SELECT * FROM service_settings WHERE id=1').fetchone()
            if not settings['accepting'] or settings['max_concurrent_checks']>self.config.hard_limit:
                raise ServiceError(503, 'admission_unavailable')
            occupied = c.execute('SELECT count(*) AS n FROM capacity_claims WHERE released_at IS NULL').fetchone()['n']
            if occupied >= settings['max_concurrent_checks']:
                raise ServiceError(429, 'capacity_full')
            rid, cid = uuid.uuid4(), uuid.uuid4()
            row = c.execute("INSERT INTO reservations(id,check_id,client_id,submission_id,attempt,manifest,state,expires_at) VALUES (%s,%s,%s,%s,%s,%s,'reserved',clock_timestamp()+(%s * interval '1 second')) RETURNING *",
                (rid,cid,identity,manifest['submissionId'],manifest['attempt'],Jsonb(manifest),self.config.get('admission','reservationTtlSeconds'))).fetchone()
            c.execute('INSERT INTO capacity_claims(id,reservation_id) VALUES (%s,%s)', (uuid.uuid4(),rid))
            c.execute("INSERT INTO idempotency VALUES (%s,'reserve',%s,%s,%s,clock_timestamp()+(%s * interval '1 day'))", (identity,key,digest,rid,self.config.get('retention','idempotencyDays')))
            return reservation(row), 201

    def get_reservation(self, client, rid, cancel=False):
        with self.transaction(lock=True) as c:
            self.expire_reserved(c)
            row = c.execute('SELECT * FROM reservations WHERE id=%s AND client_id=%s', (rid,client)).fetchone()
            if not row:
                raise ServiceError(404, 'not_found')
            if cancel:
                if row['state'] in ('uploading','consumed'):
                    raise ServiceError(409, 'reservation_busy')
                c.execute("UPDATE reservations SET state='cancelled',fence=fence+1 WHERE id=%s AND state='reserved'", (rid,))
                self.expire_reserved(c)
            return reservation(row)

    def begin_upload(self, client, rid, key):
        with self.transaction(lock=True) as c:
            self.expire_reserved(c)
            row = c.execute('SELECT * FROM reservations WHERE id=%s AND client_id=%s', (rid,client)).fetchone()
            if not row:
                raise ServiceError(404, 'not_found')
            previous = c.execute("SELECT * FROM idempotency WHERE client_id=%s AND operation='upload' AND key=%s", (client,key)).fetchone()
            if previous:
                if previous['fingerprint'] != fingerprint(row['manifest']):
                    raise ServiceError(409, 'idempotency_conflict')
                return row, accepted(previous['resource_id'])
            if c.execute("SELECT 1 FROM reservations WHERE client_id=%s AND upload_key=%s AND state='uploading'", (client,key)).fetchone():
                raise ServiceError(409,'upload_in_progress')
            if row['state']=='uploading':
                raise ServiceError(409,'upload_in_progress')
            if row['state'] in ('expired','cancelled'):
                raise ServiceError(410,'reservation_expired')
            if row['state']!='reserved':
                raise ServiceError(409,'reservation_busy')
            self.dependencies(c, client)
            row = c.execute("UPDATE reservations SET state='uploading',upload_key=%s,fence=fence+1,heartbeat_at=clock_timestamp(),upload_deadline=clock_timestamp()+(%s * interval '1 second') WHERE id=%s RETURNING *",
                (key,self.config.get('admission','uploadMaxDurationSeconds'),rid)).fetchone()
            return row, None

    def upload_heartbeat(self, rid, fence):
        with self.transaction() as c:
            if not c.execute("UPDATE reservations SET heartbeat_at=clock_timestamp() WHERE id=%s AND fence=%s AND state='uploading' AND upload_deadline>clock_timestamp() RETURNING id", (rid,fence)).fetchone():
                raise ServiceError(409,'upload_failed')

    def abort_upload(self, rid, fence):
        with self.transaction(lock=True) as c:
            c.execute("UPDATE reservations SET state='cancelled',fence=fence+1 WHERE id=%s AND fence=%s AND state='uploading'", (rid,fence))
            self.expire_reserved(c)

    def commit_upload(self, row, key):
        with self.transaction(lock=True) as c:
            r = c.execute("SELECT * FROM reservations WHERE id=%s AND fence=%s AND state='uploading' AND upload_deadline>clock_timestamp() FOR UPDATE", (row['id'],row['fence'])).fetchone()
            if not r:
                raise ServiceError(409,'upload_failed')
            c.execute("INSERT INTO checks(id,reservation_id,client_id,submission_id,attempt,manifest,state) VALUES (%s,%s,%s,%s,%s,%s,'accepted')", (r['check_id'],r['id'],r['client_id'],r['submission_id'],r['attempt'],Jsonb(r['manifest'])))
            c.execute("UPDATE reservations SET state='consumed' WHERE id=%s", (r['id'],))
            c.execute('UPDATE capacity_claims SET check_id=%s WHERE reservation_id=%s', (r['check_id'],r['id']))
            c.execute("INSERT INTO idempotency VALUES (%s,'upload',%s,%s,%s,clock_timestamp()+(%s * interval '1 day'))", (r['client_id'],key,fingerprint(r['manifest']),r['check_id'],self.config.get('retention','idempotencyDays')))
            return accepted(r['check_id'])

    def get_check(self, client, cid):
        with self.transaction() as c:
            row = c.execute('SELECT * FROM checks WHERE id=%s AND client_id=%s', (cid,client)).fetchone()
            if not row:
                raise ServiceError(404,'not_found')
            value = snapshot(row)
            delivery = c.execute('SELECT * FROM outbox WHERE check_id=%s', (cid,)).fetchone()
            value['delivery'] = {'status': delivery['state'] if delivery else 'not_created', 'attemptCount': delivery['attempt_count'] if delivery else 0,
                'nextAttemptAt': stamp(delivery['next_attempt_at']) if delivery and delivery['state']=='pending' else None,
                'lastErrorCode': delivery['last_error'] if delivery else None}
            value['artifacts'] = [{'kind':a['kind'], 'available': a['deleted_at'] is None and a['expires_at']>c.execute('SELECT clock_timestamp() AS t').fetchone()['t'],
                'url':f'/v1/checks/{cid}/artifacts/{a["kind"]}', 'expiresAt':stamp(a['expires_at'])} for a in c.execute('SELECT * FROM artifacts WHERE check_id=%s ORDER BY kind',(cid,)).fetchall()]
            return value

    def capacity(self):
        with self.transaction() as c:
            settings = c.execute('SELECT * FROM service_settings WHERE id=1').fetchone()
            rows = c.execute("SELECT COALESCE(ch.state,r.state) AS state, count(*) AS n FROM capacity_claims cl JOIN reservations r ON r.id=cl.reservation_id LEFT JOIN checks ch ON ch.id=cl.check_id WHERE cl.released_at IS NULL GROUP BY COALESCE(ch.state,r.state)").fetchall()
            counts = {r['state']:r['n'] for r in rows}
            accepting = settings['accepting'] and settings['max_concurrent_checks']<=self.config.hard_limit
            try:
                self.dependencies(c)
            except ServiceError:
                accepting = False
            occupied = sum(counts.values())
            return {'maxConcurrentChecks':settings['max_concurrent_checks'],'hardLimit':self.config.hard_limit,
                'occupiedSlots':occupied,'availableSlots':max(0,settings['max_concurrent_checks']-occupied) if accepting else 0,
                **{k:counts.get(k,0) for k in ('reserved','uploading','accepted','running','finalizing')},
                'cleanupBlocked':c.execute('SELECT count(*) AS n FROM checks WHERE cleanup_blocked').fetchone()['n'],
                'accepting':accepting,'settingsRevision':settings['revision'],'observedAt':stamp(c.execute('SELECT clock_timestamp() AS t').fetchone()['t'])}

    def presence(self, owner, role, ready, reason=None):
        with self.transaction() as c:
            c.execute('INSERT INTO worker_presence(worker_id,role,ready,hard_limit,reason) VALUES (%s,%s,%s,%s,%s) ON CONFLICT(worker_id) DO UPDATE SET heartbeat_at=clock_timestamp(),ready=EXCLUDED.ready,hard_limit=EXCLUDED.hard_limit,reason=EXCLUDED.reason', (owner,role,ready,self.config.hard_limit,reason))

    def claim(self, owner, container=None):
        with self.transaction() as c:
            row = c.execute("SELECT * FROM checks WHERE state='accepted' ORDER BY accepted_at FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
            if not row:
                return None
            return c.execute("UPDATE checks SET state='running',started_at=clock_timestamp(),lease_owner=%s,lease_until=clock_timestamp()+(%s * interval '1 second'),fence=fence+1,supervisor_container=%s WHERE id=%s RETURNING *", (owner,self.config.get('execution','checkLeaseSeconds'),container,row['id'])).fetchone()

    def heartbeat(self, cid, owner, fence):
        with self.transaction() as c:
            return bool(c.execute("UPDATE checks SET lease_until=clock_timestamp()+(%s * interval '1 second') WHERE id=%s AND lease_owner=%s AND fence=%s AND state='running' AND lease_until>clock_timestamp() RETURNING id", (self.config.get('execution','checkLeaseSeconds'),cid,owner,fence)).fetchone())

    def finalize(self, cid, fence, outcome, artifacts):
        with self.transaction(lock=True) as c:
            row = c.execute("SELECT * FROM checks WHERE id=%s AND fence=%s AND state='finalizing' FOR UPDATE", (cid,fence)).fetchone()
            if not row:
                return False
            row = c.execute("UPDATE checks SET state=%s,grading=%s,error=%s,engine=%s,normalized_hash=%s,finished_at=clock_timestamp(),cleanup_blocked=false,lease_until=NULL WHERE id=%s RETURNING *",
                (outcome['status'],Jsonb(outcome.get('grading')),Jsonb(outcome.get('error')),Jsonb(outcome.get('engine')),outcome.get('normalizedCriteriaSha256'),cid)).fetchone()
            for artifact in artifacts:
                c.execute("INSERT INTO artifacts VALUES (%s,%s,%s,%s,%s,clock_timestamp()+(%s * interval '1 day'),NULL)",
                    (cid,artifact['kind'],artifact['relative_path'],artifact['size'],artifact['sha256'],self.config.get('retention','artifactsDays')))
            event_id = uuid.uuid4()
            payload = encoded({'schemaVersion':'1.0','eventId':str(event_id),'type':'check.finished','occurredAt':stamp(row['finished_at']),'check':snapshot(row)})
            c.execute("INSERT INTO outbox(event_id,check_id,client_id,payload,state,deadline) VALUES (%s,%s,%s,%s,'pending',clock_timestamp()+(%s * interval '1 second'))", (event_id,cid,row['client_id'],payload,self.config.get('delivery','retryWindowSeconds')))
            c.execute('UPDATE capacity_claims SET released_at=clock_timestamp() WHERE check_id=%s AND released_at IS NULL', (cid,))
            c.execute("INSERT INTO audit_log(event,resource_id) VALUES ('check.finished',%s)",(str(cid),))
            return True

    def retry_delivery(self, cid):
        with self.transaction() as c:
            row = c.execute('SELECT * FROM outbox WHERE check_id=%s FOR UPDATE', (cid,)).fetchone()
            if not row:
                if not c.execute('SELECT 1 FROM checks WHERE id=%s',(cid,)).fetchone():
                    raise ServiceError(404,'not_found')
                raise ServiceError(409,'invalid_request')
            if row['payload'] is None:
                raise ServiceError(410,'event_expired')
            if row['state'] in ('failed','expired'):
                c.execute("UPDATE outbox SET state='pending',deadline=clock_timestamp()+(%s * interval '1 second'),next_attempt_at=clock_timestamp(),lease_until=NULL,fence=fence+1,last_error=NULL WHERE check_id=%s", (self.config.get('delivery','retryWindowSeconds'),cid))
            return {'checkId':str(cid),'eventId':str(row['event_id']),'status':'pending' if row['state'] in ('failed','expired') else row['state']}


def reservation(row):
    return {'reservationId':str(row['id']),'checkId':str(row['check_id']),'status':row['state'],
        'expiresAt':stamp(row['expires_at']),'uploadDeadlineAt':stamp(row['upload_deadline']),'uploadUrl':'/v1/checks'}


def accepted(cid):
    return {'checkId':str(cid),'status':'accepted','statusUrl':f'/v1/checks/{cid}'}


def snapshot(row):
    m = row['manifest']
    identifiers = {key: m[key] for key in ('submissionId', 'attempt', 'studentId', 'assignmentId')}
    if 'criteriaVersion' in m:
        identifiers['criteriaVersion'] = m['criteriaVersion']
    return {'schemaVersion':'1.0','checkId':str(row['id']), **identifiers,
        'status':row['state'],'acceptedAt':stamp(row['accepted_at']),'startedAt':stamp(row['started_at']),'finishedAt':stamp(row['finished_at']),
        'inputHashes':{'dumpSha256':m['dump']['sha256'],'criteriaSha256':m['criteria']['sha256']},
        'engine':row['engine'],'normalizedCriteriaSha256':row['normalized_hash'],'grading':row['grading'],'error':row['error']}
