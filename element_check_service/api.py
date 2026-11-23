import json
import os
import re
import shutil
import uuid
import jsonschema
import psycopg
import yaml
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, FileResponse, Response
from starlette.routing import Route
from starlette.middleware import Middleware
from starlette.exceptions import HTTPException
from .observability import TransportLog, COUNTERS, LOCK
from . import ARTIFACTS
from .auth import Registry, IDENTIFIER
from .common import ServiceError
from .config import Config, ROOT
from .db import Database
from .uploads import receive_upload
from .webhooks import webhook_url, UnsafeWebhook


def identifier(value):
    try:
        return uuid.UUID(value)
    except (ValueError,TypeError,AttributeError):
        raise ServiceError(400,'invalid_request') from None


def key(request):
    value = request.headers.get('idempotency-key','')
    if not re.fullmatch(r'[\x20-\x7e]{1,128}',value):
        raise ServiceError(400,'invalid_request')
    return value


async def small_json(request, maximum=16384):
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content)>maximum:
            raise ServiceError(413,'payload_too_large')
    try:
        def pairs(items):
            result = {}
            for k,v in items:
                if k in result:
                    raise ValueError('Duplicate JSON key')
                result[k] = v
            return result
        data = json.loads(content,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(data,dict):
            raise ValueError()
        return data
    except (ValueError,UnicodeError):
        raise ServiceError(400,'invalid_request') from None


def create_app(config=None, db=None, registry=None):
    config = config or Config()
    config.prepare()
    db = db or Database(config)
    registry = registry or Registry(os.environ['API_CLIENTS_FILE'])
    contract = yaml.safe_load((ROOT / 'docs/autocheck-openapi.yaml').read_text())
    validator = jsonschema.Draft7Validator({'$ref':'#/components/schemas/ReservationRequest','components':contract['components']})

    def authorize(request, scope):
        return registry.authorize(request.headers.get('authorization'),scope)

    def disk_ready():
        if shutil.disk_usage(config.data).free < config.get('limits','minFreeDiskBytes'):
            raise ServiceError(503,'admission_unavailable')

    async def health(request):
        if request.url.path.endswith('/live'):
            return JSONResponse({'status':'ok'})
        def check():
            disk_ready()
            with db.transaction() as c:
                db.dependencies(c)
            probe = config.data / 'staging' / (str(uuid.uuid4())+'.probe')
            with probe.open('xb') as f:
                f.write(b'ok')
            probe.unlink()
        await run_in_threadpool(check)
        return JSONResponse({'status':'ok'})

    async def reservations(request):
        client = authorize(request,'checks:write')
        idem = key(request)
        manifest = await small_json(request)
        if list(validator.iter_errors(manifest)):
            raise ServiceError(422,'invalid_request')
        if type(manifest['attempt']) is not int or manifest['attempt']>9223372036854775807:
            raise ServiceError(422,'invalid_request')
        if 'webhookUrl' in manifest:
            try:
                webhook_url(manifest['webhookUrl'])
            except UnsafeWebhook:
                raise ServiceError(422,'invalid_request') from None
        manifest.setdefault('runMode','isolated')
        if manifest['runMode'] not in client['allowedRunModes'] or manifest['runMode'] not in config.get('execution','allowedRunModes'):
            raise ServiceError(403,'forbidden')
        for name,limit in (('dump','maxDumpBytes'),('criteria','maxCriteriaBytes')):
            if manifest[name]['sizeBytes']>config.get('limits',limit):
                raise ServiceError(413,'payload_too_large')
        await run_in_threadpool(disk_ready)
        value,status = await run_in_threadpool(db.reserve,client,idem,manifest)
        return JSONResponse(value,status_code=status)

    async def reservation(request):
        client = authorize(request,'checks:write' if request.method=='DELETE' else 'checks:read')
        rid = identifier(request.path_params['rid'])
        value = await run_in_threadpool(db.get_reservation,client['clientId'],rid,request.method=='DELETE')
        return Response(status_code=204) if request.method=='DELETE' else JSONResponse(value)

    async def checks(request):
        client = authorize(request,'checks:write' if request.method=='POST' else 'checks:read')
        if request.method=='POST':
            idem = key(request)
            rid = identifier(request.headers.get('x-reservation-id'))
            row,replay = await run_in_threadpool(db.begin_upload,client['clientId'],rid,idem)
            if replay is not None:
                return JSONResponse(replay,status_code=202)
            return JSONResponse(await receive_upload(request,db,config,row,idem),status_code=202)
        query = request.query_params
        submission = query.get('submissionId','')
        if not IDENTIFIER.fullmatch(submission):
            raise ServiceError(400,'invalid_request')
        try:
            limit = int(query.get('limit','20'))
            attempt = int(query['attempt']) if 'attempt' in query else None
            cursor = str(identifier(query['cursor'])) if 'cursor' in query else None
            if not 1<=limit<=100 or (attempt is not None and attempt<1):
                raise ValueError()
        except ValueError:
            raise ServiceError(400,'invalid_request') from None
        def page():
            with db.transaction() as c:
                ids = c.execute('SELECT id FROM checks WHERE client_id=%s AND submission_id=%s AND (%s::bigint IS NULL OR attempt=%s) AND (%s::uuid IS NULL OR id>%s::uuid) ORDER BY id LIMIT %s',
                    (client['clientId'],submission,attempt,attempt,cursor,cursor,limit+1)).fetchall()
            return {'items':[db.get_check(client['clientId'],r['id']) for r in ids[:limit]],'nextCursor':str(ids[limit-1]['id']) if len(ids)>limit else None}
        return JSONResponse(await run_in_threadpool(page))

    async def check(request):
        client = authorize(request,'checks:read')
        return JSONResponse(await run_in_threadpool(db.get_check,client['clientId'],identifier(request.path_params['cid'])))

    async def artifact(request):
        client = authorize(request,'checks:read')
        cid,kind = identifier(request.path_params['cid']),request.path_params['kind']
        if kind not in ARTIFACTS:
            raise ServiceError(404,'artifact_not_found')
        def locate():
            db.get_check(client['clientId'],cid)
            with db.transaction() as c:
                row = c.execute('SELECT *,expires_at<=clock_timestamp() AS expired FROM artifacts WHERE check_id=%s AND kind=%s',(cid,kind)).fetchone()
            if row is None:
                raise ServiceError(404,'artifact_not_found')
            if row['deleted_at'] or row['expired']:
                raise ServiceError(410,'artifact_expired')
            path = config.data / row['relative_path']
            if path.is_symlink() or not path.resolve().is_relative_to((config.data/'checks'/str(cid)).resolve()) or not path.is_file():
                raise ServiceError(404,'artifact_not_found')
            return path
        path = await run_in_threadpool(locate)
        return FileResponse(path,filename=kind,media_type='application/json',
            headers={'X-Content-Type-Options':'nosniff'})

    async def capacity(request):
        authorize(request,'checks:read')
        value = await run_in_threadpool(db.capacity)
        try:
            disk_ready()
        except ServiceError:
            value.update(accepting=False,availableSlots=0)
        return JSONResponse(value)

    async def settings(request):
        client = authorize(request,'settings:write' if request.method=='PATCH' else 'settings:read')
        changes = None
        if request.method=='PATCH':
            changes = await small_json(request)
            if not changes or set(changes)-{'maxConcurrentChecks','accepting'} or ('accepting' in changes and type(changes['accepting']) is not bool) or ('maxConcurrentChecks' in changes and (type(changes['maxConcurrentChecks']) is not int or not 1<=changes['maxConcurrentChecks']<=config.hard_limit)):
                raise ServiceError(422,'invalid_settings')
        value = await run_in_threadpool(db.settings,changes,request.headers.get('if-match'),client['clientId'])
        value['hardLimit'] = config.hard_limit
        return JSONResponse(value,headers={'ETag':'"'+str(value['revision'])+'"'})

    async def retry(request):
        authorize(request,'delivery:retry')
        value = await run_in_threadpool(db.retry_delivery,identifier(request.path_params['cid']))
        return JSONResponse(value,status_code=202)

    async def metrics(request):
        authorize(request,'settings:read')
        value = await run_in_threadpool(db.capacity)
        with LOCK:
            counters = list(COUNTERS.items())
        lines = []
        for (method,route,status),(count,seconds) in counters:
            labels = f'method="{method}",route="{route}",status="{status}"'
            lines += [f'element_autocheck_http_requests_total{{{labels}}} {count}',f'element_autocheck_http_duration_seconds_sum{{{labels}}} {seconds}']
        lines += [f'element_autocheck_slots{{state="{k}"}} {value[k]}' for k in ('reserved','uploading','accepted','running','finalizing')]
        lines += [
            f'element_autocheck_slot_limit {value["maxConcurrentChecks"]}',
            f'element_autocheck_admission_open {int(value["accepting"])}',
        ]
        lines += [f'element_autocheck_cleanup_blocked {value["cleanupBlocked"]}',f'element_autocheck_disk_free_bytes {shutil.disk_usage(config.data).free}']
        def extra():
            with db.transaction() as c:
                presence = c.execute(
                    "SELECT role, count(*) AS n FROM worker_presence "
                    "WHERE ready AND heartbeat_at>clock_timestamp()-(%s * interval '1 second') "
                    "AND (role<>'worker' OR hard_limit=%s) GROUP BY role",
                    (config.get('admission', 'workerStaleAfterSeconds'), config.hard_limit),
                ).fetchall()
                deliveries=c.execute('SELECT state,count(*) AS n,COALESCE(sum(attempt_count),0) AS attempts FROM outbox GROUP BY state').fetchall()
                timings=c.execute("SELECT state,count(*) AS n,COALESCE(avg(extract(epoch FROM started_at-accepted_at)),0)::float AS start_delay,COALESCE(avg(extract(epoch FROM finished_at-accepted_at)),0)::float AS duration FROM checks GROUP BY state").fetchall()
                oldest=c.execute("SELECT COALESCE(max(extract(epoch FROM clock_timestamp()-created_at)),0)::float AS age FROM outbox WHERE state='pending'").fetchone()['age']
                return presence,deliveries,timings,oldest
        presence,deliveries,timings,oldest=await run_in_threadpool(extra)
        ready_processes = {row['role']: row['n'] for row in presence}
        lines += [
            f'element_autocheck_ready_processes{{role="{role}"}} {ready_processes.get(role, 0)}'
            for role in ('worker', 'notifier')
        ]
        lines.append(f'element_autocheck_oldest_pending_delivery_seconds {oldest}')
        for row in timings:
            lines += [f'element_autocheck_checks{{state="{row["state"]}"}} {row["n"]}',f'element_autocheck_check_duration_seconds_avg{{state="{row["state"]}"}} {row["duration"]}',f'element_autocheck_check_start_delay_seconds_avg{{state="{row["state"]}"}} {row["start_delay"]}']
        for row in deliveries:
            lines += [f'element_autocheck_delivery{{state="{row["state"]}"}} {row["n"]}',f'element_autocheck_delivery_attempts{{state="{row["state"]}"}} {row["attempts"]}']
        return Response('\n'.join(lines)+'\n',media_type='text/plain')

    async def error(request, exc):
        if request.url.path.startswith('/health/'):
            return JSONResponse({'status':'unavailable'},status_code=503)
        request_id = getattr(request.state,'request_id',str(uuid.uuid4()))
        if isinstance(exc,HTTPException):
            status,code,message,details=exc.status_code,('not_found' if exc.status_code==404 else 'invalid_request'),'Invalid request',{}
        elif isinstance(exc,ServiceError):
            status,code,message,details = exc.status,exc.code,exc.message,exc.details
        else:
            status,code,message,details = 503,'dependency_unavailable','Dependency unavailable',{}
        headers = {'X-Request-Id':request_id}
        if status in (429,503) or code=='upload_in_progress':
            headers['Retry-After'] = str(config.get('admission','retryAfterSeconds'))
        return JSONResponse({'error':{'code':code,'message':message,'requestId':request_id,**details}},status_code=status,headers=headers)

    app = Starlette(routes=[Route('/health/live',health),Route('/health/ready',health),Route('/metrics',metrics),
        Route('/v1/reservations',reservations,methods=['POST']),Route('/v1/reservations/{rid}',reservation,methods=['GET','DELETE']),
        Route('/v1/checks',checks,methods=['GET','POST']),Route('/v1/checks/{cid}',check),Route('/v1/checks/{cid}/artifacts/{kind}',artifact),
        Route('/v1/capacity',capacity),Route('/v1/admin/settings',settings,methods=['GET','PATCH']),Route('/v1/admin/checks/{cid}/delivery/retry',retry,methods=['POST'])],
        middleware=[Middleware(TransportLog)],exception_handlers={HTTPException:error,ServiceError:error,psycopg.Error:error,OSError:error})
    app.state.db, app.state.config = db,config
    return app
