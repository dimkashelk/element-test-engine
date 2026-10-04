# Соединения и NULL — №40

Два переносимых проекта (`ordinary`, `renamed`) и независимые данные/ответы
в `tests/join_fixtures.py`. Генератор восстанавливает только эти fixtures и новые
assignments; исходные архивы не меняет. Два реальных корня вызываются напрямую:
ПолучитьКурсыВалют из Demo-SRM и ПолучитьДоступныеКейсы из autocheck.

Справка Элемента 9.3, установленный Script 10.0.2-1, профиль `runtimeProfile: "9.3"`
с режимом `current`. Исходная версия реальных архивов остаётся 9.0.
[Контракт и ограничения](../../../docs/joins-and-null.md),
[измерения](../../../docs/query-stage-040-measurements.json).

```bash
PYTHONPATH=tests:. python3 tests/corpus/joins-null/build_fixtures.py
PYTHONPATH=tests:. python3 -m unittest test_query_joins.JoinPlanTest -v
ELEMENT_TEST_DOCKER_TESTS=1 ELEMENT_TEST_INTEGRATION_TESTS=1 PYTHONPATH=tests:. \
  python3 -m unittest test_query_joins -v
python3 tests/corpus/joins-null/run_native.py
bin/element-test test --project Demo-SRM-dev-2026-09-28-21-38.xdump \
  --assignment assignments/joins-null-real --output result/joins-null/public-test
bin/element-test run --project autocheck-2026-09-24-16-14.xdump \
  --project-name dimkashelk::check --assignment assignments/joins-null-cases \
  --output result/joins-null/public-cases-run
```

PostgreSQL-тесты создают временные контейнеры и удаляют их после аудита.
`run_native.py` сохраняет ожидаемые отказы отдельного режима `9.3` и нативного
литерала Запрос в standalone Script; эти отказы не являются поддержкой XBQL.

Дополнительная проверка пакетов требует доступного Python-пакета `jsonschema`:

```bash
python3 tests/corpus/joins-null/verify_public.py
PYTHONPATH=. python3 tests/corpus/joins-null/refresh_catalog.py
python3 tests/corpus/joins-null/record_measurements.py
```

`refresh_catalog.py` обновляет только 73 записи сохранённого baseline №40,
проверяет exact sourceHash/range и SHA-256 четырёх архивов. Полный discovery
локально не выполняется. Полный каталог остаётся обязательством следующих
этапов; parsed и trace не заменяют независимую runtime оценку.

Результаты и первичные сбои сохраняются в `result/joins-null/`. Приёмка
выполнена несколькими целевыми прогонами с отдельными успешными повторами,
а не полным прогоном всех тестов проекта.
