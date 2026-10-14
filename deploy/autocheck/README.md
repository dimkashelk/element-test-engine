# Развёртывание автопроверки: проект для согласования

**Это конфигурация будущего сервиса. Разработка приложения не выполнена.**
Образ `element-checker/service:review-placeholder` и модуль `element_check_service`
не существуют. `docker compose up` пока не является рабочим запуском автопроверки.
Не подменять будущий сервис заглушкой, которая принимает работы без проверки.

Основной проект: [архитектура](../../docs/autocheck-microservice-design.md),
[OpenAPI](../../docs/autocheck-openapi.yaml),
[webhook ЛК](../../docs/autocheck-webhook-openapi.yaml),
[приёмка](../../docs/autocheck-review-checklist.md).

## Состав

`gateway → api`, `worker`, `notifier`, одноразовый `migrate`, `postgres`.
Очередь остаётся в ЛК. PostgreSQL хранит места/состояния/outbox, Redis не нужен.
Динамические executor и SQL-БД каждой проверки создаёт worker на том же daemon.

Gateway слушает 127.0.0.1:8088. Внешний reverse proxy инфраструктуры завершает TLS.
Если proxy находится в отдельном Docker-контейнере, его localhost не равен localhost
хоста: требуется отдельно настроить доступ к host gateway либо общую ingress сеть.
Нельзя просто заменить адрес на 0.0.0.0 и считать HTTP защищённым.

## Что можно проверить сейчас

Из корня репозитория, без Docker daemon, создания контейнеров и реальных секретов:

```bash
docker compose --env-file deploy/autocheck/.env.example \
  -f deploy/autocheck/compose.yaml config --quiet

docker compose --env-file deploy/autocheck/.env.example \
  -f deploy/autocheck/compose.yaml config --services
```

Вторая команда выводит шесть сервисов. Проверка `config` подтверждает синтаксис
Compose и интерполяцию, но не существование image, bind paths, secret files, runtime
и не доступность daemon. Синтаксис Nginx проверен локальным `nginx -t` на временной
копии с upstream 127.0.0.1 вместо ещё не существующего Compose DNS `api`. Проверка
в целевом Nginx image и end-to-end выполняются после реализации.

## Подготовка Linux-хоста после реализации

1. Выделенный Linux Docker-host. Существующий runtime запускает host Docker CLI;
   удалённый daemon и Docker Desktop не являются целевым эксплуатационным вариантом
   этой версии. Использовать Docker Engine и Compose, поддерживающий health dependency,
   configs, file secrets и `bind.create_host_path: false`.
2. Собрать immutable сервисный image с будущим package, Python 3.12, Java 17, Docker
   CLI, текущими `element_test`, `src`, `bin`, `config`. Java/runtime нужны и для
   структурной оценки. Контракт команд указан ниже. Записать git revision и image
   digest в build metadata. Package versions фиксируются в lock file при разработке.
3. Разместить Script executor 10.0.2-1 в абсолютном `SCRIPT_RUNTIME_ROOT`, с lib/config,
   читаемыми executor UID 65534, без credentials. JDBC/SQL jars нужны integration.
4. Создать `SERVICE_DATA_ROOT` и подкаталоги `staging`, `checks`, `work` на одном
   filesystem. Владелец 10001:10001, directories 2770; API работает 10001, worker
   работает 0:10001 и сохраняет общую группу/права. Включить umask 0007 в service
   entrypoint. Directory не должен быть symlink.
5. Создать `.env` из `.env.example`, задать реальные абсолютные paths и digest image.
   Для Nginx заменить `stable-alpine` immutable digest, UID образа должен поддерживать
   запуск nginx 101:101 на 8080. PostgreSQL закреплён на том же 17 digest, который
   уже используется текущим движком; управляющая и временная БД независимы.
6. Создать настоящие секреты по [контракту](secrets/README.md), вне Git. Убедиться,
   что UID 10001 может их читать. File-based Compose secrets не являются зашифрованным
   secret vault; защищаются права хоста и резервных копий. Private key TLS находится
   на внешнем reverse proxy, не в этом Compose.
7. До admission загрузить runtime images по digest из `config/runtimes.json` и
   `config/integration.json`. Runtime делает `--pull never`. Изображения динамических
   контейнеров не наследуют `SERVICE_IMAGE`.
8. Прогнать checklist, резервное копирование и восстановление. Затем разрешить ЛК
   отправку. Не начинать с hardLimit=8 как с измеренной безопасной производительности.

## Будущие команды image

| Команда | Контракт |
|---|---|
| `python -m element_check_service migrate` | Создать/обновить схему, settings только при отсутствии, instance UUID; exit 0 после завершения |
| `python -m element_check_service api --host 0.0.0.0 --port 8000` | HTTP API и middleware раннего допуска |
| `python -m element_check_service worker` | Supervisor, heartbeat, execution, recovery, cleanup/retention; без внешней очереди |
| `python -m element_check_service notifier` | Delivery registry, outbox claim и callback retries |
| `python -m element_check_service health api` | GET localhost /health/ready; exit 0 только ready |
| `python -m element_check_service health worker` | Свежая presence текущего процесса, DB, runtime/Docker preflight |
| `python -m element_check_service health notifier` | Свежий heartbeat текущего процесса и валидные delivery configs |

ENV `_FILE` — **контракт будущего приложения**: существующий движок не умеет читать
`INTEGRATION_PASSWORD_FILE`. Worker читает secret и передаёт значение только дочернему
процессу в `ELEMENT_TEST_INTEGRATION_PASSWORD`. DB/callback secrets не наследуются
движком. `health` не должен сам регистрировать heartbeat вместо живого supervisor.

## Параллельность

Начальный default=2 и hardLimit=8 передаются migrate/worker. Реальный
`maxConcurrentChecks` хранится в PostgreSQL и изменяется PATCH admin/settings с ETag.
Перезапуск не сбрасывает настройку. API concurrency/Uvicorn workers не являются
лимитом проверок. Уменьшение не обрывает текущие работы/резервации; pause запрещает
только новые reserve. Места освобождаются после подтверждённой очистки executor.

У Compose нет `container_name`, поэтому реплики worker технически возможны на одном
хосте; глобальный cap, leases, labels и один instance UUID обязательны при любой
репликации. Установка API/worker на другом хосте без общего файлового контракта
этим документом не предусмотрена.

## Жизненный цикл после реализации

Начальный запуск: проверить Compose с `.env`, выполнить `up -d`; health dependencies
дождутся PostgreSQL, migrate и API. Запуск приложения пока отсутствует.

Обычный `docker compose up -d` может не перезапустить успешно завершённый migrate
при замене версии приложения. При обновлении: отключить admission в БД через API,
дождаться всех claims=0, остановить api/worker/notifier/gateway, создать backup,
обновить image/config, выполнить `docker compose run --rm migrate` с новой версией,
затем `docker compose up -d --force-recreate`. Проверить readiness/schema и только
затем включить admission. Не рассчитывать на depends_on как на механизм rolling
migration. Миграции транзакционные, повторяемые; старый image не запускается с
несовместимой schema. DB major upgrade не выполняется простой заменой image tag.

Shutdown: pause, drain, затем stop. `stop_grace_period` покрывает cleanup, а полная
работа может длиться 900 секунд; SIGTERM worker не должен ждать 900 секунд. Если
drain не выполнен, policy незавершённых работ — execution_failed после cleanup.

Никогда не использовать `down -v` как штатный stop: это удаляет PostgreSQL volume.
Data bind directory живёт отдельно. Backup охватывает и БД, и data; для согласованного
backup проще pause/drain, затем snapshot. Восстановленная установка сначала сверяет
Docker resources/claims и закрывает orphans, затем включает admission.

## Ограничения этой проверки

Compose config и локальный синтаксис Nginx проверены без запуска. Секреты, image,
Nginx configuration внутри контейнера,
host directories, labels/fencing, нагрузка, callback и восстановление будут проверены
при реализации. Никакие студенческие работы через проектный сервис не запускались.
