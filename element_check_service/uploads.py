"""Bounded streaming multipart; filenames never become filesystem paths."""
import asyncio
import fcntl
from hashlib import sha256
import os
import shutil
import time
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from .common import ServiceError, sync_dir


class UploadParser:
    def __init__(self, content_type, directory, manifest):
        kind, options = parse_options_header(content_type)
        boundary = options.get(b'boundary', b'')
        if kind != b'multipart/form-data' or not 1 <= len(boundary) <= 200:
            raise ServiceError(422, 'upload_failed')
        self.directory, self.manifest = directory, manifest
        self.seen, self.headers, self.field, self.value = set(), {}, bytearray(), bytearray()
        self.stream, self.name, self.complete = None, None, False
        self.size, self.hash = 0, None
        callbacks = {name:getattr(self,name) for name in ('on_part_begin','on_header_field','on_header_value','on_header_end','on_headers_finished','on_part_data','on_part_end','on_end')}
        self.parser = MultipartParser(boundary, callbacks, max_header_count=8, max_header_size=8192)

    def on_part_begin(self):
        self.headers, self.field, self.value = {}, bytearray(), bytearray()

    def on_header_field(self, data, start, end):
        self.field.extend(data[start:end])

    def on_header_value(self, data, start, end):
        self.value.extend(data[start:end])

    def on_header_end(self):
        field = bytes(self.field).lower()
        if field in self.headers:
            raise ServiceError(422,'upload_failed')
        self.headers[field] = bytes(self.value)
        self.field.clear()
        self.value.clear()

    def on_headers_finished(self):
        kind, options = parse_options_header(self.headers.get(b'content-disposition', b''))
        try:
            name = options.get(b'name',b'').decode('ascii')
        except UnicodeError:
            raise ServiceError(422,'upload_failed') from None
        if kind != b'form-data' or name not in ('dump','criteria') or name in self.seen:
            raise ServiceError(422,'upload_failed')
        self.name, self.size, self.hash = name, 0, sha256()
        self.seen.add(name)
        suffix = self.manifest[name]['format']
        self.stream = (self.directory / (name+'.'+suffix)).open('xb')

    def on_part_data(self, data, start, end):
        part = memoryview(data)[start:end]
        self.size += len(part)
        if self.size > self.manifest[self.name]['sizeBytes']:
            raise ServiceError(413,'payload_too_large')
        self.hash.update(part)
        self.stream.write(part)

    def on_part_end(self):
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()
        self.stream = None
        manifest = self.manifest[self.name]
        if self.size != manifest['sizeBytes'] or self.hash.hexdigest() != manifest['sha256']:
            raise ServiceError(422,'manifest_mismatch')

    def on_end(self):
        self.complete = True

    def finish(self):
        self.parser.finalize()
        if not self.complete or self.seen != {'dump','criteria'} or self.stream is not None:
            raise ServiceError(422,'upload_failed')
        sync_dir(self.directory)

    def close(self):
        if self.stream:
            self.stream.close()
            self.stream = None


async def receive_upload(request, db, config, row, key):
    directory = config.data / 'staging' / f'{row["id"]}-{row["fence"]}'
    parser, lock, renamed, committed = None, None, False, False
    target = config.data / 'checks' / str(row['check_id'])
    started, heartbeat, total = time.monotonic(), time.monotonic(), 0
    duration = config.get('admission','uploadMaxDurationSeconds')
    idle = config.get('admission','uploadIdleTimeoutSeconds')
    try:
        content_length = request.headers.get('content-length')
        if content_length and (not content_length.isdigit() or int(content_length) > config.get('limits','maxRequestBytes')):
            raise ServiceError(413,'payload_too_large')
        directory.mkdir(mode=0o2770)
        lock = (directory / '.lock').open('xb')
        fcntl.flock(lock, fcntl.LOCK_EX)
        parser = UploadParser(request.headers.get('content-type',''), directory, row['manifest'])
        iterator = request.stream().__aiter__()
        while True:
            remaining = duration - (time.monotonic()-started)
            if remaining <= 0:
                raise ServiceError(422,'upload_failed')
            try:
                chunk = await asyncio.wait_for(anext(iterator), min(idle, remaining))
            except StopAsyncIteration:
                break
            total += len(chunk)
            if total > config.get('limits','maxRequestBytes'):
                raise ServiceError(413,'payload_too_large')
            for offset in range(0,len(chunk),65536):
                await run_in_threadpool(parser.parser.write,chunk[offset:offset+65536])
            if time.monotonic()-heartbeat >= config.get('admission','uploadHeartbeatSeconds'):
                await run_in_threadpool(db.upload_heartbeat,row['id'],row['fence'])
                heartbeat = time.monotonic()
        await run_in_threadpool(parser.finish)
        await run_in_threadpool(db.upload_heartbeat,row['id'],row['fence'])
        target.mkdir(mode=0o2770)
        os.rename(directory,target / 'input')
        renamed = True
        sync_dir(target)
        sync_dir(target.parent)
        sync_dir(config.data / 'staging')
        value = await run_in_threadpool(db.commit_upload,row,key)
        committed = True
        return value
    except ServiceError:
        raise
    except (TimeoutError,ClientDisconnect,ValueError,OSError):
        raise ServiceError(422,'upload_failed') from None
    finally:
        if parser:
            parser.close()
        try:
            if not committed:
                # A lost commit response is ambiguous: never remove a committed input.
                try:
                    with db.transaction() as c:
                        exists = c.execute('SELECT 1 FROM checks WHERE id=%s',(row['check_id'],)).fetchone()
                    if not exists:
                        await run_in_threadpool(db.abort_upload,row['id'],row['fence'])
                        shutil.rmtree(target if renamed else directory,ignore_errors=True)
                except Exception:
                    pass  # Recovery checks the DB before removing the orphan.
        finally:
            if lock:
                lock.close()
