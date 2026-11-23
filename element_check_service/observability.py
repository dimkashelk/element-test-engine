"""Structured transport logs with no URLs, bodies, tokens or personal identifiers."""
import json
import threading
import time
import uuid

COUNTERS = {}
LOCK = threading.Lock()


def event(name, **fields):
    print(json.dumps({'event':name,**fields},separators=(',',':')),flush=True)


class TransportLog:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        request_id = str(uuid.uuid4())
        scope.setdefault('state',{})['request_id'] = request_id
        status,started = 500,time.monotonic()
        async def response(message):
            nonlocal status
            if message['type']=='http.response.start':
                status = message['status']
                message['headers'] = [(k,v) for k,v in message.get('headers',[]) if k.lower()!=b'x-request-id']+[(b'x-request-id',request_id.encode())]
            await send(message)
        try:
            await self.app(scope,receive,response)
        finally:
            route = getattr(scope.get('route'),'path','unknown')
            # Health probes are measured without continuously filling operator logs.
            with LOCK:
                key = (scope['method'],route,status)
                count,seconds = COUNTERS.get(key,(0,0))
                COUNTERS[key] = count+1,seconds+time.monotonic()-started
            if not scope['path'].startswith('/health/'):
                event('http.request',requestId=request_id,method=scope['method'],route=route,status=status,durationMs=round(1000*(time.monotonic()-started),2))
