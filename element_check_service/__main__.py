import argparse
import os
from pathlib import Path
import signal
import sys
import urllib.request
from .config import Config
from .db import Database


def main():
    os.umask(0o007)
    parser = argparse.ArgumentParser(description='Element Autocheck service')
    commands = parser.add_subparsers(dest='command',required=True)
    commands.add_parser('migrate')
    api = commands.add_parser('api')
    api.add_argument('--host',default='127.0.0.1')
    api.add_argument('--port',type=int,default=8000)
    commands.add_parser('worker')
    commands.add_parser('notifier')
    health = commands.add_parser('health')
    health.add_argument('role',choices=['api','worker','notifier'])
    args = parser.parse_args()
    config = Config()
    db = Database(config)
    if args.command=='migrate':
        db.migrate()
    elif args.command=='api':
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(config,db),host=args.host,port=args.port,access_log=False,proxy_headers=False)
    elif args.command in ('worker','notifier'):
        if args.command=='worker':
            from .worker import Worker
            service = Worker(db,config)
        else:
            from .notifier import Notifier
            service = Notifier(db,config)
        for sig in (signal.SIGINT,signal.SIGTERM):
            signal.signal(sig,lambda *_:service.stop.set())
        # Health checks query this exact process, never another replica's heartbeat.
        marker = Path('/tmp/autocheck-'+args.command+'.id')
        marker.write_text(service.owner)
        service.run()
    elif args.role=='api':
        with urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=2) as response:
            return 0 if response.status==200 else 1
    else:
        identity = Path('/tmp/autocheck-'+args.role+'.id').read_text()
        with db.transaction() as c:
            row = c.execute("SELECT 1 FROM worker_presence WHERE worker_id=%s AND role=%s AND ready AND heartbeat_at>clock_timestamp()-(%s * interval '1 second')", (identity,args.role,config.get('admission','workerStaleAfterSeconds'))).fetchone()
        return 0 if row else 1
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except Exception:
        # No DSNs, token registries or engine output in service error logs.
        print('autocheck: operation failed; check configuration and dependencies',file=sys.stderr)
        raise SystemExit(1)
