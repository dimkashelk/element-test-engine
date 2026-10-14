# Проверка проекта микросервиса

14 октября 2026. Проверялась проектная конфигурация, без реализации сервиса,
запуска контейнеров и исполнения студенческих работ.

| Проверка | Результат |
|---|---|
| Docker Compose `config --quiet` | PASS, Compose v5.5.1 |
| Docker Compose `config --services` | PASS: postgres, migrate, notifier, worker, api, gateway |
| Разбор API/webhook/config YAML | PASS |
| Ссылки API/webhook | PASS, 114 ссылок разрешены |
| Path parameters / operationId | PASS, API: 10 paths / 13 operations; webhook: 1 / 1 |
| Синтаксис 25 схем OpenAPI после переноса nullable в JSON Schema | PASS |
| Четыре примера API/event | PASS по соответствующим схемам с проверкой UUID/date-time |
| Grading в примерах | PASS по существующему grading-v1.schema.json |
| Транспортные лимиты config/API/Nginx | PASS: 32/24/4 MiB, multipart запас предусмотрен |
| Состав/права доступа Compose | PASS: socket только worker, ports только gateway, DB внутренняя |
| Абсолютные bind paths API/worker/runtime | PASS: host source совпадает с container target |
| Defaults и зависимости | PASS: default=2, hardLimit=8 у migrate/worker; сервисы ждут migrate |
| Синтаксис Nginx | PASS, native `nginx -t`, только в временной копии upstream заменён на 127.0.0.1 |
| Изменения кода движка/приложения | Нет |

OpenAPI проверялся структурно и по схемам примеров. Полный внешний валидатор
OpenAPI Specification не использовался; это не заменяет validation и contract tests
при реализации. Контейнерный запуск Nginx и разрешение Compose DNS не проверялись.
Настоящие secrets, host directories и service image не создавались.

На проектной проверке отдельно уточнены условия безопасности восстановления:

- Lease/fence ограничивает публикацию состояния, но не останавливает физический
  Docker-клиент. Claim нельзя освобождать при неподтверждённой остановке владельца.
- Docker create имеет durable intent, детерминированное имя и labels; неизвестный
  исход операции оставляет cleanupBlocked, а не увеличивает доступную capacity.
- Worker limits из Compose не ограничивают sibling executor/SQL контейнеры;
  hardware budget должен учитывать их отдельно.
- `202` возвращается после durable input и DB commit; rename/DB crash gaps покрыты
  orphan recovery, callback создаётся только с terminal DB transaction.

Повторяемая проверка Compose из корня репозитория:

```bash
docker compose --env-file deploy/autocheck/.env.example \
  -f deploy/autocheck/compose.yaml config --quiet
```

Критерии будущей приёмки — в [checklist](autocheck-review-checklist.md).
