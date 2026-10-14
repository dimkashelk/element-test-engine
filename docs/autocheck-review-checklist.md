# Финальная проверка проекта и приёмка будущей реализации

Дата: 14 октября 2026. Это критерии проверки, а не уже выполненные E2E-тесты.

## Решения для согласования

- Очередь и приоритеты только в ЛК; reserve лишь для готовых к отправке работ.
- Критерии отправляет backend ЛК, snapshot хранится вместе с запуском.
- Reservation занимает тот же слот, что upload/check. Нет скрытой второй очереди.
- TTL reserve 120 сек, upload 180 сек, whole check 900 сек.
- Request 32 MiB, dump 24 MiB, criteria 4 MiB; более крупные работы пока отвергаются.
- Динамический maxConcurrentChecks первоначально 2, hardLimit 8, pause отдельный.
- Работы не прерываются при снижении лимита. Нет отмены running и auto re-execution.
- Terminal outcome и доставка разделены; технический сбой не создаёт оценку 0.
- Один Linux Docker-host, общий filesystem, PostgreSQL, worker с Docker socket.
- Callback at-least-once с HMAC, retries до 7 дней и GET для сверки.
- Artifacts 30 дней (37 при проблемах доставки), metadata/idempotency 180 дней.
- Compose — подготовленная схема; service image/module пока не реализованы.

## Проверено на этапе проектирования

- [x] Текущий engine/bridge/runtime/integration/grading/batch прочитан.
- [x] Compose `config --quiet` и `config --services` проходят без запуска.
- [x] YAML артефакты разбираются, ссылки OpenAPI разрешаются, operationId уникальны.
- [x] Примеры контрактов и перенос grading проверены по JSON Schema.
- [x] Defaults лимитов и состав сервисов согласованы между config/OpenAPI/docs.
- [x] Локальный `nginx -t` проходит с временной заменой upstream на localhost;
      проверка именно в будущем container image остаётся этапом реализации.
- [x] Приложение, миграции, Dockerfile и engine code не разрабатывались.

## Резервирование и загрузка — будущая приёмка

- [ ] 100 конкурентных reserve при cap=4 создают ровно 4 claims, остальные 429.
- [ ] Два API/worker на одном хосте сохраняют тот же глобальный cap.
- [ ] Чужой reservation/check UUID не доступен; scope checks:read не даёт write.
- [ ] Просроченный reservation освобождается; повтор key возвращает 410, не новый слот.
- [ ] Живой upload не освобождается по истечении reserve TTL.
- [ ] Upload дольше 180 секунд/idle 30 сек прерывается и не может commit старым fence.
- [ ] Без auth/reservation API отвергает headers до multipart parsing.
- [ ] 20 MiB upload не оказывается целиком в RAM приложения/клиента.
- [ ] Chunked body, multipart overhead, duplicate parts и неверные hashes проверяются.
- [ ] Превышение Nginx и app limits дают 413; частичные файлы убираются.
- [ ] ZIP traversal, symlink, hardlink, duplicates, YAML alias bomb и внешние
      formRequirements paths отклоняются до запуска engine.
- [ ] Дамп не может переопределить доверенные criteria/image/command/mounts.
- [ ] Потерянный ответ 202 восстанавливается через GET/replay без новой проверки.
- [ ] Crash до/после rename и до/после DB commit не теряет committed input и не
      запускает orphan. Одновременно по одной сдаче/attempt — одна accepted check.

## Исполнение, настройки и очистка — будущая приёмка

- [ ] Изменение cap 2→4 начинает использовать новые места без restart.
- [ ] Изменение 4→1 не обрывает текущие работы; новые reserve ждут снижения occupied.
- [ ] ETag защищает PATCH от потерянного обновления; pause допускает live reservations.
- [ ] Restart сохраняет DB settings; hardLimit mismatch выключает admission.
- [ ] Worker readiness проверяет Docker, pinned images, runtime и SQL jars.
- [ ] Accepted работа начинает выполнение за 10 секунд либо завершается execution_failed.
- [ ] Действительно работают student executor limits и isolated/internal SQL networks.
- [ ] Executor/SQL не имеют доступа к управляющей БД, API, secret files и Docker socket.
- [ ] Текущие regression outcomes движка совпадают с одиночным CLI на тех же входах.
- [ ] Structured grade выдаётся через SBSL, API не пересчитывает score.
- [ ] Есть grading schema validation, sourceHash/raw hash distinction и engine provenance.
- [ ] Whole check timeout останавливает process group и Docker resources.
- [ ] SIGKILL worker, daemon restart и потеря DB fencing не вызывают второй запуск
      или позднюю публикацию старой попытки.
- [ ] Crash между Docker create и registry insert покрыт labels sweep.
- [ ] Cleaner не удаляет чужие контейнеры; unresolved cleanup держит слот и даёт сигнал.
- [ ] Слот свободен только после cleanup и terminal DB transaction/outbox commit.

## Доставка и эксплуатация — будущая приёмка

- [ ] Outbox terminal event создаётся в той же транзакции, что terminal outcome.
- [ ] Crash после HTTP 2xx до DB ack повторяет eventId, ЛК дедуплицирует событие.
- [ ] ЛК отвергает неверную подпись, body/header eventId mismatch и старый timestamp.
- [ ] У позднего retry timestamp новый, body/eventId прежние.
- [ ] 429/5xx/timeouts дают bounded jitter retries; 3xx не follow; 4xx сигнал оператору.
- [ ] Failed delivery не запускает повторную оценку и не занимает execution slot.
- [ ] Manual delivery retry сохраняет eventId и payload, expired payload даёт 410.
- [ ] Старый attempt не заменяет результат новой сдачи в ЛК.
- [ ] API GET возвращает результат при недоступном callback; secrets/raw logs не утекли.
- [ ] Backpressure disk/outbox блокирует новые reserve, не удаляет принятые работы.
- [ ] Retention не удаляет live files, grade живёт после удаления HTML, GET artifact=410.
- [ ] Secrets rotation, no-token logs, backup+restore и migrate upgrade проверены.
- [ ] Nginx `nginx -t`, контейнерные healthchecks и реальные permissions проходят.
- [ ] Измерены CPU/RAM/I/O/latency на cap 1/2/4 для structural/runtime/SQL/generative;
      по результатам утверждён hardLimit и hardware budget.
