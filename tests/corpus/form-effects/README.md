# Приёмка №36

`assignments/dvizhok-form-effects` содержит 46 независимых критериев:
по 23 memory/PostgreSQL, три прямых корня неизменённого Dvizhok.xdump.
Fixture/expected заданы в tests/form_effect_fixtures.py; бизнес-вычисления
исполняют исходные XBSL тела в Script, оценивание — SBSL.

```sh
PYTHONPATH=.:tests python3 tests/form_effect_fixtures.py
python3 tests/corpus/form-effects/run_native_probes.py
python3 tests/corpus/form-effects/run_acceptance.py
bin/element-test test --project Dvizhok.xdump --assignment assignments/dvizhok-form-effects --output result/dvizhok-form-effects/manual-test --integration
```

Для SQL обязателен временный ELEMENT_TEST_INTEGRATION_PASSWORD; приёмочный
runner создаёт его в окружении без сохранения в файлах. Требуются Docker и
локальный Script runtime. Нельзя запускать широкие прогоны параллельно.

ledgers/accounts проверяют два разных набора имён, одинаковые краткие owners
в Alpha/Beta, alias Destination, nullable ссылки, закрытые массивы и изменение
строк после первого открытия. Lifecycle целевого контекста дополнительно
меняет собственную копию массива — снимки запросов остаются независимыми.

Тесты test_form_effects.py сохраняют планы, trace, наблюдения и SBSL результаты,
проверяют собственные Записать/Открыть, callback и затенённый alias,
отказы/rollback/SQL-сбой, временные мутации исходников, две публичные batch
с двумя правильными и одной мутированной работой. Исходные архивы не меняются.
Границы и формат — [form-effects.md](../../../docs/form-effects.md).

Для finalize_measurements нужен Python с пакетом jsonschema; в этой приёмке
использовано ранее созданное изолированное окружение element-test-schema34.

Полная финальная проверка выполняется последовательно, после baseline и
native-проб. Runner включает Docker/SQL и создаёт временный пароль; последующие
шаги требуют успешного полного журнала и сохранённых свидетельств baseline
(три отказа и исходные SHA-256), первичной приёмки и её повторов:

```sh
python3 tests/corpus/form-effects/run_regression.py
python3 tests/corpus/form-effects/verify_public.py
python3 tests/corpus/form-effects/finalize_measurements.py
```

verify_public дополнительно повторяет прежние 35 прямых корней, выполняет
четыре validate и пересоздаёт карту. finalize_measurements сверяет схемы,
хеши, результаты публичных команд и batch, отсутствие тестовых ресурсов;
затем сохраняет карту и измерения в docs. Первичные журналы и повторы
сохраняются отдельно в result/dvizhok-form-effects.
