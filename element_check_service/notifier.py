"""Durable at-least-once webhook delivery, signing the stored bytes verbatim."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from email.utils import parsedate_to_datetime
import hashlib
import hmac
import os
import random
import threading
import time
import uuid
import httpx
from .auth import Registry
from .common import now
from .observability import event
from .webhooks import pinned_destination, UnsafeWebhook


def signed_headers(payload, event, timestamp, key_id, secret):
    prefix = (str(timestamp)+'.'+str(event)+'.').encode()
    signature = hmac.new(secret.encode(),prefix+payload,hashlib.sha256).hexdigest()
    return {'Content-Type':'application/json','X-Autocheck-Event-Id':str(event),'X-Autocheck-Timestamp':str(timestamp),
        'X-Autocheck-Key-Id':key_id,'X-Autocheck-Signature':'v1='+signature}


def retry_after(value):
    try:
        return max(0,float(value))
    except (TypeError,ValueError):
        try:
            return max(0,(parsedate_to_datetime(value)-now()).total_seconds())
        except (TypeError,ValueError,OverflowError):
            return 0


class Notifier:
    def __init__(self, db, config, registry=None, transport=None):
        self.db,self.config = db,config
        self.registry = registry or Registry(os.environ['WEBHOOK_CLIENTS_FILE'],webhook=True)
        self.owner,self.stop = str(uuid.uuid4()),threading.Event()
        self.transport = transport

    def sync(self):
        self.registry.reload()
        with self.db.transaction() as c:
            c.execute('DELETE FROM delivery_clients')
            for client in self.registry.clients:
                c.execute('INSERT INTO delivery_clients(client_id) VALUES (%s)',(client,))
        self.db.presence(self.owner,'notifier',True)

    def claim(self):
        if not self.config.values['delivery'].get('enabled', True):
            return None  # Keep the durable outbox intact during local-only testing.
        with self.db.transaction() as c:
            c.execute("UPDATE outbox SET state='expired',last_error='retry_window_expired' WHERE state='pending' AND deadline<=clock_timestamp() AND (lease_until IS NULL OR lease_until<=clock_timestamp())")
            row = c.execute("SELECT * FROM outbox WHERE state='pending' AND payload IS NOT NULL AND next_attempt_at<=clock_timestamp() AND deadline>clock_timestamp() AND (lease_until IS NULL OR lease_until<=clock_timestamp()) ORDER BY next_attempt_at FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
            if row is None:
                return None
            claimed = c.execute("UPDATE outbox SET lease_owner=%s,lease_until=clock_timestamp()+interval '30 seconds',fence=fence+1,attempt_count=attempt_count+1 WHERE event_id=%s RETURNING *", (self.owner,row['event_id'])).fetchone()
            claimed['webhook_url'] = c.execute("SELECT manifest->>'webhookUrl' AS url FROM checks WHERE id=%s", (row['check_id'],)).fetchone()['url']
            return claimed

    def send(self, row):
        self.registry.reload()
        client = self.registry.clients.get(row['client_id'])
        state,error,delay = 'pending',None,0
        if client is None:
            state,error = 'failed','delivery_client_missing'
        elif not (row.get('webhook_url') or client.get('url')):
            state,error = 'failed','delivery_url_missing'
        else:
            headers = signed_headers(bytes(row['payload']),row['event_id'],int(time.time()),client['currentKeyId'],client['keys'][client['currentKeyId']])
            config = self.config.values['delivery']
            try:
                destination, host, hostname = pinned_destination(row.get('webhook_url') or client['url'])
                headers['Host'] = host
                timeout = httpx.Timeout(config['requestTimeoutSeconds'],connect=config['connectTimeoutSeconds'])
                with httpx.Client(transport=self.transport,timeout=timeout,follow_redirects=False,trust_env=False) as http:
                    started = time.monotonic()
                    with http.stream('POST',destination,content=bytes(row['payload']),headers=headers,
                                     extensions={'sni_hostname': hostname}) as response:
                        code = response.status_code
                        delay = min(config['retryWindowSeconds'],retry_after(response.headers.get('retry-after')))
                        size = 0
                        for chunk in (() if response.is_stream_consumed else response.iter_raw()):
                            size += len(chunk)
                            if size>65536 or time.monotonic()-started>config['requestTimeoutSeconds']:
                                break
                        if 200<=code<300:
                            state = 'delivered'
                        elif code in (408,429) or 500<=code<600:
                            error = 'callback_temporary_failure'
                        else:
                            state,error = 'failed','callback_http_rejected'
            except UnsafeWebhook:
                state,error = 'failed','callback_url_forbidden'
            except (httpx.HTTPError,OSError):
                error = 'callback_unavailable'
        if state=='pending':
            base = self.config.get('delivery','retryBaseSeconds')
            maximum = self.config.get('delivery','retryMaxSeconds')
            delay = max(delay,random.uniform(0,min(maximum,base*2**min(row['attempt_count']-1,20))))
            if now()+timedelta(seconds=delay)>=row['deadline']:
                state,error = 'expired','retry_window_expired'
        event('delivery.attempt',eventId=str(row['event_id']),status=state,reasonCode=error,attempt=row['attempt_count'])
        with self.db.transaction() as c:
            c.execute("UPDATE outbox SET state=%s,last_error=%s,next_attempt_at=clock_timestamp()+(%s * interval '1 second'),lease_until=NULL WHERE event_id=%s AND lease_owner=%s AND fence=%s AND state='pending' AND lease_until>clock_timestamp()", (state,error,delay,row['event_id'],self.owner,row['fence']))

    def run(self):
        tasks = set()
        with ThreadPoolExecutor(max_workers=self.config.get('delivery','maxConcurrentRequests')) as pool:
            try:
                while not self.stop.is_set():
                    try:
                        self.sync()
                        tasks = {task for task in tasks if not task.done()}
                        while len(tasks)<self.config.get('delivery','maxConcurrentRequests'):
                            row = self.claim()
                            if not row:
                                break
                            tasks.add(pool.submit(self.send,row))
                    except Exception:
                        try:
                            self.db.presence(self.owner,'notifier',False,'dependency_unavailable')
                        except Exception:
                            pass
                    self.stop.wait(1)
            finally:
                self.db.presence(self.owner,'notifier',False,'stopping')
