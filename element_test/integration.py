"""Opt-in ephemeral PostgreSQL for SQL smoke and a fixed shipment adapter."""
import json
import os
from pathlib import Path
import re
import shutil
import secrets
import subprocess
from tempfile import TemporaryDirectory
import time
from urllib.parse import quote
import uuid
import zipfile

from .runtime import REPO, decode_output


class BackendUnavailable(Exception):
    pass


def docker(*args, input=None, env=None, timeout=30):
    # Never expose Docker/driver diagnostics: they may contain connection secrets.
    try:
        result = subprocess.run(['docker', *args], input=input, env=env,
                                capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackendUnavailable('Docker: операция недоступна или превысила timeout') from exc
    if result.returncode:
        raise BackendUnavailable('Docker: операция ' + args[0] + ' недоступна')
    return result.stdout


def preflight(check, model, *, enabled=False):
    """Validate before allocating resources. Returns configuration, never a fake result."""
    if not enabled:
        raise BackendUnavailable('Интеграционный режим выключен; требуется --integration')
    contract = check.get('integration')
    if not isinstance(contract, dict) or set(contract) != {'backend', 'operation'}:
        raise BackendUnavailable('integration требует только backend и operation')
    if contract['backend'] != 'postgres':
        raise BackendUnavailable('Неподдержанный integration backend')
    if contract['operation'] not in {'sql-smoke', 'shipment-storage'}:
        raise BackendUnavailable('Неподдержанная операция: реализован только доверенный sql-smoke; XBQL и объектный API отсутствуют')
    if any(key in check for key in ('target', 'mocks', 'context', 'args', 'runtimeDateTime')):
        raise BackendUnavailable('sql-smoke не принимает студенческий target, mocks, context или args')
    try:
        config = json.loads(Path(os.environ.get('ELEMENT_TEST_INTEGRATION_CONFIG',
                                                REPO / 'config/integration.json')).read_text())
        runtimes = json.loads(Path(os.environ.get('ELEMENT_TEST_RUNTIMES',
                                                  REPO / 'config/runtimes.json')).read_text())
        runtime = runtimes.get(model.get('compatibilityVersion')) if isinstance(runtimes, dict) else None
    except (OSError, ValueError, TypeError):
        raise BackendUnavailable('Некорректная или отсутствующая конфигурация integration/runtime') from None
    if not isinstance(config, dict) or set(config) != {'backend', 'image'} or config['backend'] != 'postgres':
        raise BackendUnavailable('Конфигурация принимает только backend=postgres и image; внешние БД запрещены')
    if not isinstance(config['image'], str) or not re.fullmatch(r'postgres:17@sha256:[0-9a-f]{64}', config['image']):
        raise BackendUnavailable('Требуется официальный postgres:17 с фиксированным digest')
    if not isinstance(runtime, dict) or not all(isinstance(runtime.get(k), str) for k in ('directory', 'image')):
        raise BackendUnavailable('Для совместимости проекта не настроен Script runtime')
    home = Path(os.environ.get('ELEMENT_SCRIPT_HOME', REPO / runtime['directory'])).resolve()
    try:
        drivers = list((home / 'lib').glob('postgresql-*.jar'))
        driver_found = False
        for path in drivers:
            with zipfile.ZipFile(path) as archive:
                driver_found |= 'org/postgresql/Driver.class' in archive.namelist()
        if not driver_found:
            raise BackendUnavailable('В Script runtime отсутствует PostgreSQL JDBC-драйвер')
        if not list((home / 'lib').glob('com.e1c.g5rt.appliedobjects.sql.xbsl.runtime-*.jar')):
            raise BackendUnavailable('В Script runtime отсутствует SQL API')
    except (OSError, zipfile.BadZipFile):
        raise BackendUnavailable('Невозможно прочитать SQL API/JDBC-драйвер runtime') from None
    password = os.environ.get('ELEMENT_TEST_INTEGRATION_PASSWORD', '')
    if not password or '\x00' in password or '\n' in password or '\r' in password:
        raise BackendUnavailable('Требуется непустой ELEMENT_TEST_INTEGRATION_PASSWORD из окружения (без переводов строки)')
    if not shutil.which('docker'):
        raise BackendUnavailable('Для интеграционного backend требуется Docker')
    try:
        docker('info', '--format', '{{.ServerVersion}}')
    except BackendUnavailable:
        raise BackendUnavailable('Docker daemon недоступен; проверьте запуск Docker и доступ к socket') from None
    for label, image in (('PostgreSQL', config['image']), ('Script executor', runtime['image'])):
        try:
            docker('image', 'inspect', image, '--format', '{{.Id}}')
        except BackendUnavailable:
            raise BackendUnavailable(f'Локальный образ {label} отсутствует или недоступен; автоматическая загрузка выключена') from None
    return config, runtime, home, password


def executor_command(runtime, home, directory, network, name, compatibility):
    """Same containment as mocks, with only this run's internal DB network."""
    return ['create', '--name', name, '--pull', 'never', '--network', network,
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--user', '65534:65534', '--memory', '384m', '--memory-swap', '384m',
            '--cpus', '1', '--pids-limit', '64', '--ulimit', 'cpu=8:8',
            '--ulimit', 'fsize=2097152:2097152', '--log-driver', 'none',
            '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m', '--workdir', '/tmp',
            '--mount', f'type=bind,source={home},target=/runtime,readonly',
            '--mount', f'type=bind,source={directory},target=/trusted,readonly', runtime['image'],
            'java', '-Xmx128m', '-XX:MaxMetaspaceSize=128m', '-XX:+UseSerialGC',
            '--add-opens', 'java.base/java.lang=ALL-UNNAMED',
            '--add-opens', 'java.base/java.nio=ALL-UNNAMED', '-Dfile.encoding=UTF-8',
            '-Djava.io.tmpdir=/tmp', '-Dlogs.root=/tmp',
            '-Dlogback.configurationFile=/runtime/config/logback.xml', '-Dexecutor.location=/runtime',
            '-cp', '/runtime/lib/*', 'com.e1c.g5rt.executor.boot.ExecutorBootstrap',
            '-c', compatibility, '/trusted/SqlSmoke.sbsl', '/trusted/connection.json']


def executor_output(executor, directory):
    """Bound student output and wall time; never export raw driver diagnostics."""
    process = None
    stdout_path, stderr_path = directory / 'stdout', directory / 'stderr'
    try:
        with stdout_path.open('w+b') as out, stderr_path.open('w+b') as err:
            process = subprocess.Popen(['docker', 'start', '--attach', executor], stdout=out, stderr=err)
            deadline = time.monotonic() + 30
            while process.poll() is None:
                if time.monotonic() > deadline:
                    raise BackendUnavailable('Интеграция: превышен timeout executor')
                if stdout_path.stat().st_size + stderr_path.stat().st_size > 1024 * 1024:
                    raise BackendUnavailable('Интеграция: превышен лимит вывода 1 MiB')
                time.sleep(0.05)
            if process.returncode or stdout_path.stat().st_size + stderr_path.stat().st_size > 1024 * 1024:
                raise BackendUnavailable('Интеграция: ошибка executor или превышен лимит вывода')
            out.seek(0)
            return out.read(1024 * 1024).decode('utf-8', errors='replace')
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


def run_integration(check, model, temporary, *, enabled=False, inject_failure=False, root=None):
    """One fresh PostgreSQL and network per check; injection is trusted test-only API."""
    try:
        config, runtime, home, password = preflight(check, model, enabled=enabled)
    except BackendUnavailable as exc:
        return {'status': 'UNSUPPORTED', 'message': str(exc)}
    operation = check['integration']['operation']
    if operation == 'shipment-storage' and root is None:
        return {'status': 'UNSUPPORTED', 'message': 'shipment-storage требует исходный проект'}
    role_password = secrets.token_hex(32)
    run_id = uuid.uuid4().hex
    network, database, executor = ['element-integration-' + run_id + suffix
                                   for suffix in ('-net', '-db', '-exec')]
    resources = []
    result = {'status': 'ERROR', 'message': 'SQL smoke: незавершённое выполнение'}
    phase = 'provision'
    # Directory must outlive containers; no credentials are copied to reports.
    with TemporaryDirectory(prefix='integration-', dir=temporary) as work:
        directory = Path(work)
        try:
            if operation == 'shipment-storage':
                from .shipment_storage import prepare, SCHEMA, audit
                from .yaml_io import InputError
                try:
                    prepare(root, model, check, directory, inject_failure=inject_failure)
                except InputError as exc:
                    return {'status': 'UNSUPPORTED', 'message': str(exc)}
            resources.append(('network', network))
            docker('network', 'create', '--internal', network)
            resources.append(('container', database))
            docker('create', '--name', database, '--pull', 'never', '--network', network,
                   '--network-alias', 'db', '--memory', '256m', '--memory-swap', '256m',
                   '--cpus', '1', '--pids-limit', '128', '--security-opt', 'no-new-privileges',
                   '--log-driver', 'none', '--tmpfs', '/var/lib/postgresql/data:rw,nosuid,size=128m',
                   '--env', 'POSTGRES_PASSWORD', '--env', 'POSTGRES_DB=integration', config['image'],
                   env={**os.environ, 'POSTGRES_PASSWORD': password})
            docker('start', database)
            deadline = time.monotonic() + 20
            while True:
                try:
                    # Wait for final TCP listener, not the temporary init socket server.
                    docker('exec', database, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres', '-d', 'integration', timeout=3)
                    break
                except BackendUnavailable:
                    if time.monotonic() > deadline:
                        raise BackendUnavailable('PostgreSQL не готов за 20 секунд')
                    time.sleep(0.2)
            escaped = role_password.replace("'", "''")
            bootstrap = f"""SET standard_conforming_strings = on;
CREATE ROLE smoke LOGIN PASSWORD '{escaped}' NOSUPERUSER NOCREATEDB NOCREATEROLE;
REVOKE ALL ON DATABASE integration FROM PUBLIC;
GRANT CONNECT ON DATABASE integration TO smoke;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA smoke;
CREATE TABLE smoke.stock(dataset text, item text, warehouse text, quantity numeric(20,6),
  PRIMARY KEY(dataset, item, warehouse));
GRANT USAGE ON SCHEMA smoke TO smoke;
GRANT SELECT, INSERT, UPDATE, DELETE ON smoke.stock TO smoke;
ALTER ROLE smoke SET statement_timeout = '3s';
"""
            if operation == 'shipment-storage':
                bootstrap += SCHEMA
            docker('exec', '-i', database, 'psql', '-U', 'postgres', '-d', 'integration',
                   '-v', 'ON_ERROR_STOP=1', input=bootstrap)
            if operation == 'sql-smoke':
                (directory / 'SqlSmoke.sbsl').write_text((REPO / 'src/SqlSmoke.sbsl').read_text())
            credentials = directory / 'connection.json'
            credentials.write_text(json.dumps({
                'connection': 'jdbc:postgresql://db:5432/integration?user=smoke&password=' + quote(role_password, safe='')
                              + '&connectTimeout=3&socketTimeout=5',
                'first': 'first-' + run_id, 'second': 'second-' + run_id,
                'injectFailure': inject_failure}))
            # The temporary parent is 0700 on host. Only this trusted container
            # sees only the restricted role credential; admin/bootstrap are not mounted.
            resources.append(('container', executor))
            docker(*executor_command(runtime, home, directory, network, executor, model['compatibilityVersion']))
            phase = 'execute'
            stdout = executor_output(executor, directory)
            state = json.loads(docker('inspect', executor, '--format', '{{json .State}}'))
            if state.get('ExitCode') != 0 or state.get('OOMKilled'):
                result = {'status': 'ERROR', 'message': 'Интеграция: Script executor завершился с ошибкой'}
            else:
                decoded = decode_output(stdout)
                if decoded.get('status') == 'EXECUTED' and isinstance(decoded.get('actual'), dict):
                    result = {'status': 'EXECUTED', 'actual': decoded['actual']}

                elif operation == 'shipment-storage' and decoded.get('status') == 'UNSUPPORTED':
                    result = {'status': 'UNSUPPORTED', 'message': 'shipment-storage: неоднозначная проекция остатков по нескольким складам'}
                else:
                    result = {'status': 'ERROR', 'message': 'Интеграция: ошибка выполнения запроса'}
                if operation == 'shipment-storage':
                    committed = audit(database, docker)
                    if result['status'] == 'EXECUTED':
                        result['actual']['database'] = committed
                    else:
                        result['storageEvidence'] = committed
        except BackendUnavailable as exc:
            result = {'status': 'UNSUPPORTED' if phase == 'provision' else 'ERROR', 'message': str(exc)}
        except (OSError, ValueError, KeyError, TypeError):
            result = {'status': 'ERROR', 'message': 'Интеграция: некорректный результат или ошибка инфраструктуры'}
        finally:
            cleanup = True
            for kind, name in reversed(resources):
                try:
                    listing = ['ps', '-a', '--format', '{{.Names}}'] if kind == 'container' else ['network', 'ls', '--format', '{{.Name}}']
                    if name in docker(*listing, '--filter', 'name=' + name, timeout=10).splitlines():
                        docker('rm' if kind == 'container' else 'network',
                               *(['-f', name] if kind == 'container' else ['rm', name]), timeout=10)
                    if name in docker(*listing, '--filter', 'name=' + name, timeout=10).splitlines():
                        cleanup = False
                except BackendUnavailable:
                    cleanup = False
            # Drop any student output/DB value containing this run's credentials.
            serialized = json.dumps(result, ensure_ascii=False)
            if password in serialized or role_password in serialized or 'jdbc:' in serialized:
                result = {'status': 'ERROR', 'message': 'Интеграция: запрещён вывод данных подключения'}
            # Cleanup is required even for injected failure or an executor timeout.
            if not cleanup:
                result = {'status': 'ERROR', 'message': 'SQL smoke: очистка ресурсов Docker не завершена'}
            result['integration'] = {'backend': 'postgres', 'operation': operation,
                                     'runId': run_id, 'cleanup': cleanup}
    return result
