# Приёмочный корпус №42

Авторские данные и expected находятся в `tests/composite_fixtures.py`,
сгенерированные проекты и задания сохранены в Git. `ordinary` и `renamed`
имеют одинаковую семантику при разных подсистемах, модулях и полях.
`documented` содержит пять исходных review templates без изменения XBQL.
Реальный корень выполняется прямо из неизменённого практического архива.

Из корня репозитория (Docker/Script и PostgreSQL доступны):

```bash
PYTHONPATH=tests:. python3 tests/composite_fixtures.py
ELEMENT_TEST_DOCKER_TESTS=1 ELEMENT_TEST_INTEGRATION_TESTS=1 PYTHONPATH=tests:. \
  python3 -m unittest test_query_composites -v
python3 tests/corpus/unions-nesting/run_native.py
PYTHONPATH=tests:. python3 tests/corpus/unions-nesting/refresh_catalog.py
python3 tests/corpus/unions-nesting/verify_public.py
python3 tests/corpus/unions-nesting/record_measurements.py
```

Для `verify_public.py` нужен Python с `jsonschema` (Draft202012).
На этой машине использован `PYTHONPATH=/private/tmp/element-query-fill-schema`.
Public `test`/`run` и два свежих batch запускаются тестами; batch автоматически
выбирает новый каталог результата и отдельный cache. `batch-latest.json`
указывает последние два успешных пакета. При повторе сохранить первичные
журналы под отдельными именами: measurements читает последние статусы каждого
тестового метода, учитывая повторы, а не только общий итог первого запуска.

Отдельный публичный вызов:

```bash
bin/element-test test --project Prakticheskie-primery-2026-09-30-15-20.xdump \
  --assignment assignments/unions-real --output result/unions-nesting/manual-test
```

Результаты: 56 основных сценариев в двух проектах, семь пустых,
шесть граничных, пять шаблонов, пять критериев реального корня, семь
SBSL FAIL мутаций, альтернативная реализация, восемь SQL-сценариев.
В SQL выбран только storage-срез; чистые сочетания не дублируются полностью.
Ожидания задаются независимо от actual и проверяются через SBSL oracle.

`refresh_catalog.py` проверяет exact hash/range только сохранённого baseline
№42 и не запускает полное discovery. История предыдущих этапов сохраняется.
`record_measurements.py` только читает evidence и не исполняет проекты.
Первичные отказы, отдельные повторы и отклонение локального набора описаны в
`docs/query-stage-042-measurements.json` и `docs/unions-and-nesting.md`.

Поддержка относится к standalone адаптеру: индексы — проверяемые логические
подсказки, queryContext — явный fixture идентификатора пользователя;
коррелированные/EXISTS-подзапросы, расширенные поля временных таблиц,
native XBQL/платформенная компиляция и права не заявлены.
