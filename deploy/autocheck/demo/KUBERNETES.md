# Стенд автопроверки в Kubernetes

Стенд использует встроенный одноузловой кластер Docker Desktop, профиль
`kubeadm`, контекст `docker-desktop` и namespace `element-autocheck-demo`.
На macOS кластер работает в служебной Linux-VM Docker Desktop. Дистрибутив
1С:Элемент Скрипт берётся из `script_u_10.0.2_1` или из `SCRIPT_RUNTIME_ROOT`.
На новом Mac включите кластер в Docker Desktop через **Kubernetes → Create
cluster → Kubeadm**.

## Запуск и контроль

```bash
python3 deploy/autocheck/demo/kube.py up
python3 deploy/autocheck/demo/kube.py status
python3 deploy/autocheck/demo/kube.py validate
```

Для первого переноса с работающего Compose используйте `up --from-compose`.
Скрипт проверяет монтирование данных, рантайма и Docker-сокета, закрывает приём
работ, дожидается освобождения слотов и сохраняет дамп PostgreSQL. Затем
останавливает и удаляет контейнеры и сети Compose с сохранением томов, переносит
дамп в пустой Kubernetes PVC и запускает миграцию. Артефакты проверок доступны
из прежнего локального каталога. Старые метрики и настройки Grafana остаются в
томах Compose. Новый мониторинг собирает данные заново и загружает
дашборд из репозитория.

Шлюз Nginx передаёт запросы в Kubernetes Service, который выбирает готовые Pod API.
Prometheus обнаруживает отдельные реплики через headless Service.
PostgreSQL работает как StatefulSet, миграция как Job. Kubernetes поддерживает
реплики API и воркеров и выполняет rolling update. Prometheus, Grafana и
Alertmanager хранят данные в отдельных PVC. Внешняя webhook-доставка на стенде
отключена.

При `up` скрипт запускает локальный `kubectl port-forward` с переподключением
после пересоздания шлюза. Адреса доступны на этом Mac.

- [API — проверка готовности](http://127.0.0.1:8088/health/ready)
- [Grafana — дашборд](http://127.0.0.1:3000/d/autocheck-demo/autocheck-demo)
- [Prometheus — цели сбора метрик](http://127.0.0.1:9090/targets)
- [Alertmanager — активные алерты](http://127.0.0.1:9093/)

## Масштабирование и проверки

```bash
kubectl --context docker-desktop -n element-autocheck-demo scale deployment/api --replicas=4
kubectl --context docker-desktop -n element-autocheck-demo scale deployment/worker --replicas=3
```

При следующем `up` число реплик определяется `DEMO_API_REPLICAS` и
`DEMO_WORKER_REPLICAS`, по умолчанию 3 и 2. Чтобы уменьшить число воркеров без
прерывания работ, выполните `kube.py pause`, дождитесь нуля занятых слотов
в Grafana и после масштабирования выполните `kube.py resume`. Лимит в две
одновременные проверки действует на все воркеры вместе.

```bash
python3 deploy/autocheck/demo/kube.py load
python3 deploy/autocheck/demo/kube.py load --profile ramp --rate 60 --duration 30s
.venv/bin/python deploy/autocheck/demo/verify.py engine
.venv/bin/python deploy/autocheck/demo/verify.py failover --kubernetes
python3 deploy/autocheck/demo/kube.py backup
```

Нагрузка k6 выполняется как временный Job и отправляет `GET /v1/capacity`
через Nginx. Профиль `steady` по умолчанию держит 15 запросов в секунду
45 секунд. Профиль `ramp` из примера повышает нагрузку с 5 до 20, 40 и 60
запросов в секунду, держит пик 30 секунд и снижает нагрузку до нуля. Пять
этапов по 30 секунд занимают 2 минуты 30 секунд. k6 показывает задержку p95,
долю HTTP-ошибок и число пропущенных итераций. В Grafana видно, как запросы
распределяются по репликам API. Реальные работы отправляет сценарий `verify.py engine`.

Сценарий отказа проверяет создание
нового Pod после удаления одного, доступность API на оставшейся реплике и
срабатывание и закрытие алерта. `backup` сохраняет логический дамп базы
в `.state/backups`. Файлы артефактов хранятся в `.state/data`.

Воркеры по-прежнему создают временные контейнеры SQL-проверки через Docker.
Поэтому им нужны локальный Docker-сокет и тот же путь к файлам, который видит
Docker Engine. При аварийном исчезновении воркера во время работы сервис
удерживает слот, если не может подтвердить остановку процесса и очистку ресурсов.
Хост, Nginx и PostgreSQL остаются единичными точками отказа.
