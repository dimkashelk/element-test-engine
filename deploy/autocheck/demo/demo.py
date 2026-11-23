#!/usr/bin/env python3
"""Manage the loopback-only Autocheck Compose project."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from urllib.request import ProxyHandler, Request, build_opener

DEMO = Path(__file__).resolve().parent
BASE = DEMO.parent
ROOT = BASE.parents[1]
STATE = DEMO / ".state"
CUTOVER = STATE / "kubernetes-cutover.json"
KUBERNETES_OWNER = STATE / "kubernetes-owner"
IMAGE = "element-checker/service:demo"
PROJECT = "element-autocheck-demo"


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], cwd=ROOT, check=True, **kwargs)


def docker_output(*args):
    return docker(*args, capture_output=True, text=True).stdout.strip()


def compose(*args, **kwargs):
    return docker(
        "compose", "--env-file", os.devnull, "-p", PROJECT,
        "-f", str(BASE / "compose.yaml"), "-f", str(DEMO / "compose.yaml"),
        *args, **kwargs,
    )


def save(path, value, mode=0o444):
    if path.is_file() and path.read_text() == value:
        return
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
        file.write(value)
        temporary = Path(file.name)
    temporary.chmod(mode)
    temporary.replace(path)


def initialize():
    runtime = Path(os.environ["SCRIPT_RUNTIME_ROOT"])
    if not (runtime / "lib").is_dir() or not (runtime / "config" / "logback.xml").is_file():
        raise ValueError(f"Укажите SCRIPT_RUNTIME_ROOT с дистрибутивом 1С:Элемент Скрипт: {runtime}")

    STATE.mkdir(mode=0o700, exist_ok=True)
    STATE.chmod(0o700)
    secret_dir = STATE / "secrets"
    secret_dir.mkdir(mode=0o700, exist_ok=True)
    secret_dir.chmod(0o700)
    data_dir = STATE / "data"
    data_dir.mkdir(exist_ok=True)
    # Docker Desktop maps the host owner. These shared bind paths are inside private state.
    for directory in (data_dir, *(data_dir / name for name in ("checks", "staging", "work"))):
        if not directory.exists():
            directory.mkdir()
        directory.chmod(0o2777)

    secret_names = (
        "db_password", "integration_password", "webhook_key", "grafana_password",
        "metrics_token", "load_token", "check_token", "operator_token",
    )
    for name in secret_names:
        path = secret_dir / name
        if not path.exists():
            mode = 0o400 if name in {"webhook_key", "check_token", "operator_token"} else 0o444
            save(path, secrets.token_hex(32) + "\n", mode)

    clients = []
    for client_id, name, scopes, modes in (
        ("demo", "check_token", ["checks:read", "checks:write"], ["isolated", "integration"]),
        ("metrics", "metrics_token", ["settings:read"], []),
        ("load", "load_token", ["checks:read"], []),
        ("operator", "operator_token", ["settings:read", "settings:write"], []),
    ):
        token = (secret_dir / name).read_text().strip()
        clients.append({
            "clientId": client_id,
            "allowedRunModes": modes,
            "tokens": [{
                "keyId": name,
                "tokenSha256": hashlib.sha256(token.encode()).hexdigest(),
                "scopes": scopes,
                "enabled": True,
            }],
        })
    save(secret_dir / "api_clients.json", json.dumps({"clients": clients}) + "\n")
    save(secret_dir / "webhook_clients.json", json.dumps({"clients": [{
        "clientId": "demo",
        "currentKeyId": "demo",
        "keys": {"demo": (secret_dir / "webhook_key").read_text().strip()},
    }]}) + "\n")
    config = (BASE / "service.yaml").read_text()
    delivery_setting = "\ndelivery:\n  enabled: true\n"
    if delivery_setting not in config:
        raise ValueError("Ожидался раздел delivery в базовом service.yaml")
    save(STATE / "notifier.yaml", config.replace(delivery_setting, "\ndelivery:\n  enabled: false\n", 1))


def ensure_engine_images():
    config = ROOT / "config"
    runtimes = json.loads((config / "runtimes.json").read_text())
    integration = json.loads((config / "integration.json").read_text())
    images = {integration["image"], *(runtime["image"] for runtime in runtimes.values())}
    for image in sorted(images):
        try:
            docker_output("image", "inspect", image, "--format", "{{.Id}}")
        except subprocess.CalledProcessError:
            docker("pull", image)


def build_image():
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    docker("build", "--build-arg", f"ENGINE_REVISION={revision}", "-t", IMAGE, "-f", str(BASE / "Dockerfile"), ".")


def start():
    STATE.mkdir(mode=0o700, exist_ok=True)
    with (STATE / "deployment.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Другой процесс уже запускает демонстрационный стенд") from None
        if CUTOVER.exists() or KUBERNETES_OWNER.exists():
            raise ValueError("Данные стенда перенесены в Kubernetes. Используйте kube.py up")
        initialize()
        build_image()
        ensure_engine_images()
        os.environ["ENGINE_IMAGE_DIGEST"] = docker_output("image", "inspect", IMAGE, "--format", "{{.Id}}")
        compose("config", "--quiet")
        compose("up", "-d", "--wait", "--wait-timeout", "180")


def configure():
    endpoint = os.environ.get("DOCKER_HOST")
    if os.environ.get("DOCKER_CONTEXT") or not endpoint:
        endpoint = docker_output("context", "inspect", "--format", "{{.Endpoints.docker.Host}}")
    if not endpoint.startswith("unix://"):
        raise ValueError("Для демонстрации нужен локальный Docker через Unix-сокет")
    os.environ.update(
        SERVICE_IMAGE=IMAGE,
        SERVICE_DATA_ROOT=str(STATE / "data"),
        SCRIPT_RUNTIME_ROOT=str(Path(os.environ.get("SCRIPT_RUNTIME_ROOT", ROOT / "script_u_10.0.2_1")).resolve()),
        SECRETS_ROOT=str(STATE / "secrets"),
        DEMO_STATE_ROOT=str(STATE),
        GATEWAY_BIND_ADDRESS="127.0.0.1",
        DEFAULT_MAX_CONCURRENT_CHECKS="2",
        MAX_CONCURRENT_CHECKS_HARD_LIMIT="2",
    )


def admission(action):
    port = int(os.environ.get("GATEWAY_PORT", 8088))
    url = f"http://127.0.0.1:{port}/v1/admin/settings"
    token = (STATE / "secrets" / "operator_token").read_text().strip()
    headers = {"Authorization": f"Bearer {token}"}
    opener = build_opener(ProxyHandler({}))
    with opener.open(Request(url, headers=headers), timeout=10) as response:
        value = json.load(response)
        etag = response.headers["ETag"]
    if action != "settings":
        headers.update({"If-Match": etag, "Content-Type": "application/json"})
        body = json.dumps({"accepting": action == "resume"}).encode()
        with opener.open(Request(url, data=body, headers=headers, method="PATCH"), timeout=10) as response:
            value = json.load(response)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return value


def postgres_backup(run):
    directory = STATE / "backups"
    directory.mkdir(mode=0o700, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"postgres-{timestamp}.dump"
    with tempfile.NamedTemporaryFile(dir=directory) as file:
        run("pg_dump", "-U", "autocheck", "-Fc", "autocheck", stdout=file)
        file.seek(0)
        run("pg_restore", "--list", stdin=file, stdout=subprocess.DEVNULL)
        # The temporary file is private and stays on the same filesystem as the archive.
        os.link(file.name, path)
    print(path)
    return path


def backup():
    return postgres_backup(lambda *args, **kwargs: compose("exec", "-T", "postgres", *args, **kwargs))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=(
        "up", "status", "logs", "validate", "load", "settings", "pause", "resume",
        "backup", "stop", "compose",
    ))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        configure()
        if args.action == "up":
            start()
        elif args.action == "status":
            compose("ps", "--all", *args.arguments)
        elif args.action == "logs":
            compose("logs", "--tail", "80", *args.arguments)
        elif args.action == "validate":
            compose("config", "--quiet")
            compose("exec", "-T", "gateway", "nginx", "-t")
            compose("exec", "-T", "prometheus", "promtool", "check", "config", "/etc/prometheus/prometheus.yml")
            compose("exec", "-T", "alertmanager", "amtool", "check-config", "/etc/alertmanager/alertmanager.yml")
        elif args.action == "load":
            compose("--profile", "load", "run", "--rm", "--no-deps", "k6", *args.arguments)
        elif args.action in ("settings", "pause", "resume"):
            admission(args.action)
        elif args.action == "backup":
            backup()
        elif args.action == "stop":
            compose("stop", *args.arguments)
        else:
            compose(*args.arguments)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Стенд: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
