# Развёртывание автопроверки

Реализован сервис `element_check_service`: PostgreSQL-миграция, HTTP API, worker,
notifier, Docker supervisor, health и метрики. Очередь остаётся в ЛК. Сервис запускает
существующий движок; баллы определяет SBSL. API, callback и артефакты результатов —
JSON; ЛК отвечает за отображение, HTML сервис не создаёт. Kubernetes для этой версии не требуется.

Контракты: [архитектура](../../docs/autocheck-microservice-design.md),
[OpenAPI](../../docs/autocheck-openapi.yaml),
[webhook](../../docs/autocheck-webhook-openapi.yaml),
[приёмка](../../docs/autocheck-review-checklist.md).

## Локальный демонстрационный стенд

[Инструкция для стенда](demo/README.md) описывает запуск на одной машине,
балансировку трёх реплик API, два воркера, тестовую нагрузку, Grafana и проверку алертов.

## Сборка и проверка

Из корня репозитория:

```bash
python -m pip install -r deploy/autocheck/requirements.lock
python -m unittest tests.test_autocheck_service -v

docker build --build-arg ENGINE_REVISION="$(git rev-parse HEAD)" \
  -t element-checker/service:local -f deploy/autocheck/Dockerfile .

docker compose --env-file deploy/autocheck/.env.example \
  -f deploy/autocheck/compose.yaml config --quiet
```

Python сервиса — 3.12+. Для тестов с настоящими транзакциями задайте
`TEST_AUTOCHECK_DSN` отдельного тестового PostgreSQL. Тесты создают случайную схему и
удаляют только её. `TEST_AUTOCHECK_ENGINE=1` дополнительно включает настоящий движок
и параллельные SQL-проверки двух конфигураций; нужны runtime, Docker и входные дампы.
Без соответствующих переменных эти проверки явно пропускаются.

Полный тест временной Compose-установки с реальным SQL и HTTP API:

```bash
python tools/ci/autocheck_smoke.py --runtime /absolute/path/to/script-runtime
```

Он создаёт отдельный случайный Compose project, тестовые секреты и localhost-порт,
проверяет readiness, reserve/upload, SQL, JSON-артефакт и освобождение слота, затем удаляет
только собственные контейнеры и тестовый volume. Callback указывает на недоступный
локальный endpoint; реальных внешних получателей у этого теста нет. Доставку и HMAC
проверяют отдельные service-тесты с контролируемым HTTP transport.

## Установка на Linux

1. Выделенный Linux Docker-host, Docker Engine и Compose. Несколько worker на одном
   хосте используют общий лимит PostgreSQL. Docker Desktop годится для локального
   smoke-теста; эксплуатационный целевой вариант — Linux.
2. Соберите образ, сохраните git revision и опубликуйте его в своём registry.
   В `SERVICE_IMAGE` используйте immutable `repository@sha256:...`, в
   `ENGINE_IMAGE_DIGEST` — настоящий digest этого образа. Без build revision/digest
   worker не допускает новые проверки. Локальный smoke использует фактический image ID.
3. Разместите Script executor 10.0.2-1 с `lib/` и `config/` по абсолютному пути,
   доступному на чтение UID 65534. Runtime не скачивается при каждой проверке.
   Для SQL обязательны JDBC и SQL API jars.
4. Создайте `SERVICE_DATA_ROOT` и `staging`, `checks`, `work` на одном filesystem:
   владелец 10001:10001, каталоги 2770. API — UID/GID 10001, worker — 0:10001,
   umask сервиса 0007. Data и runtime пути должны совпадать абсолютно на хосте и в
   worker: Docker daemon разрешает bind source на хосте.
5. Создайте `.env` по `.env.example`, задайте реальные пути. Закрепите Nginx digest.
   Создайте секреты вне Git по [форматам](secrets/README.md). UID 10001 должен читать
   API/DB/webhook secret files. В Compose secrets — файловые bind mounts, не vault.
6. Загрузите заранее образы из `config/runtimes.json` и `config/integration.json`
   по digest. Проверки используют `--pull never`.
7. Проверьте конфигурацию, затем запустите:

```bash
docker compose --env-file deploy/autocheck/.env \
  -f deploy/autocheck/compose.yaml up -d --wait
```

Состав: gateway, API, worker, notifier, migrate, PostgreSQL. Gateway слушает только
127.0.0.1:8088; внешний reverse proxy завершает TLS. Если он в другом контейнере,
настройте ingress/host gateway явно. БД управления не используется тестами:
каждая SQL-проверка получает свои временные PostgreSQL, сеть и executor.

Начальный лимит — 2, hardLimit — 8. Это верхняя граница настройки, а не измеренная
производительность. CPU/RAM worker не ограничивают созданные им соседние контейнеры.
Резервируйте ресурсы хоста под весь разрешённый fan-out и управляющие процессы.
Увеличивайте лимит после измерений своих работ.

## Совместное размещение с другим сервисом

Для Linux с cgroup v2 и systemd есть `compose.shared-host.yaml` и `autocheck.slice`.
Slice ограничивает **весь** новый стек одним CPU и 2 GiB RAM, имеет пониженный
CPU/IO weight и не затрагивает контейнеры вне slice. `MemoryHigh` — 1792 MiB,
swap запрещён. Одновременное исполнение ограничено одной работой.
Таймаут runtime по умолчанию — 30 секунд вместо 5: на ограниченном CPU нужно
учитывать холодный запуск JVM и компиляцию SBSL. Явный `timeout` критерия имеет
приоритет; максимальное значение движка остаётся 30 секунд.
Профиль задаёт отдельные меньшие лимиты API, worker, notifier и управляющей БД.
Worker передаёт `AUTOCHECK_CGROUP_PARENT` всем создаваемым executor/SQL-контейнерам;
одних Compose-лимитов worker для ограничения этих соседних контейнеров недостаточно.

Установите unit, затем добавьте override к каждой команде Compose:

```bash
sudo install -m 0644 deploy/autocheck/autocheck.slice /etc/systemd/system/autocheck.slice
sudo systemctl daemon-reload
sudo systemctl enable --now autocheck.slice
docker compose --env-file deploy/autocheck/.env \
  -f deploy/autocheck/compose.yaml -f deploy/autocheck/compose.shared-host.yaml config --quiet
```

В `.env` установите обе переменные `DEFAULT_MAX_CONCURRENT_CHECKS=1` и
`MAX_CONCURRENT_CHECKS_HARD_LIMIT=1`. Общий лимит не является резервированием памяти:
фактический запас хоста и пиковое потребление нужно измерять на своих работах.
При нехватке ресурсов отказывает новый стек, а не снимаются его ограничения.

Для локального тестирования без webhook установите в активном `service.yaml`
`delivery.enabled: false`. Notifier продолжает heartbeat и регистрацию клиентов,
но не забирает события из outbox: `attempt_count` остаётся 0. Результаты доступны
через API, события сохраняются до настройки настоящего HTTPS-получателя.
Настройки backlog и retention продолжают действовать. При последующем включении
отправки учитывайте уже накопленные тестовые события и срок их доставки.

`tools/ci/autocheck_host_verify.py` проверяет установленный стек на сервере:
резервирование, лимит одного слота, upload, реальное исполнение и JSON-артефакты.
Он измеряет память slice, отслеживает cgroup временных контейнеров и сравнивает
ID, время запуска, restart count и хеш конфигурации существующего Moodle-контейнера.
HTTP-пробы существующего сервиса выполняют только GET; это проверка доступности,
не запуск выставления реальных оценок. Критерии передаются отдельным JSON-файлом.

## Управление

Все команды исполняются одним образом:

| Команда | Назначение |
|---|---|
| `python -m element_check_service migrate` | Транзакционная идемпотентная миграция, начальные settings/instance UUID |
| `python -m element_check_service api --host 0.0.0.0 --port 8000` | HTTP API, ранний допуск до чтения multipart |
| `python -m element_check_service worker` | Исполнение, fencing, cleanup/recovery, retention |
| `python -m element_check_service notifier` | Реестр получателей, durable outbox, HMAC, retries |
| `python -m element_check_service health api` | Readiness API |
| `python -m element_check_service health worker` | Heartbeat именно текущего worker |
| `python -m element_check_service health notifier` | Heartbeat именно текущего notifier |

`GET /metrics` требует `settings:read`; метрики не содержат student/submission IDs.
Логи запросов — JSON с requestId, шаблоном маршрута, статусом и длительностью;
Authorization, тела и query strings не логируются. Технический ограниченный журнал
процесса остаётся только в серверном каталоге и через API не выдаётся.

Лимит и pause меняются `PATCH /v1/admin/settings` с `If-Match` из ETag GET.
Снижение лимита не прерывает работы и не отзывает резервации. Pause останавливает
новые reserve. Для обслуживания: pause, дождаться `occupiedSlots=0`, затем stop.

Обновление: pause/drain, backup БД + data, остановить приложения, обновить image,
выполнить `docker compose run --rm migrate`, затем `up -d --force-recreate`.
Завершённый migrate сам не гарантирует запуск новой миграции при обычном `up`.
Несовместимая schema revision вызывает отказ запуска; автоматического downgrade нет.
Не используйте `down -v` для штатного stop: он удаляет рабочую БД.

## Отказы и восстановление

После потери worker оценивание автоматически не повторяется. Supervisor завершает
process group, затем очищает только Docker-ресурсы с собственными instance/check/fence
labels. Все операции идут через приватный CLI proxy, проверяющий действующую lease;
намерение create сохраняется до обращения к daemon. Executor не получает proxy,
Docker socket, credentials управляющей БД или callback-секреты.

При пропавшем supervisor recovery требует подтверждение остановки дочерних процессов
или остановленного/перезапущенного worker-контейнера. Если живой старый supervisor
завис, cleanup не доказан или create имеет неопределённый исход без обнаруженного
ресурса, работа остаётся finalizing/cleanupBlocked, слот занят, новый приём закрыт.
Проверьте worker/daemon, остановите зависший старый worker и восстановите Docker.
Не освобождайте claim вручную, пока не доказано отсутствие процесса и незавершённого
create. Неопределённый create без ресурса требует операторского расследования;
сервис намеренно не превращает отсутствие первого результата inspect в доказательство.

Доставка независима от оценки: ошибки callback не занимают slot и не запускают
движок снова. GET check позволяет сверить результат. Manual delivery retry сохраняет
исходные eventId/body; после удаления payload возвращает 410. Retention: артефакты
30 дней, до 37 при недоставленном результате; входы/приватные файлы до 37 дней,
metadata/grade и idempotency 180 дней. Для согласованного backup проще pause/drain.

Перед эксплуатацией обязательны реальные секреты и URL ЛК, TLS proxy, backup/restore,
проверка аварийных сценариев на целевом Linux-хосте и нагрузочные измерения. Локальные
проверки не подтверждают производительность и отказоустойчивость инфраструктуры ЛК.

## HTTPS по IP и webhook в запросе

Пример адреса сервиса — `https://autocheck.example.com/autocheck`. Инструкция подключения и регистрации
независимых систем: [autocheck-integration.md](../../docs/autocheck-integration.md).
Адрес webhook передаётся в `webhookUrl` запроса reservation, HMAC-ключ остаётся
привязан к clientId. Внешний Nginx подключает `nginx-host-location.conf` внутри
существующего TLS server, сохраняя его прежний location `/`. Gateway остаётся
на 127.0.0.1:8088. Настройте действующий TLS-сертификат для адреса сервиса и его автоматическое продление.
