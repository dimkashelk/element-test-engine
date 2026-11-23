#!/usr/bin/env python3
"""Verify an installed service with synthetic identities and read-only Moodle probes.

Run on the host, with access to the deployment's local credential file and Docker.
No request or administrative command is sent to Moodle except read-only HTTP GET.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid


def request(url, token=None, data=None, headers=None, method=None):
    values = dict(headers or {})
    if token:
        values['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, data=data, headers=values, method=method)
    try:
        response = urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read(), dict(response.headers)


def container_state():
    value = json.loads(subprocess.check_output(['docker', 'inspect', 'moodle-grade-api_moodle-rest_1']))[0]
    config = {key: value[key] for key in ('Config', 'HostConfig')}
    config['Mounts'] = sorted(value['Mounts'], key=lambda mount: mount['Destination'])
    return {'id': value['Id'], 'startedAt': value['State']['StartedAt'],
            'restartCount': value['RestartCount'], 'health': value['State']['Health']['Status'],
            'configurationSha256': hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--release', type=Path, default=Path('/opt/element-autocheck/current'))
    operator_home = Path(os.path.expanduser('~' + os.environ.get('SUDO_USER', '')))
    parser.add_argument('--credentials', type=Path,
                        default=operator_home / '.config/element-autocheck/credentials.json')
    parser.add_argument('--criteria', type=Path, required=True)
    parser.add_argument('--dump', type=Path)
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('--output', type=Path, default=Path('/srv/element-autocheck/verification.json'))
    args = parser.parse_args()
    credentials = json.loads(args.credentials.read_text())
    base, token = credentials['baseUrl'], credentials['localTestingToken']
    before = container_state()
    cgroup = Path('/sys/fs/cgroup/autocheck.slice')
    samples, seen, results, probe_errors = [], {}, [], []
    stop = threading.Event()

    def monitor():
        while not stop.is_set():
            try:
                started = time.monotonic()
                status, _, _ = request('http://127.0.0.1:8000/healthz')
                assert status == 200, ('Moodle health', status)
                memory = int((cgroup/'memory.current').read_text())
                available = next(int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
                samples.append({'monotonic': time.monotonic(), 'moodleHttpStatus': status, 'moodleHttpMs': round((time.monotonic()-started)*1000, 2),
                                'autocheckMemoryBytes': memory, 'hostAvailableBytes': available,
                                'cpu': (cgroup/'cpu.stat').read_text()})
                ids = subprocess.check_output(['docker', 'ps', '-q', '--filter', 'label=element.autocheck.instance'], text=True).split()
                for identity in ids:
                    if identity in seen:
                        continue
                    p = subprocess.run(['docker', 'inspect', identity], capture_output=True, text=True)
                    if p.returncode:
                        continue
                    c = json.loads(p.stdout)[0]
                    seen[identity] = {'name': c['Name'], 'cgroupParent': c['HostConfig']['CgroupParent'],
                                      'memoryLimit': c['HostConfig']['Memory']}
            except Exception as error:
                probe_errors.append(type(error).__name__)
            stop.wait(1)

    thread = threading.Thread(target=monitor)
    thread.start()
    try:
        time.sleep(5)
        baseline_count = len(samples)
        status, _, _ = request(base+'/health/ready')
        assert status == 200, ('readiness', status)
        status, _, _ = request(base+'/v1/capacity')
        assert status == 401, ('unauthenticated', status)
        dump = (args.dump or args.release/'Dvizhok.xdump').read_bytes()
        for case in json.loads(args.criteria.read_text()):
            started = time.monotonic()
            criteria = json.dumps(case['criteria'], ensure_ascii=False).encode()
            identity = 'deployment-'+uuid.uuid4().hex
            manifest = {'submissionId': identity, 'studentId': 'deployment-test', 'assignmentId': case['name'],
                        'attempt': 1, 'runMode': case['runMode'],
                        'dump': {'format': 'xdump', 'sizeBytes': len(dump), 'sha256': hashlib.sha256(dump).hexdigest()},
                        'criteria': {'format': 'yaml', 'sizeBytes': len(criteria), 'sha256': hashlib.sha256(criteria).hexdigest()}}
            if 'criteriaVersion' in case:
                manifest['criteriaVersion'] = case['criteriaVersion']
            def reserve(body, key):
                return request(base+'/v1/reservations', token, json.dumps(body).encode(),
                               {'Content-Type': 'application/json', 'Idempotency-Key': key})
            status, body, _ = reserve(manifest, identity)
            assert status == 201, ('reservation', status, body.decode())
            reservation = json.loads(body)
            overflow = dict(manifest, submissionId=identity+'-overflow')
            status, _, _ = reserve(overflow, identity+'-overflow')
            assert status == 429, ('single-slot-limit', status)
            boundary = 'autocheck-'+uuid.uuid4().hex
            payload = bytearray()
            for name, filename, value in [('dump', 'input.xdump', dump), ('criteria', 'assignment.yaml', criteria)]:
                payload.extend(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+name+'"; filename="'+filename+'"\r\nContent-Type: application/octet-stream\r\n\r\n').encode())
                payload.extend(value)
                payload.extend(b'\r\n')
            payload.extend(('--'+boundary+'--\r\n').encode())
            status, body, _ = request(base+'/v1/checks', token, bytes(payload),
                                      {'Content-Type': 'multipart/form-data; boundary='+boundary,
                                       'Idempotency-Key': identity, 'X-Reservation-Id': reservation['reservationId']})
            assert status == 202, ('upload', status, body.decode())
            deadline = time.monotonic()+args.timeout
            while time.monotonic() < deadline:
                status, body, _ = request(base+'/v1/checks/'+reservation['checkId'], token)
                assert status == 200, ('poll', status)
                value = json.loads(body)
                if value['status'] in ('completed', 'execution_failed', 'rejected'):
                    break
                time.sleep(.5)
            assert value['status'] == 'completed', (case['name'], value.get('error'), value['status'])
            assert ('criteriaVersion' in value) == ('criteriaVersion' in manifest)
            assert value.get('criteriaVersion') == manifest.get('criteriaVersion')
            assert value['inputHashes']['criteriaSha256'] == manifest['criteria']['sha256']
            assert value['grading']['status'] == 'passed', (case['name'], value['grading'])
            if 'expectedScore' in case:
                assert value['grading']['score']==case['expectedScore'] and value['grading']['maxScore']==case['expectedScore'], (case['name'],'score')
            if 'expectedChecks' in case:
                assert len(value['grading']['feedback'])==case['expectedChecks'], (case['name'],'feedback')
            status, body, headers = request(base+'/v1/checks/'+reservation['checkId']+'/artifacts/grading.json', token)
            assert status == 200 and json.loads(body)['status'] == 'passed'
            status, body, _ = request(base+'/v1/capacity', token)
            capacity = json.loads(body)
            assert capacity['occupiedSlots'] == 0 and capacity['hardLimit'] == 1
            result = {'case': case['name'], 'result': 'passed', 'checkId': reservation['checkId'],
                      'seconds': round(time.monotonic()-started, 2), 'singleSlotEnforced': True,
                      'jsonArtifact': True, 'slotsAfter': 0,
                      'status':value['grading']['status'],'score':value['grading']['score'],
                      'maxScore':value['grading']['maxScore'],'unavailablePoints':value['grading']['unavailablePoints'],
                      'checks':len(value['grading']['feedback']),'sourceHash':value['grading']['sourceHash'],
                      'engine':value.get('engine')}
            results.append(result)
            print(json.dumps(result), flush=True)
        time.sleep(3)
    finally:
        stop.set()
        thread.join(timeout=25)
        after = container_state()
        baseline = samples[:locals().get('baseline_count', 0)]
        workload = samples[locals().get('baseline_count', 0):]
        report = {'cases': results, 'moodleBefore': before, 'moodleAfter': after, 'moodleUnchanged': before == after,
                  'moodleProbeErrors': probe_errors, 'probeCount': len(samples),
                  'moodleHttpStatuses': sorted({s['moodleHttpStatus'] for s in samples}),
                  'baselineMoodleMedianMs': statistics.median([s['moodleHttpMs'] for s in baseline]) if baseline else None,
                  'workloadMoodleMedianMs': statistics.median([s['moodleHttpMs'] for s in workload]) if workload else None,
                  'workloadMoodleMaxMs': max([s['moodleHttpMs'] for s in workload], default=0),
                  'peakAutocheckMemoryBytes': max([s['autocheckMemoryBytes'] for s in samples], default=0),
                  'minimumHostAvailableBytes': min([s['hostAvailableBytes'] for s in samples], default=0),
                  'transientContainers': list(seen.values()), 'memoryEvents': (cgroup/'memory.events').read_text(),
                  'sampleCount': len(samples)}
        if len(workload) > 1:
            def usage(sample):
                return int(dict(line.split() for line in sample['cpu'].splitlines())['usage_usec'])
            report['averageAutocheckCpuCores'] = round((usage(workload[-1])-usage(workload[0])) / 1e6 /
                                                       (workload[-1]['monotonic']-workload[0]['monotonic']), 3)
        args.output.write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
    assert before == after, 'Moodle state/configuration changed'
    assert not probe_errors, 'Moodle probe failures'
    assert seen and all(c['cgroupParent'] == 'autocheck.slice' for c in seen.values()), 'Transient container escaped aggregate budget'


if __name__ == '__main__':
    main()
