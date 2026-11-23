# Реализация микросервиса автопроверки

15 октября 2026. Реализован single-host сервис на Docker Compose. Kubernetes не
используется. Сервис подключает существующий Element Test Engine; баллы, статусы
критериев и итоговую оценку вычисляет SBSL.

## JSON-контракт для ЛК

Основной результат — `GET /v1/checks/{checkId}` с `Content-Type: application/json`.
Ответ содержит identity работы, состояние, времена, входные хеши, engine provenance,
`normalizedCriteriaSha256`, `grading`, `error`, состояние доставки и JSON-артефакты.

`grading.feedback` содержит `criterionId`, статус, баллы, `expected`, `actual`,
`message`, при наличии `group`, `reasonCode`, `failureExplanation`. ЛК самостоятельно
строит таблицы, группы, пояснения и другие элементы интерфейса из этих полей.
Сервис не генерирует и не выдаёт HTML. Webhook также передаёт JSON.

Доступные дополнительные JSON-артефакты:

- `grading.json` — пакет оценки v1;
- `result.json` — результат движка с очищенной диагностикой;
- `execution-plans.json` — технические планы подготовки проверок;
- `runtime-evidence.json` — факты исполнения.

Они выдаются как JSON attachments с `nosniff`; входной дамп и скрытые критерии через
API недоступны. Ошибки API, включая неизвестный маршрут и запрет HTML-артефакта,
возвращаются в JSON. Gateway также возвращает JSON при превышении размера запроса
и инфраструктурных HTTP-ошибках. `/metrics` — отдельный служебный Prometheus endpoint.

Опция `render_html=False` в bridge отключает генерацию отчёта для сервиса. Обычный
CLI/batch сохраняет прежнее поведение по умолчанию.

## Реализованные компоненты

| Компонент | Файл | Поведение |
|---|---|---|
| API | `element_check_service/api.py` | Reserve, upload, GET/list checks, JSON artifacts, capacity, settings/ETag, delivery retry, health, metrics |
| Транзакции | `element_check_service/db.py` | Общий cap под singleton lock, idempotency, claim/lease/fence, terminal + release + outbox в одной транзакции |
| Миграция | `element_check_service/migrations/001.sql` | Таблицы, ограничения, индексы; settings и instance UUID сохраняются при повторе |
| Загрузка | `element_check_service/uploads.py` | Авторизация/резервация до тела; multipart блоками, размеры/SHA-256, fsync/rename/commit, lock живого writer |
| Входы | `element_check_service/inputs.py` | Ограниченные ZIP/YAML, запрет ссылок/выхода из snapshot, проверка formRequirements и количества checks |
| Worker | `element_check_service/worker.py` | Отдельная process group, filtered env, общий timeout, heartbeat, recovery без повторного оценивания, retention |
| Docker | `element_check_service/docker_control.py`, `docker_proxy.py` | Private CLI proxy, lease/advisory lock, intent перед create, labels, одна активная роль executor/database/network на check, cleanup |
| Движок | `element_check_service/engine_child.py` | Валидация дампа и критериев, настоящий bridge/SBSL, JSON Schema grading, JSON output |
| Доставка | `element_check_service/notifier.py` | Outbox leases, подпись точных сохранённых байтов, retries/full jitter, webhookUrl из manifest, публичный HTTPS с DNS pinning, без redirects |
| Авторизация | `element_check_service/auth.py` | SHA-256 токенов, constant-time comparison, scopes, reload с сохранением последнего валидного registry |
| Эксплуатация | `deploy/autocheck/` | Проверенный Dockerfile, pinned базовые образы и Python dependencies, Compose, Nginx, команды/секреты |

Управляющая PostgreSQL хранит состояние сервиса. SQL-проверки двух конфигураций
используют отдельные временные PostgreSQL, executor и внутренние сети; общие имена
таблиц и одинаковые UUID не смешивают данные.

## Выполненные проверки

- 38 service-тестов с настоящим PostgreSQL; в том числе четыре opt-in теста с
  настоящим движком/Docker. Оценки не подменялись фиктивными результатами.
- 100 конкурентных reserve при cap=4: ровно 4 приняты, 96 ответов 429.
- Потоковый chunked upload 20 MiB: пик Python allocations ниже 8 MiB. Это замер
  приложения через ASGI, не полный RSS-профиль инфраструктуры и клиента ЛК.
- Ошибки хеша, duplicate parts, multipart overhead, absolute/idle timeout,
  авторизация до вызова receive, TTL резервации и lock живого writer.
- Потерянный ответ после upload commit: входы сохраняются, GET/replay возвращают
  прежнюю accepted check. Ошибка создания outbox откатывает terminal и release.
- ETag, pause, снижение cap без отзыва резерваций, повтор migrate, чужие UUID/scopes.
- Docker intent до create, отказ stale fence, запрет дополнительной активной роли,
  сохранение слота при неопределённом create, отказ удаления чужих labels.
- Recovery не запускает движок повторно. Без доказательства остановки supervisor
  сохраняет cleanupBlocked; после подтверждения очищает и завершает execution_failed.
- Настоящий pure runtime при umask 0007, проверка filtered env, остановка реальной
  дочерней process group по whole-check timeout.
- Параллельные SQL-проверки `Dvizhok.xdump` и `autocheck-2026-09-24-16-14.xdump`:
  обе passed, исходные таблицы пусты, все шесть созданных ресурсов удалены.
- HMAC над исходными байтами, HTTP retry, manual retry с прежними eventId/body,
  payload expiry, backpressure недоставленных payload, соответствие ответов OpenAPI.
- Собран Docker image `element-checker/service:local`; `nginx -t` выполнен в целевом
  Nginx image с UID 101, read-only root и сброшенными capabilities.
- Временная установка всех шести Compose services: readiness, настоящий HTTP
  reserve/upload, SQL passed, JSON artifact, JSON gateway 413 с requestId, slots=0.
  Тестовый Compose project, контейнеры, сети и его volume удалены после проверки.
- Общая регрессия: 1027 тестов, 636 штатных пропусков, ошибок нет. После изменения
  JSON-выдачи дополнительно выполнены 9 grading/batch тестов и service-набор.

Воспроизводимые команды находятся в `deploy/autocheck/README.md`. Service-набор —
`tests/test_autocheck_service.py`; временный deployment —
`tools/ci/autocheck_smoke.py`. Добавлен CI workflow с PostgreSQL для service-тестов.

## Границы локальной приёмки

Первоначальные результаты этого раздела относятся к локальной приёмке.
Конфигурация серверного развёртывания описана в
[инструкции по установке](../deploy/autocheck/README.md). Локальная установка
использовала Docker Desktop и тестовые секреты, не обращалась к реальному callback.

Автоматическая очистка сохраняет слот при неопределённом create, если ресурс ещё
не обнаружен, и при отсутствии доказательства остановки старого supervisor. Такой
случай требует операторского расследования; простое отсутствие результата inspect
не освобождает место. Массовые SIGKILL/daemon restart, несколько production replicas,
rolling upgrade и аварийное восстановление из backup требуют отдельной приёмки.

Артефакты живут 30 дней, до 37 при недоставленном результате; входы и приватные
файлы удаляются после 37 дней. Оценка и metadata сохраняются 180 дней. Consumed
reservation сохраняется вместе с check для replay/FK; незагруженные завершённые
резервации удаляются через 7 дней, idempotency tombstones — через 180 дней.

HardLimit=8 не подтверждает производительность восьми одновременных SQL-проверок.
Перед увеличением начального cap=2 нужны измерения CPU/RAM/I/O и задержек на реальных
работах. Не все пункты эксплуатационного checklist считаются выполненными локально.
