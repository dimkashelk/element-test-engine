# Предикаты, состояние и доступ — №45

Версии исходных проектов: 9.0. Справка: Элемент 9.3. Исполнение:
Script 10.0.2-1, current; native XBQL отсутствует. `reference-9.3.json`
фиксирует прочитанные первичные источники и границы адаптера.

`tests/state_fixtures.py` содержит авторские данные и ответы до исполнения,
строит ordinary/daily/hierarchy/documented и задания state-*.
У ordinary и daily меняется источник: справочник и дневной срез регистра.
У hierarchy объявлен системный Родитель. Запросы и операции исполняются
в Script над текущей storage-сессией; Python не вычисляет строки результатов.

`queryAccess` с mode `executor-fixture` задаёт явные права чтения источника
и список видимых ID. Неуказанный источник запрещён. Это тестовая модель,
а не исполнение native КонтрольДоступа, разрешений или RLS-обработчиков.
Исторические state/rights templates содержат прозу; им не приписывается
точная исполнимая оценка. Для их семантики есть отдельные критерии.
Дополнительные иерархии, nullable внешние корреляции, коррелированный IN,
DML с несколькими источниками и custom data sources остаются недоступны.

Воспроизведение целевого набора:

```bash
PYTHONPATH=tests:. python3 -m unittest -v \
  test_query_state.StatePlanTest test_query_state.CorrelationGuardTest
ELEMENT_TEST_DOCKER_TESTS=1 PYTHONPATH=tests:. python3 -m unittest -v \
  test_query_state.StateDockerTest test_query_state.StateSourceTest \
  test_query_state.StateEdgeTest test_query_state.StatePublicTest
ELEMENT_TEST_INTEGRATION_TESTS=1 PYTHONPATH=tests:. python3 -m unittest -v \
  test_query_state.StateSqlTest test_query_state.StateRealSqlTest
PYTHONPATH=tests:. python3 tests/corpus/state-rights/refresh_catalog.py
PYTHONPATH=tests:. python3 tests/corpus/state-rights/verify_public.py
python3 tests/corpus/state-rights/record_measurements.py
```

Для verify_public требуется jsonschema. При приёмке пакет был установлен
во временный `/private/tmp/element-stage045-deps`, без изменения зависимостей
проекта. Точная закреплённая пара Docker-образов задана config/runtimes.json
и config/integration.json. Первичный отказ при отсутствующих образах,
ошибки генерации и отдельные повторы сохранены в result/state-rights.

Полный discovery, 303 формы, 44 прежних корня и четыре архивные validate
относятся к nightly. Скрипт refresh_catalog обновляет только 161 замороженную
запись baseline; parsed не считается independentlyAssessed.
