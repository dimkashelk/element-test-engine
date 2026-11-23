#!/usr/bin/env python3
"""Deploy Autocheck on the built-in single-node Docker Desktop Kubernetes cluster."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
import uuid

import demo
from kube_manifest import Manifest, NAMESPACE, PORTS, resource

CONTEXT = "docker-desktop"
CUTOVER = demo.CUTOVER
FORWARD = demo.STATE / "kubernetes-forward.log"
FORWARD_LOCK = demo.STATE / "kubernetes-forward.lock"


def command(*args):
    return ["kubectl", "--context", CONTEXT, "--namespace", NAMESPACE, *args]


def kubectl(*args, **kwargs):
    return subprocess.run(command(*args), check=True, **kwargs)


def output(*args):
    return kubectl(*args, stdout=subprocess.PIPE, text=True).stdout.strip()


def apply(objects):
    kubectl("apply", "--server-side", "-f", "-", text=True,
            input=json.dumps({"apiVersion": "v1", "kind": "List", "items": objects}))


def configure():
    os.environ["DOCKER_CONTEXT"] = "desktop-linux"
    demo.configure()
    server = output("config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}")
    if urlsplit(server).hostname not in {"127.0.0.1", "::1", "localhost", "kubernetes.docker.internal"}:
        raise ValueError("Ожидался локальный Kubernetes Docker Desktop")


def image_refs():
    config = demo.compose("--profile", "load", "config", "--format", "json", stdout=subprocess.PIPE, text=True)
    services = json.loads(config.stdout)["services"]
    images = {name: values["image"] for name, values in services.items()}
    return images


def reachability():
    url = f"http://127.0.0.1:{os.environ.get('GATEWAY_PORT', '8088')}/health/ready"
    try:
        with build_opener(ProxyHandler({})).open(url, timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def wait_for(check, seconds, label):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(1)
    raise RuntimeError(f"Истекло время ожидания ({label})")


def capacity():
    url = f"http://127.0.0.1:{os.environ.get('GATEWAY_PORT', '8088')}/v1/capacity"
    token = (demo.STATE / "secrets" / "load_token").read_text().strip()
    request = Request(url, headers={"Authorization": "Bearer " + token})
    with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
        return json.load(response)


def host_paths(runtime):
    paths = {"runtime": str(runtime), "data": str(demo.STATE / "data")}
    prefixes = ("/run/desktop/mnt/host", "")
    names = ["hostpaths-" + uuid.uuid4().hex[:8] for _ in prefixes]
    mounts = [
        {"name": key, "mountPath": path, "readOnly": True}
        for key, path in paths.items()
    ] + [{"name": "docker-socket", "mountPath": "/var/run/docker.sock"}]
    probes = []
    for name, prefix in zip(names, prefixes):
        volumes = [
            {"name": key, "hostPath": {"path": prefix + path, "type": "Directory"}}
            for key, path in paths.items()
        ] + [{"name": "docker-socket", "hostPath": {"path": "/var/run/docker.sock", "type": "Socket"}}]
        probes.append(resource("Pod", name, spec={
            "automountServiceAccountToken": False,
            "restartPolicy": "Never", "activeDeadlineSeconds": 35,
            "containers": [{
                "name": "preflight", "image": demo.IMAGE, "imagePullPolicy": "Never",
                "command": ["sh", "-ec", 'test -r "$1/config/logback.xml"; test -d "$2/work"; docker info --format "{{.ServerVersion}}"',
                            "sh", paths["runtime"], paths["data"]],
                "securityContext": {"runAsUser": 0, "allowPrivilegeEscalation": False},
                "volumeMounts": mounts,
            }], "volumes": volumes,
        }))
    apply(probes)
    try:
        def jobs():
            entries = json.loads(output("get", "pod", *names, "-o", "json"))["items"]
            return {item["metadata"]["name"]: item["status"]["phase"] for item in entries}

        wait_for(lambda: "Succeeded" in jobs().values(), 30, "доступ Pod к локальным файлам и Docker")
        selected = jobs()
        return next(prefix for name, prefix in zip(names, prefixes) if selected[name] == "Succeeded")
    except RuntimeError:
        kubectl("describe", "pod", *names)
        raise
    finally:
        kubectl("delete", "pod", *names, "--ignore-not-found", "--wait=false")


def psql(statement):
    return output("exec", "postgres-0", "--", "psql", "-U", "autocheck", "-d", "autocheck",
                  "-v", "ON_ERROR_STOP=1", "-Atc", statement)


def migrate_compose():
    active = demo.compose("ps", "--status", "running", "-q", "postgres", stdout=subprocess.PIPE, text=True)
    if not active.stdout.strip():
        if not CUTOVER.exists():
            raise ValueError("Compose PostgreSQL остановлена и сохранённого снимка переноса нет")
        demo.compose("down", "--timeout", "60")
        return json.loads(CUTOVER.read_text())

    settings = demo.admission("settings")
    if settings["accepting"]:
        demo.admission("pause")
    try:
        wait_for(lambda: capacity()["occupiedSlots"] == 0, 180, "освобождение слотов Compose")
        backup = demo.backup()
        transfer = {"dump": str(backup), "resume": settings["accepting"]}
        demo.save(CUTOVER, json.dumps(transfer) + "\n", 0o400)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        if settings["accepting"]:
            demo.admission("resume")
        raise
    demo.compose("down", "--timeout", "60")
    return transfer


def manifest(host_prefix="", dns_ip=""):
    image_id = demo.docker_output("image", "inspect", demo.IMAGE, "--format", "{{.Id}}")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise ValueError("Docker не вернул ID локального образа")
    return Manifest(image_refs(), demo.STATE, Path(os.environ["SCRIPT_RUNTIME_ROOT"]), host_prefix, image_id, dns_ip)


def start(from_compose):
    demo.initialize()
    try:
        demo.docker_output("image", "inspect", demo.IMAGE, "--format", "{{.Id}}")
    except subprocess.CalledProcessError:
        demo.build_image()
    demo.ensure_engine_images()
    kubectl("wait", "--for=condition=Ready", "node", "--all", "--timeout=45s")
    ready = output("get", "nodes", "-o", "name").splitlines()
    if len(ready) != 1:
        raise ValueError("Стенд с локальным хранилищем рассчитан на один узел Kubernetes")
    namespace = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE}}
    apply([namespace])
    prefix = host_paths(Path(os.environ["SCRIPT_RUNTIME_ROOT"]))
    dns_ip = output("-n", "kube-system", "get", "service", "kube-dns", "-o", "jsonpath={.spec.clusterIP}")
    deployment = manifest(prefix, dns_ip)
    deployment.database()
    apply(deployment.objects)
    kubectl("rollout", "status", "statefulset/postgres", "--timeout=120s")

    active = demo.compose("ps", "-q", stdout=subprocess.PIPE, text=True).stdout.strip()
    initialized = psql("SELECT to_regclass('public.service_instance') IS NOT NULL") == "t"
    if active and not from_compose:
        raise ValueError("Compose ещё запущен. Для переноса используйте up --from-compose")
    if not initialized and not from_compose:
        old_database = demo.docker_output(
            "volume", "ls", "--filter", f"label=com.docker.compose.project={demo.PROJECT}",
            "--filter", "label=com.docker.compose.volume=postgres_data", "--format", "{{.Name}}",
        )
        if old_database or CUTOVER.exists():
            raise ValueError("Найдены данные Compose. Для переноса используйте up --from-compose")
    transfer = None
    if from_compose:
        if active and initialized:
            raise ValueError("В кластере уже есть база приложения. Исходный Compose не менялся")
        transfer = migrate_compose()
        if not initialized:
            with Path(transfer["dump"]).open("rb") as archive:
                kubectl("exec", "-i", "postgres-0", "--", "pg_restore", "--exit-on-error", "--single-transaction",
                        "--no-owner", "--no-privileges", "-U", "autocheck", "-d", "autocheck", stdin=archive)

    deployment.objects.clear()
    deployment.app(int(os.environ.get("DEMO_API_REPLICAS", "3")), int(os.environ.get("DEMO_WORKER_REPLICAS", "2")))
    migration = [obj for obj in deployment.objects if obj["kind"] != "Deployment" and obj["kind"] != "Service"]
    apply(migration)
    kubectl("wait", "--for=condition=Complete", f"job/{deployment.migration_job}", "--timeout=120s")
    deployment.objects = [obj for obj in deployment.objects if obj not in migration]
    deployment.gateway()
    deployment.monitoring()
    demo.save(demo.KUBERNETES_OWNER, NAMESPACE + "\n", 0o400)
    apply(deployment.objects)
    for name in ("worker", "notifier", "api", "gateway", "prometheus", "grafana", "alertmanager", "blackbox"):
        kubectl("rollout", "status", f"deployment/{name}", "--timeout=120s")
    start_forward()
    wait_for(reachability, 30, "локальный туннель Nginx")
    if transfer and transfer["resume"] and not transfer.get("admission_restored"):
        if not demo.admission("settings")["accepting"]:
            demo.admission("resume")
        transfer["admission_restored"] = True
        demo.save(CUTOVER, json.dumps(transfer) + "\n", 0o400)


def port_arguments():
    return [f"{int(os.environ.get(variable, default))}:{target}" for target, variable, default in PORTS.values()]


def start_forward():
    with FORWARD_LOCK.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        for mapping in port_arguments():
            port = int(mapping.split(":")[0])
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
    log = os.open(FORWARD, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(log, "a") as stream:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "forward"], stdin=subprocess.DEVNULL,
                         stdout=stream, stderr=stream, start_new_session=True)


def forward():
    demo.STATE.mkdir(mode=0o700, exist_ok=True)
    active_process = None

    def stop(_signum, _frame):
        if active_process is not None:
            active_process.terminate()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with FORWARD_LOCK.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        while True:
            active_process = subprocess.Popen(command(
                "port-forward", "--address", "127.0.0.1", "service/gateway", *port_arguments()
            ))
            active_process.wait()
            active_process = None
            time.sleep(2)


def load(profile, rate, duration):
    jobs = manifest()
    name = "k6-" + uuid.uuid4().hex[:8]
    if rate is not None and rate < 1:
        raise ValueError("--rate должен быть положительным целым числом")
    rate = str(rate) if rate is not None else os.environ.get("DEMO_LOAD_RATE", "")
    duration = duration or os.environ.get("DEMO_LOAD_DURATION", "")
    jobs.load_test(name, profile, rate, duration)
    apply(jobs.objects)
    try:
        pod_name = None

        def container_started():
            nonlocal pod_name
            pods = json.loads(output("get", "pods", "-l", f"batch.kubernetes.io/job-name={name}", "-o", "json"))
            for pod in pods["items"]:
                states = pod["status"].get("containerStatuses", ())
                if states and set(states[0]["state"]) & {"running", "terminated"}:
                    pod_name = pod["metadata"]["name"]
                    return True
            return False

        wait_for(container_started, 120, "запуск контейнера k6")
        kubectl("logs", "-f", pod_name)

        def job_finished():
            status = json.loads(output("get", "job", name, "-o", "json"))["status"]
            return any(c["type"] in {"Complete", "Failed"} and c["status"] == "True"
                       for c in status.get("conditions", ()))

        wait_for(job_finished, 15, "завершение k6")
        if output("get", "job", name, "-o", "jsonpath={.status.succeeded}") != "1":
            raise RuntimeError("k6 не прошёл пороги нагрузки")
    finally:
        kubectl("delete", "job", name, "--ignore-not-found", "--wait=false")


def validate():
    kubectl("exec", "deployment/gateway", "--", "nginx", "-t")
    kubectl("exec", "deployment/prometheus", "--", "promtool", "check", "config", "/etc/prometheus/prometheus.yml")
    kubectl("exec", "deployment/alertmanager", "--", "amtool", "check-config", "/etc/alertmanager/alertmanager.yml")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=(
        "up", "status", "forward", "load", "validate", "backup", "settings", "pause", "resume",
    ))
    parser.add_argument("--from-compose", action="store_true")
    parser.add_argument("--profile", choices=("steady", "ramp"), default="steady", help="Профиль нагрузки для load")
    parser.add_argument("--rate", type=int, help="Запросов в секунду для load, пиковое значение для ramp")
    parser.add_argument("--duration", help="Длительность теста steady или каждого этапа ramp, например 30s")
    args = parser.parse_args()
    try:
        configure()
        if args.action == "up":
            demo.STATE.mkdir(mode=0o700, exist_ok=True)
            with (demo.STATE / "deployment.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                start(args.from_compose)
        elif args.action == "status":
            kubectl("get", "deployment,statefulset,job,pod,service,pvc")
        elif args.action == "forward":
            forward()
        elif args.action == "load":
            load(args.profile, args.rate, args.duration)
        elif args.action == "validate":
            validate()
        elif args.action == "backup":
            demo.postgres_backup(lambda *a, **kw: kubectl("exec", "-i", "postgres-0", "--", *a, **kw))
        else:
            demo.admission(args.action)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Kubernetes: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
