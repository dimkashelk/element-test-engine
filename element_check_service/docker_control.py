"""Supervisor-owned Docker operations, fenced and registered before creation.

The trusted engine uses a private Docker CLI proxy. Executors never see its socket.
A per-check advisory lock serializes daemon operations with recovery/cleanup.
"""
import base64
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socketserver
import subprocess
import threading
import time
import uuid
from .common import encoded

PREFIX = 'element.autocheck'


def resource_parent(args):
    """Apply the operator's aggregate budget to every transient container."""
    if args[0] != 'create':
        return args
    if any(value == '--cgroup-parent' or value.startswith('--cgroup-parent=') for value in args):
        raise ValueError('Engine cannot override the operator resource parent')
    parent = os.environ.get('AUTOCHECK_CGROUP_PARENT', '')
    if not parent:
        return args
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.slice', parent):
        raise ValueError('Expected a systemd slice name')
    return [args[0], '--cgroup-parent', parent, *args[1:]]


def real_docker(args, *, input=None, env=None, timeout=35):
    return subprocess.run(['docker',*args],input=input,env=env,capture_output=True,timeout=timeout)


def advisory(c, cid):
    c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(str(cid),))


class DockerControl:
    def __init__(self, db, row, *, runner=real_docker):
        self.db,self.row,self.runner = db,row,runner
        with db.transaction() as c:
            self.instance = str(c.execute('SELECT instance_id FROM service_instance WHERE id=1').fetchone()['instance_id'])
        self.cancel = threading.Event()
        self.timeouts = {}
        self.token = secrets.token_hex(32)
        self.socket = '/tmp/acs-'+uuid.uuid4().hex+'.sock'
        control = self
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    self.connection.settimeout(10)
                    raw = self.rfile.readline(20*1024*1024)
                    if not raw.endswith(b'\n'):
                        raise ValueError()
                    request = json.loads(raw)
                    if not hmac.compare_digest(request['token'],control.token):
                        raise ValueError()
                    result = control.execute(request)
                except Exception:
                    result = {'code':125,'stdout':'','stderr':base64.b64encode(b'Docker operation unavailable').decode()}
                self.wfile.write(encoded(result)+b'\n')
        self.server = socketserver.ThreadingUnixStreamServer(self.socket,Handler)
        self.server.daemon_threads = False
        os.chmod(self.socket,0o600)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.cancel.set()
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()  # Waits until every in-flight daemon operation ends.
        if self.thread.is_alive():
            self.thread.join()
        Path(self.socket).unlink(missing_ok=True)

    def labels(self, name, fence=None):
        return {PREFIX+'.instance':self.instance,PREFIX+'.check':str(self.row['id']),
                PREFIX+'.fence':str(self.row['fence'] if fence is None else fence),PREFIX+'.resource':name}

    def execute(self, request):
        args = request['args']
        if not isinstance(args,list) or not args or any(not isinstance(x,str) for x in args):
            raise ValueError()
        if args[0] not in ('create','start','rm','exec','inspect','ps','network','info','image'):
            raise ValueError('Unsupported Docker command')
        if args[0]=='image' and args[1:2]!=['inspect']:
            raise ValueError('Only image inspection is allowed')
        args = resource_parent(args)
        creating = args[0]=='create' or args[:2]==['network','create']
        name = args[args.index('--name')+1] if args[0]=='create' else args[-1] if creating else None
        kind = 'container' if args[0]=='create' else 'network'
        with self.db.transaction() as c:
            advisory(c,self.row['id'])
            lease = c.execute("SELECT 1 FROM checks WHERE id=%s AND fence=%s AND lease_owner=%s AND state='running' AND lease_until>clock_timestamp()", (self.row['id'],self.row['fence'],self.row['lease_owner'])).fetchone()
            if not lease:
                raise ValueError('Lease lost')
            if creating:
                # Persist intent before contacting the daemon; a timeout remains uncertain.
                if not name or not name.startswith('element-'):
                    raise ValueError('Invalid resource name')
                role = 'network' if kind=='network' else 'executor' if '--user' in args else 'database'
                if c.execute('SELECT 1 FROM docker_resources WHERE check_id=%s AND resource_role=%s AND deleted_at IS NULL',(self.row['id'],role)).fetchone():
                    raise ValueError('A resource of this role is already active')
                for mount in args:
                    if mount.startswith('type=bind,source=') and ',target=/generated,readonly' in mount:
                        source = Path(mount.split('source=',1)[1].split(',',1)[0]).resolve()
                        allowed = (self.db.config.data/'work'/str(self.row['id'])).resolve()
                        if not source.is_relative_to(allowed):
                            raise ValueError('Generated mount outside job directory')
                        for path in [source,*source.rglob('*')]:
                            if path.is_symlink():
                                raise ValueError('Symlink in generated inputs')
                            path.chmod(0o755 if path.is_dir() else 0o644)
                self.timeouts[name] = next((int(x.split('=')[1].split(':')[0]) for x in args if x.startswith('cpu=')),30)
                with self.db.transaction() as intent:
                    intent.execute("INSERT INTO docker_resources(intent_name,check_id,fence,kind,resource_role,operation_state) VALUES (%s,%s,%s,%s,%s,'creating')", (name,self.row['id'],self.row['fence'],kind,role))
                labels = [x for k,v in self.labels(name).items() for x in ('--label',k+'='+v)]
                at = 1 if args[0]=='create' else 2
                args = args[:at]+labels+args[at:]
            else:
                target = None
                if args[0]=='start':
                    target = args[-1]
                elif args[0]=='exec':
                    target = next(x for x in args[1:] if not x.startswith('-'))
                elif args[0]=='rm' or args[:2]==['network','rm']:
                    target = args[-1]
                elif args[0]=='network' and args[1] not in ('ls','inspect'):
                    raise ValueError('Unsupported network command')
                if target and not c.execute('SELECT 1 FROM docker_resources WHERE intent_name=%s AND check_id=%s AND fence=%s', (target,self.row['id'],self.row['fence'])).fetchone():
                    raise ValueError('Unowned resource')
            env = dict(os.environ)
            if 'password' in request:
                env['POSTGRES_PASSWORD'] = request['password']
            input_bytes = base64.b64decode(request['input']) if request.get('input') is not None else None
            try:
                if args[0]=='start' and '--attach' in args and self.runner is real_docker:
                    result = self.attach(args)
                else:
                    result = self.runner(args,input=input_bytes,env=env,timeout=30)
            except subprocess.TimeoutExpired:
                if creating:
                    with self.db.transaction() as intent:
                        intent.execute("UPDATE docker_resources SET operation_state='uncertain' WHERE intent_name=%s",(name,))
                raise
            if not creating and target and result.returncode==0 and (args[0]=='rm' or args[:2]==['network','rm']):
                c.execute("UPDATE docker_resources SET operation_state='deleted',deleted_at=clock_timestamp() WHERE intent_name=%s",(target,))
            if creating:
                with self.db.transaction() as intent:
                    intent.execute('UPDATE docker_resources SET operation_state=%s,resource_id=%s WHERE intent_name=%s',
                        ('created' if result.returncode==0 else 'uncertain', result.stdout.decode().strip() if result.returncode==0 else None,name))
            return {'code':result.returncode,'stdout':base64.b64encode(result.stdout[:2*1024*1024]).decode(),
                    'stderr':base64.b64encode(result.stderr[:65536]).decode()}

    def attach(self, args):
        import selectors
        p = subprocess.Popen(['docker',*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        selector = selectors.DefaultSelector()
        selector.register(p.stdout,selectors.EVENT_READ,0)
        selector.register(p.stderr,selectors.EVENT_READ,1)
        buffers = [bytearray(),bytearray()]
        deadline = time.monotonic()+self.timeouts.get(args[-1],30)
        try:
            while selector.get_map():
                if self.cancel.is_set() or time.monotonic()>deadline or sum(map(len,buffers))>1024*1024:
                    raise ValueError('Executor output/time limit')
                for key,_ in selector.select(0.1):
                    data = os.read(key.fileobj.fileno(),65536)
                    if data:
                        buffers[key.data].extend(data)
                    else:
                        selector.unregister(key.fileobj)
            return subprocess.CompletedProcess(args,p.wait(timeout=5),bytes(buffers[0]),bytes(buffers[1]))
        finally:
            selector.close()
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5)
            p.stdout.close()
            p.stderr.close()

    def cleanup(self):
        """Remove only matching labels; uncertain creation without a resource holds capacity."""
        with self.db.transaction() as c:
            advisory(c,self.row['id'])
            rows = c.execute('SELECT * FROM docker_resources WHERE check_id=%s ORDER BY kind,intent_name',(self.row['id'],)).fetchall()
            if not rows:
                return True  # Every create commits its intent before contacting Docker.
            # Sweep catches create -> registry-write crashes using the persistent instance ID.
            filters = [x for k,v in {PREFIX+'.instance':self.instance,PREFIX+'.check':str(self.row['id'])}.items() for x in ('--filter','label='+k+'='+v)]
            for kind,command in [('container',['ps','-aq']),('network',['network','ls','-q'])]:
                listing = self.runner(command+filters,timeout=15)
                if listing.returncode:
                    return False
                known = {r['intent_name'] for r in rows}
                for resource_id in listing.stdout.decode().splitlines():
                    inspected = self.runner((['inspect'] if kind=='container' else ['network','inspect'])+[resource_id],timeout=15)
                    if inspected.returncode:
                        return False
                    value = json.loads(inspected.stdout)[0]
                    labels = value.get('Config',{}).get('Labels',{}) if kind=='container' else value.get('Labels',{})
                    name = labels.get(PREFIX+'.resource')
                    if not name or labels.get(PREFIX+'.instance')!=self.instance or labels.get(PREFIX+'.check')!=str(self.row['id']):
                        return False
                    if name not in known:
                        return False  # Unregistered intent needs operator investigation.
            for row in rows:
                inspect = self.runner((['inspect'] if row['kind']=='container' else ['network','inspect'])+[row['intent_name']],timeout=15)
                if inspect.returncode:
                    listing = self.runner((['ps','-a','--format','{{.Names}}'] if row['kind']=='container' else ['network','ls','--format','{{.Name}}']),timeout=15)
                    if listing.returncode or row['intent_name'] in listing.stdout.decode().splitlines():
                        return False
                    # A completed create/rm can be absent; an unacknowledged create cannot.
                    if row['operation_state'] in ('creating','uncertain'):
                        return False
                    c.execute("UPDATE docker_resources SET deleted_at=clock_timestamp(),operation_state='deleted' WHERE intent_name=%s",(row['intent_name'],))
                    continue
                value = json.loads(inspect.stdout)[0]
                labels = value.get('Config',{}).get('Labels',{}) if row['kind']=='container' else value.get('Labels',{})
                if any(labels.get(k)!=v for k,v in self.labels(row['intent_name'],row['fence']).items()):
                    return False
                removed = self.runner((['rm','-f'] if row['kind']=='container' else ['network','rm'])+[row['intent_name']],timeout=15)
                if removed.returncode:
                    return False
                c.execute("UPDATE docker_resources SET deleted_at=clock_timestamp(),operation_state='deleted' WHERE intent_name=%s",(row['intent_name'],))
            return True
