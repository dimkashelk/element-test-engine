# Приёмочный корпус №43

`tests/virtual_fixtures.py` содержит независимо заданные истории и expected.
`ordinary` и `renamed` проверяют переносимость между подсистемами и модулями;
`documented` исполняет четыре точных review templates без правки XBQL.
Прямой реальный корень — `ПередЗаписью` документа Отгрузка из Dvizhok.xdump.

Из корня репозитория, при доступных Docker/Script/PostgreSQL:

```bash
PYTHONPATH=tests:. python3 tests/virtual_fixtures.py
ELEMENT_TEST_DOCKER_TESTS=1 ELEMENT_TEST_INTEGRATION_TESTS=1 PYTHONPATH=tests:. \
  python3 -m unittest test_query_sources -v
python3 tests/corpus/virtual-tables/run_native.py
PYTHONPATH=tests:. python3 tests/corpus/virtual-tables/refresh_catalog.py
python3 tests/corpus/virtual-tables/verify_public.py
python3 tests/corpus/virtual-tables/record_measurements.py
```

Для проверки JSON Schema нужен Python с jsonschema/Draft202012; здесь
использован `PYTHONPATH=/private/tmp/element-query-fill-schema`.
Тесты запускают публичные test/run и два batch с отдельными cache и новыми
каталогами вывода. `batch-latest.json` сохраняет последние два успешных пакета.
Первичные журналы хранить отдельно от повторов: measurements читает последние
статусы методов и сохраняет перечень первичных сбоев.

Отдельный публичный вызов:

```bash
bin/element-test test --project Dvizhok.xdump \
  --assignment assignments/virtual-real --output result/virtual-tables/manual-test
```

Набор проверяет границы первого/последнего среза и итогов, нулевые группы,
неактивные записи, кратность/порядок членов, несколько измерений/ресурсов,
JOIN/вложения/UNION, сохранённый источник, пользователей/константы,
scalar/array IN, повторные снимки, rollback, инфраструктурный отказ и recovery.
Шесть бизнес-мутаций исполняются с SBSL FAIL; корректный альтернативный агрегат
проходит. В SQL не дублируется весь чистый набор.

`refresh_catalog.py` проверяет exact hash/range только 27 baseline-записей,
сохраняя историю остальных этапов. `record_measurements.py` только читает
evidence. Источники справки/поля/параметры и различия adapter/platform записаны
в `reference-9.3.json`; границы поддержки — в `docs/virtual-tables.md`.
Native XBQL/права/обмены/настройки и фильтры/периодизация виртуальных источников
не заявляются. Таблицы контрактов сущностей исключены по версии.
