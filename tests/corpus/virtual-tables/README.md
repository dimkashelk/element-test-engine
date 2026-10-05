# Приёмочный корпус №43

`tests/virtual_fixtures.py` содержит независимо заданные истории и expected.
`ordinary` и `renamed` проверяют переносимость между подсистемами и модулями;
`documented` исполняет четыре точных review templates без правки XBQL.
Прямой реальный корень — `ПередЗаписью` документа Отгрузка из Dvizhok.xdump.

## Расширение от 5 октября 2026 года

`test_virtual_extensions.py` добавляет независимые ожидания для фильтров,
календарных итогов, Авто/дополнения, истории Момент/СледующийПериод,
параметризованных сохранённых источников и различия NULL/Неопределено.
Три оригинальных внешних `.xbql` исполняются через отдельную Query API обвязку;
hash исходника и hash обвязки учитываются раздельно. Native lifecycle отчётов
не заявляется. Полная приёмка остаётся открытой, ограничения перечислены в
`docs/virtual-tables.md`; пользователь подтвердил отсутствие приложения Элемента.

Воспроизведение всех 44 целевых тестов из корня репозитория:

```bash
mkdir -p result/virtual-tables-completion
PYTHONPATH=tests:. python3 tests/virtual_fixtures.py
bin/script-runtime -c current tests/corpus/virtual-tables/native-probes/HistoryLimits.sbsl \
  > result/virtual-tables-completion/native-history-limits.json
ELEMENT_TEST_DOCKER_TESTS=1 ELEMENT_TEST_INTEGRATION_TESTS=1 PYTHONPATH=tests:. \
  python3 tests/corpus/virtual-tables/run_completion.py \
  test_query_sources test_virtual_extensions \
  test_query_joins.JoinPlanTest test_query_projections.ProjectionPlanTest \
  test_query_composites.CompositePlanTest test_query_results.ResultPlanTest \
  test_query_composites.CompositeEdgeTest.test_hidden_null_column_collision_and_nullable_alias_comparison \
  > result/virtual-tables-completion/full-repeat.log 2>&1
PYTHONPATH=tests:. python3 tests/corpus/virtual-tables/record_completion.py full-repeat.log
```

Для последней команды нужен установленный `jsonschema` (при текущей приёмке
добавлен `/private/tmp/element-query-fill-schema` в PYTHONPATH). Driver запускается
из файла с main guard: запуск batch через Python stdin несовместим с spawn.
Для отдельных повторов передавайте driver точные selectors, а reporter —
имена журналов в хронологическом порядке. Без аргументов reporter использует
четыре журнала текущей приёмки. Парсинг или пропуск теста не считается PASS.

Результаты хранятся отдельно в `result/virtual-tables-completion/`;
coverage/measurements с суффиксом `completion` сохраняют hashes, последние
успешные статусы, первичные сбои, 91 schema validation и четыре свежих batch.
Старые measurements, архивы, assignments/expected, лимиты и CI не переписываются.
Локальный повтор не включает полный discovery, 303 формы, 44 прежних корня,
четыре validate или полную регрессию.

## Историческая приёмка первоначального executor

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
Эта первоначальная приёмка не включала фильтры/периодизацию виртуальных
источников; их свежие свидетельства находятся в completion. Native
XBQL/права/обмены/настройки не заявляются. Таблицы контрактов сущностей
исключены по версии.
