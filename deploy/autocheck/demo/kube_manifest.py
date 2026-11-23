"""Kubernetes resources for the single-node Docker Desktop demonstration."""

import base64
import hashlib
import json

from demo import BASE, DEMO

NAMESPACE = "element-autocheck-demo"
MONITORING = DEMO / "monitoring"
PORTS = {
    "gateway": (8080, "GATEWAY_PORT", "8088"),
    "grafana": (3000, "DEMO_GRAFANA_PORT", "3000"),
    "prometheus": (9090, "DEMO_PROMETHEUS_PORT", "9090"),
    "alertmanager": (9093, "DEMO_ALERTMANAGER_PORT", "9093"),
}


def resource(kind, name, api="v1", **fields):
    return {
        "apiVersion": api, "kind": kind,
        "metadata": {"name": name, "namespace": NAMESPACE},
        **fields,
    }


def labels(name):
    return {"app.kubernetes.io/name": name, "app.kubernetes.io/part-of": "autocheck"}


def service(name, ports, *, component=None, headless=False):
    spec = {
        "selector": labels(component or name),
        "ports": [{"name": key, "port": port, "targetPort": port} for key, port in ports.items()],
    }
    if headless:
        spec["clusterIP"] = "None"
    return resource("Service", name, spec=spec)


def claim(name, size):
    return resource("PersistentVolumeClaim", name, spec={
        "accessModes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": size}},
    })


def http(path, port):
    return {"httpGet": {"path": path, "port": port}, "periodSeconds": 5, "timeoutSeconds": 3}


def executing(*command):
    return {"exec": {"command": command}, "periodSeconds": 5, "timeoutSeconds": 5}


class Manifest:
    def __init__(self, images, state, runtime, host_prefix, image_id, dns_ip):
        self.images = images
        self.state = state
        self.runtime = runtime
        self.host_prefix = host_prefix
        self.image_id = image_id
        self.dns_ip = dns_ip
        self.objects = []
        self.storage = {}
        self.versions = {}
        self.db_env = {
            "DB_HOST": "postgres",
            "DB_PASSWORD_FILE": "/run/secrets/autocheck/db_password",
            "SERVICE_CONFIG": "/etc/autocheck/service.yaml",
            "DEFAULT_MAX_CONCURRENT_CHECKS": "2",
            "MAX_CONCURRENT_CHECKS_HARD_LIMIT": "2",
        }

    def put(self, obj):
        self.objects.append(obj)

    def files(self, name, data, *, secret=False):
        kind = "Secret" if secret else "ConfigMap"
        stored = data
        if secret:
            stored = {key: base64.b64encode(value.encode()).decode() for key, value in data.items()}
        self.put(resource(kind, name, data=stored))
        source = {"secretName" if secret else "name": name, "defaultMode": 0o444}
        self.storage[name] = {"secret" if secret else "configMap": source}
        self.versions[name] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def secret(self, name, keys):
        directory = self.state / "secrets"
        self.files(name, {key: (directory / filename).read_text() for key, filename in keys.items()}, secret=True)

    def workload(self, name, *, env=None, command=None, args=None, mounts=(), ports=(),
                 readiness=None, liveness=None, replicas=1, user=None, group=None,
                 fs_group=None, default_limits=("500m", "256Mi"), grace=30,
                 extra_caps=(), read_only=True, job_name=None, stateful=False):
        cpu, memory = default_limits
        security = {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": read_only,
            "capabilities": {"drop": ["ALL"], "add": list(extra_caps)},
        }
        if user is not None:
            security["runAsUser"] = user
        if group is not None:
            security["runAsGroup"] = group
        # The upstream PostgreSQL entrypoint initializes the volume and drops to postgres.
        if name == "postgres":
            security.pop("capabilities")

        used = {key for key, *_ in mounts}
        volumes = [{"name": key, **self.storage[key]} for key in sorted(used)]
        volume_mounts = []
        for key, target, *parts in mounts:
            mount = {"name": key, "mountPath": str(target)}
            if parts:
                mount.update(subPath=parts[0], readOnly=True)
            elif key in {"runtime"} or "secret" in self.storage[key] or "configMap" in self.storage[key]:
                mount["readOnly"] = True
            volume_mounts.append(mount)

        container = {
            "name": name, "image": self.images[name],
            "imagePullPolicy": "Never" if name in {"api", "worker", "notifier", "migrate"} else "IfNotPresent",
            "securityContext": security,
            "ports": [{"containerPort": p} for p in ports],
            "env": [{"name": key, "value": value} for key, value in (env or {}).items()],
            "volumeMounts": volume_mounts,
            "resources": {
                "requests": {"cpu": "100m", "memory": "96Mi"},
                "limits": {"cpu": cpu, "memory": memory},
            },
        }
        for key, value in {"command": command, "args": args, "readinessProbe": readiness, "livenessProbe": liveness}.items():
            if value is not None:
                container[key] = value
        if name == "api":
            # Let Service endpoints drain before Uvicorn receives SIGTERM.
            container["lifecycle"] = {"preStop": {"sleep": {"seconds": 5}}}
        pod = {
            "automountServiceAccountToken": False,
            "restartPolicy": "Never" if job_name else "Always",
            "terminationGracePeriodSeconds": grace,
            "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [container],
            "volumes": volumes,
        }
        if fs_group is not None:
            pod["securityContext"].update(fsGroup=fs_group, fsGroupChangePolicy="OnRootMismatch")
        template = {"metadata": {"labels": labels(name), "annotations": {
            "autocheck.demo/config-hash": hashlib.sha256(
                "".join(self.versions[key] for key in sorted(used) if key in self.versions).encode()
            ).hexdigest(),
        }}, "spec": pod}
        if job_name:
            self.put(resource("Job", job_name, "batch/v1", spec={
                "template": template, "backoffLimit": 0 if name == "k6" else 1,
            }))
            return
        spec = {"replicas": replicas, "selector": {"matchLabels": labels(name)}, "template": template}
        if stateful:
            spec["serviceName"] = name
        else:
            spec["strategy"] = {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1}}
            if name in {"prometheus", "alertmanager", "grafana"}:
                spec["strategy"] = {"type": "Recreate"}
        self.put(resource("StatefulSet" if stateful else "Deployment", name, "apps/v1", spec=spec))

    def database(self):
        self.secret("pg-secret", {"db_password": "db_password"})
        self.storage["pg-data"] = {"persistentVolumeClaim": {"claimName": "pg-data"}}
        self.put(claim("pg-data", "2Gi"))
        self.put(service("postgres", {"postgres": 5432}))
        self.workload("postgres", stateful=True, read_only=False, ports=(5432,),
                      mounts=(("pg-data", "/var/lib/postgresql/data"), ("pg-secret", "/run/secrets/autocheck")),
                      env={"POSTGRES_DB": "autocheck", "POSTGRES_USER": "autocheck",
                           "POSTGRES_PASSWORD_FILE": self.db_env["DB_PASSWORD_FILE"],
                           "PGDATA": "/var/lib/postgresql/data/pgdata"},
                      readiness=executing("pg_isready", "-h", "127.0.0.1", "-U", "autocheck", "-d", "autocheck"),
                      default_limits=("1000m", "768Mi"))

    def app(self, api_count, worker_count):
        self.files("service-config", {"service.yaml": (BASE / "service.yaml").read_text()})
        self.files("notifier-config", {"service.yaml": (self.state / "notifier.yaml").read_text()})
        self.secret("api-secret", {"api_clients": "api_clients.json"})
        self.secret("worker-secret", {"integration_password": "integration_password"})
        self.secret("notifier-secret", {"webhook_clients": "webhook_clients.json"})
        data = self.state / "data"
        for key, path in {"data": data, "runtime": self.runtime}.items():
            self.storage[key] = {"hostPath": {"path": self.host_prefix + str(path), "type": "Directory"}}
        self.storage["docker-socket"] = {"hostPath": {"path": "/var/run/docker.sock", "type": "Socket"}}
        self.storage["tmp"] = {"emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}}
        base = (("tmp", "/tmp"), ("pg-secret", "/run/secrets/autocheck"))
        app = (*base, ("service-config", "/etc/autocheck"))
        self.workload("migrate", args=("python", "-m", "element_check_service", "migrate"),
                      env=self.db_env, mounts=app, user=10001, group=10001,
                      job_name=self.migration_job)
        self.put(service("api", {"http": 8000}, headless=True))
        self.put(service("api-http", {"http": 8000}, component="api"))
        self.workload("api", args=("python", "-m", "element_check_service", "api", "--host", "0.0.0.0", "--port", "8000"),
                      ports=(8000,), replicas=api_count, user=10001, group=10001,
                      env={**self.db_env, "SERVICE_DATA_ROOT": str(data), "API_CLIENTS_FILE": "/run/secrets/api/api_clients"},
                      mounts=(*app, ("data", data), ("api-secret", "/run/secrets/api")),
                      readiness=http("/health/ready", 8000), liveness=http("/health/live", 8000),
                      default_limits=("750m", "512Mi"))
        self.workload("worker", args=("python", "-m", "element_check_service", "worker"),
                      replicas=worker_count, user=0, group=10001, grace=60,
                      extra_caps=("DAC_OVERRIDE", "FOWNER", "KILL"),
                      env={**self.db_env, "SERVICE_DATA_ROOT": str(data), "TMPDIR": str(data / "work"),
                           "ELEMENT_SCRIPT_HOME": str(self.runtime),
                           "INTEGRATION_PASSWORD_FILE": "/run/secrets/worker/integration_password",
                           "DOCKER_HOST": "unix:///var/run/docker.sock", "ENGINE_IMAGE_DIGEST": self.image_id,
                           "ELEMENT_TEST_RUNTIME_TIMEOUT": "30s"},
                      mounts=(*app, ("data", data), ("runtime", self.runtime),
                              ("worker-secret", "/run/secrets/worker"), ("docker-socket", "/var/run/docker.sock")),
                      readiness=executing("python", "-m", "element_check_service", "health", "worker"),
                      default_limits=("2000m", "2Gi"))
        self.workload("notifier", args=("python", "-m", "element_check_service", "notifier"),
                      user=10001, group=10001,
                      env={**self.db_env, "WEBHOOK_CLIENTS_FILE": "/run/secrets/notifier/webhook_clients"},
                      mounts=(*base, ("notifier-config", "/etc/autocheck"), ("notifier-secret", "/run/secrets/notifier")),
                      readiness=executing("python", "-m", "element_check_service", "health", "notifier"))

    @property
    def migration_job(self):
        return "migrate-" + self.image_id.removeprefix("sha256:")[:12]

    def gateway(self):
        nginx = (BASE / "nginx.conf").read_text().replace("127.0.0.11", self.dns_ip)
        nginx = nginx.replace("server api:8000 resolve;", f"server api-http.{NAMESPACE}.svc.cluster.local:8000 resolve;")
        # Use a fresh TCP connection so the Service can distribute each request.
        nginx = nginx.replace("        keepalive 16;", "        keepalive 0;")
        proxy = []
        # The host uses one loopback-only port-forward for all demo interfaces.
        for name in ("grafana", "prometheus", "alertmanager"):
            port = PORTS[name][0]
            proxy.append(f"""
    server {{
        listen {port};
        location / {{
            resolver {self.dns_ip} valid=5s ipv6=off;
            set $backend http://{name}.{NAMESPACE}.svc.cluster.local:{port};
            proxy_pass $backend;
            proxy_set_header Host $http_host;
        }}
    }}
""")
        nginx = nginx.rstrip().removesuffix("}") + "".join(proxy) + "}\n"
        self.files("nginx", {"nginx.conf": nginx})
        ports = {name: entry[0] for name, entry in PORTS.items()}
        self.put(service("gateway", ports))
        self.workload("gateway", command=("nginx",), args=("-g", "daemon off;"),
                      ports=tuple(ports.values()), user=101, group=101,
                      mounts=(("nginx", "/etc/nginx/nginx.conf", "nginx.conf"), ("tmp", "/tmp")),
                      readiness=http("/health/ready", 8080), liveness=http("/gateway-health", 8080),
                      default_limits=("500m", "128Mi"))

    def monitoring(self):
        config = (MONITORING / "prometheus.yml").read_text().replace(
            "names: [api]", f"names: [api.{NAMESPACE}.svc.cluster.local]"
        )
        self.files("prometheus-config", {"prometheus.yml": config, "alerts.yml": (MONITORING / "alerts.yml").read_text()})
        self.secret("metrics-token", {"demo_metrics_token": "metrics_token"})
        self.files("blackbox-config", {"config.yml": (MONITORING / "blackbox.yml").read_text()})
        self.files("alertmanager-config", {"alertmanager.yml": (MONITORING / "alertmanager.yml").read_text()})
        grafana = MONITORING / "grafana"
        self.files("grafana-providers", {
            "dashboards.yml": (grafana / "provisioning/dashboards/autocheck.yml").read_text(),
            "datasources.yml": (grafana / "provisioning/datasources/prometheus.yml").read_text(),
        })
        self.files("grafana-dashboards", {"autocheck.json": (grafana / "dashboards/autocheck.json").read_text()})
        self.secret("grafana-secret", {"grafana_password": "grafana_password"})
        for name, port in {"prometheus": 9090, "alertmanager": 9093, "grafana": 3000, "blackbox": 9115}.items():
            self.put(service(name, {"http": port}))
            if name != "blackbox":
                key = name + "-data"
                self.storage[key] = {"persistentVolumeClaim": {"claimName": key}}
                self.put(claim(key, "2Gi" if name == "prometheus" else "1Gi"))

        self.workload("blackbox", args=("--config.file=/etc/blackbox/config.yml",), ports=(9115,),
                      mounts=(("blackbox-config", "/etc/blackbox"),), readiness=http("/", 9115),
                      default_limits=("250m", "128Mi"))
        self.workload("prometheus", ports=(9090,), fs_group=65534, args=(
            "--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/prometheus",
            "--storage.tsdb.retention.time=7d", "--storage.tsdb.retention.size=1GB",
            "--query.lookback-delta=20s",
        ), mounts=(("prometheus-config", "/etc/prometheus"), ("prometheus-data", "/prometheus"),
                   ("metrics-token", "/run/secrets")), readiness=http("/-/ready", 9090),
                      default_limits=("500m", "512Mi"))
        self.workload("alertmanager", ports=(9093,), fs_group=65534, args=(
            "--config.file=/etc/alertmanager/alertmanager.yml", "--storage.path=/alertmanager",
            "--cluster.listen-address=",
        ), mounts=(("alertmanager-config", "/etc/alertmanager"), ("alertmanager-data", "/alertmanager")),
                      readiness=http("/-/ready", 9093), default_limits=("250m", "128Mi"))
        self.workload("grafana", ports=(3000,), user=472, group=472, fs_group=472,
                      mounts=(("grafana-providers", "/etc/grafana/provisioning/datasources/autocheck.yml", "datasources.yml"),
                              ("grafana-providers", "/etc/grafana/provisioning/dashboards/autocheck.yml", "dashboards.yml"),
                              ("grafana-dashboards", "/var/lib/grafana/dashboards"),
                              ("grafana-secret", "/run/secrets/grafana"), ("grafana-data", "/var/lib/grafana"),
                              ("tmp", "/tmp")),
                      env={"GF_SECURITY_ADMIN_PASSWORD__FILE": "/run/secrets/grafana/grafana_password",
                           "GF_AUTH_ANONYMOUS_ENABLED": "true", "GF_AUTH_ANONYMOUS_ORG_ROLE": "Viewer",
                           "GF_USERS_ALLOW_SIGN_UP": "false", "GF_ANALYTICS_REPORTING_ENABLED": "false",
                           "GF_ANALYTICS_CHECK_FOR_UPDATES": "false", "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES": "false",
                           "GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH": "/var/lib/grafana/dashboards/autocheck.json"},
                      readiness=http("/api/health", 3000), default_limits=("500m", "384Mi"))

    def load_test(self, name, profile, rate, duration):
        self.files("k6-script", {"scenario.js": (DEMO / "load.js").read_text()})
        self.secret("k6-token", {"demo_load_token": "load_token"})
        self.workload("k6", job_name=name, args=("run", "--no-usage-report", "/opt/k6/scenario.js"),
                      mounts=(("k6-script", "/opt/k6"), ("k6-token", "/run/secrets")),
                      env={"DEMO_LOAD_PROFILE": profile, "DEMO_LOAD_RATE": rate, "DEMO_LOAD_DURATION": duration})
