# Проекции и агрегаты — №41

Два переносимых проекта `ordinary`/`renamed`, отдельный `documented` для пяти
точных review templates и два прямых реальных корня. Авторские входы/ответы:
`tests/projection_fixtures.py`. Генератор меняет только новые fixtures/assignments.
Справка Элемента 9.3, установленный Script 10.0.2-1/current; исходные архивы 9.0.
[Контракт и ограничения](../../../docs/projections-and-aggregates.md),
[измерения](../../../docs/query-stage-041-measurements.json).

```bash
PYTHONPATH=tests:. python3 tests/corpus/projections-aggregates/build_fixtures.py
PYTHONPATH=tests:. python3 -m unittest test_query_projections.ProjectionPlanTest -v
ELEMENT_TEST_DOCKER_TESTS=1 ELEMENT_TEST_INTEGRATION_TESTS=1 PYTHONPATH=tests:. \
  python3 -m unittest test_query_projections -v
python3 tests/corpus/projections-aggregates/run_native.py
bin/element-test test --project Demo-SRM-dev-2026-09-28-21-38.xdump \
  --assignment assignments/projections-real-tasks --output result/projections-aggregates/public-tasks-test
bin/element-test run --project autocheck-2026-09-24-16-14.xdump \
  --project-name dimkashelk::check --assignment assignments/projections-real-max \
  --output result/projections-aggregates/public-max-run
```

PostgreSQL использует временные контейнеры с независимым SQL-аудитом и cleanup.
Нативные пробы сохраняют ожидаемые отказы `-c 9.3` и литерала Запрос отдельно
от успешных scalar-проб; эти отказы не являются поддержкой XBQL.

После публичных тестов (для проверки схем требуется `jsonschema`):

```bash
python3 tests/corpus/projections-aggregates/verify_public.py
PYTHONPATH=tests:. python3 tests/corpus/projections-aggregates/refresh_catalog.py
python3 tests/corpus/projections-aggregates/record_measurements.py
```

Scoped refresh обновляет только 39 записей сохранённого baseline, проверяет
точные хеши/диапазоны и отдельные SBSL receipts пяти review templates.
Полный discovery для воспроизведения №41 не нужен. Результаты и первичные
сбои сохраняются в `result/projections-aggregates/`; SHA-256 и сводки — в docs.
Локальная приёмка выполнена целевыми прогонами с отдельными повторами.
