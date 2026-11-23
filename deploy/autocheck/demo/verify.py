#!/usr/bin/env python3
"""Exercise the demo gateway, global admission limit, SQL runtime and alerting."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

import httpx
import yaml

from demo import ROOT, STATE, compose, configure, docker


def service_client(port, default, token=None):
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + (STATE / "secrets" / token).read_text().strip()
    return httpx.Client(
        base_url=f"http://127.0.0.1:{int(os.environ.get(port, default))}",
        headers=headers, timeout=10, trust_env=False,
    )


def wait_for(probe, description, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = probe()
        if result:
            return result
        time.sleep(1)
    raise RuntimeError(f"Таймаут: {description}")


def query(prometheus, expr):
    data = prometheus.get("/api/v1/query", params={"query": expr}).raise_for_status().json()
    return [float(series["value"][1]) for series in data["data"]["result"]]


def engine(dump_path):
    dump = dump_path.read_bytes()
    source = ROOT / "assignments" / "poc-integration-environment" / "assignment.yaml"
    assignment = yaml.safe_load(source.read_text())
    criteria = yaml.safe_dump({
        "name": assignment["name"],
        "checks": assignment["checks"][:1],
    }, allow_unicode=True).encode()
    dump_hash = hashlib.sha256(dump).hexdigest()
    criteria_hash = hashlib.sha256(criteria).hexdigest()
    test_id = uuid.uuid4().hex[:12]

    with service_client("GATEWAY_PORT", 8088, "check_token") as api:
        api.get("/health/ready").raise_for_status()
        current = api.get("/v1/capacity").raise_for_status().json()
        if current["occupiedSlots"] or current["availableSlots"] != 2:
            raise RuntimeError("Для сценария нужны два свободных слота на отдельном стенде")

        def reserve(index):
            work = f"demo-{test_id}-{index}"
            return api.post("/v1/reservations", headers={"Idempotency-Key": work}, json={
                "submissionId": work, "studentId": "demo", "assignmentId": "sql-smoke",
                "attempt": 1, "runMode": "integration",
                "dump": {"format": "xdump", "sizeBytes": len(dump), "sha256": dump_hash},
                "criteria": {"format": "yaml", "sizeBytes": len(criteria), "sha256": criteria_hash},
            })

        with ThreadPoolExecutor(max_workers=8) as pool:
            # Keep each future so successful reservations can still be cancelled if a peer fails.
            requests = [pool.submit(reserve, index) for index in range(8)]
        accepted = []
        errors = []
        full = 0
        for pending in requests:
            try:
                response = pending.result()
                if response.status_code == 201:
                    accepted.append(response.json())
                elif response.status_code == 429 and response.json()["error"]["code"] == "capacity_full":
                    full += 1
                else:
                    errors.append(f"HTTP {response.status_code}: {response.text}")
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                errors.append(str(exc))

        unused = {r["reservationId"] for r in accepted}
        try:
            if (len(accepted), full) != (2, 6) or errors:
                raise RuntimeError(f"Ожидались 2 резерва и 6 HTTP 429, получено {len(accepted)}/{full}: {errors}")
            print("Приём под нагрузкой: 2 резерва, 6 ответов HTTP 429.", flush=True)

            for reservation in accepted:
                rid = reservation["reservationId"]
                api.post("/v1/checks", headers={"Idempotency-Key": rid, "X-Reservation-Id": rid}, files={
                    "dump": (dump_path.name, dump),
                    "criteria": ("assignment.yaml", criteria),
                }).raise_for_status()
                unused.remove(rid)

            for reservation in accepted:
                resource = "/v1/checks/" + reservation["checkId"]

                def terminal():
                    value = api.get(resource).raise_for_status().json()
                    return value if value["status"] in {"completed", "rejected", "execution_failed"} else None

                result = wait_for(terminal, resource, timeout=120)
                if result["status"] != "completed" or result["grading"]["status"] != "passed":
                    raise RuntimeError(f"{resource}: {result}")
                artifact = api.get(resource + "/artifacts/grading.json").raise_for_status()
                if artifact.headers["Content-Type"] != "application/json" or artifact.json()["status"] != "passed":
                    raise RuntimeError(f"{resource}: некорректный grading.json")
                print(f"{reservation['checkId']}: SQL прошёл, grading.json получен.", flush=True)
            if api.get("/v1/capacity").raise_for_status().json()["occupiedSlots"]:
                raise RuntimeError("Сервис не освободил все слоты")
            print("После исполнения занято 0 слотов.", flush=True)
        finally:
            for rid in unused:
                try:
                    api.delete("/v1/reservations/" + rid)
                except httpx.HTTPError:
                    pass  # The reservation also has a bounded TTL.


def kubernetes_api_pods():
    from kube import output

    data = output("get", "pods", "-l", "app.kubernetes.io/name=api", "-o", "json")
    return {
        pod["metadata"]["name"] for pod in json.loads(data)["items"]
        if not pod["metadata"].get("deletionTimestamp")
        and any(c["type"] == "Ready" and c["status"] == "True" for c in pod["status"].get("conditions", []))
    }


def failover(kubernetes=False):
    if kubernetes:
        from kube import kubectl, output

        count = int(output("get", "deployment", "api", "-o", "jsonpath={.spec.replicas}"))
        wait_for(lambda: len(kubernetes_api_pods()) == count, "Kubernetes поднял все Pod API")

        def remove():
            kubectl("scale", "deployment/api", "--replicas=1")
            wait_for(lambda: len(kubernetes_api_pods()) == 1, "остался один готовый Pod API")

        def restore():
            kubectl("scale", "deployment/api", f"--replicas={count}")
            wait_for(lambda: len(kubernetes_api_pods()) == count, "Kubernetes восстановил реплики")
    else:
        containers = compose("ps", "--status", "running", "-q", "api", capture_output=True, text=True).stdout.split()
        count = len(containers)

        def remove():
            docker("stop", *containers[1:])

        def restore():
            docker("start", *containers[1:])

    if count < 3:
        raise RuntimeError("Для демонстрации нужны хотя бы три запущенные реплики API")
    with (
        service_client("GATEWAY_PORT", 8088) as api,
        service_client("DEMO_PROMETHEUS_PORT", 9090) as prometheus,
        service_client("DEMO_ALERTMANAGER_PORT", 9093) as alertmanager,
    ):
        up = 'sum(up{job="autocheck-api"})'
        wait_for(lambda: query(prometheus, up) == [count], "Prometheus обнаруживает все API")
        if kubernetes:
            before = kubernetes_api_pods()
            removed = min(before)
            kubectl("delete", "pod", removed, "--wait=false")

            def recreated():
                api.get("/health/ready").raise_for_status()
                ready = kubernetes_api_pods()
                return len(ready) == count and removed not in ready

            wait_for(recreated, "Deployment создал новый Pod API")
            wait_for(lambda: query(prometheus, up) == [count], "Prometheus обнаружил новый Pod")
            print("Kubernetes заменил удалённый Pod API, шлюз оставался доступен на пробах.", flush=True)
        sample_count = 0

        def alert_is_active():
            alerts = alertmanager.get("/api/v2/alerts", params={
                "filter": 'alertname="AutocheckApiRedundancyLost"',
            }).raise_for_status().json()
            return any(alert["status"]["state"] == "active" for alert in alerts)

        try:
            remove()

            def probe():
                nonlocal sample_count
                api.get("/health/ready").raise_for_status()
                sample_count += 1
                return alert_is_active()

            wait_for(probe, "Alertmanager получает сигнал о потере реплик")
            print(f"Одна реплика обслужила {sample_count} последовательных проб HTTP 200. Алерт сработал.", flush=True)
        finally:
            restore()
        wait_for(lambda: query(prometheus, up) == [count], "реплики вернулись в Prometheus")
        wait_for(lambda: not alert_is_active(), "Alertmanager закрыл алерт")
        print("Реплики восстановились, алерт закрыт.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=("engine", "failover"))
    parser.add_argument("--dump", type=Path, default=ROOT / "Dvizhok-dev-final.xdump")
    parser.add_argument("--kubernetes", action="store_true", help="Use the local Kubernetes Deployment for failover")
    args = parser.parse_args()
    if args.scenario == "engine":
        engine(args.dump)
    else:
        if args.kubernetes:
            from kube import configure as configure_kubernetes

            configure_kubernetes()
        else:
            configure()
        failover(args.kubernetes)


if __name__ == "__main__":
    main()
