#!/usr/bin/env python3
"""Provision an independent API token and webhook signing key on the Docker host.

Run as root, then recreate api/notifier to refresh file-based Compose secret mounts.
Existing client IDs are rejected; credentials are written only to a mode-0600 file.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import fcntl


def atomic_json(path, value, mode, uid, gid):
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name+'-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            os.fchown(stream.fileno(), uid, gid)
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('client_id')
    parser.add_argument('--secrets-dir', type=Path, default=Path('/srv/element-autocheck/secrets'))
    parser.add_argument('--credentials', type=Path, required=True)
    parser.add_argument('--owner-uid', type=int, default=0)
    parser.add_argument('--owner-gid', type=int, default=0)
    parser.add_argument('--base-url', default='http://127.0.0.1:8088')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', args.client_id):
        parser.error('Invalid client ID')
    args.credentials.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.secrets_dir/'.provision.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        api_path, webhook_path = [args.secrets_dir/name for name in ('api_clients.json', 'webhook_clients.json')]
        api, webhooks = [json.loads(path.read_text()) for path in (api_path, webhook_path)]
        if args.credentials.exists() or any(client['clientId'] == args.client_id for data in (api, webhooks) for client in data['clients']):
            parser.error('Client or credentials already exist; refusing to overwrite')
        token, signing_key = secrets.token_urlsafe(32), secrets.token_hex(32)
        credentials = {'clientId': args.client_id, 'baseUrl': args.base_url, 'apiToken': token,
                       'webhookKeyId': 'v1', 'webhookSecret': signing_key}
        api['clients'].append({'clientId': args.client_id, 'allowedRunModes': ['isolated', 'integration'],
                              'tokens': [{'keyId': 'v1', 'enabled': True, 'scopes': ['checks:read', 'checks:write'],
                                          'tokenSha256': hashlib.sha256(token.encode()).hexdigest()}]})
        webhooks['clients'].append({'clientId': args.client_id, 'currentKeyId': 'v1', 'keys': {'v1': signing_key}})
        # Credentials first: a partial failure never leaves an active token whose
        # only plaintext copy has been lost. Rerun requires operator reconciliation.
        atomic_json(args.credentials, credentials, 0o600, args.owner_uid, args.owner_gid)
        for path, data in ((webhook_path, webhooks), (api_path, api)):
            stat = path.stat()
            atomic_json(path, data, stat.st_mode & 0o777, stat.st_uid, stat.st_gid)
    print('Created client '+args.client_id+'; credentials: '+str(args.credentials))
    print('Recreate api and notifier to refresh Compose secret mounts.')


if __name__ == '__main__':
    main()
