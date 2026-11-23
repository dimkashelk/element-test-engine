"""Private CLI shim used only by the trusted engine child."""
import base64
import json
import os
import socket
import sys
from .common import encoded


def main():
    args = sys.argv[1:]
    data = sys.stdin.buffer.read(16*1024*1024+1) if '-i' in args and args[0]=='exec' else None
    if data is not None and len(data)>16*1024*1024:
        return 125
    request = {'token':os.environ['AUTOCHECK_CONTROL_TOKEN'],'args':args,
               'input':base64.b64encode(data).decode() if data is not None else None}
    if args[0]=='create' and 'POSTGRES_PASSWORD' in os.environ:
        request['password'] = os.environ['POSTGRES_PASSWORD']
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
            connection.connect(os.environ['AUTOCHECK_CONTROL_SOCKET'])
            connection.sendall(encoded(request)+b'\n')
            with connection.makefile('rb') as stream:
                response = json.loads(stream.readline(4*1024*1024))
        sys.stdout.buffer.write(base64.b64decode(response['stdout']))
        sys.stderr.buffer.write(base64.b64decode(response['stderr']))
        return response['code']
    except Exception:
        sys.stderr.write('Docker supervisor unavailable\n')
        return 125


if __name__=='__main__':
    raise SystemExit(main())
