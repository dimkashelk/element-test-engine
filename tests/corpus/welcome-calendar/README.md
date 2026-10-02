# Корпус этапа 35

70 независимых критериев вызывают восемь прямых корней исходного Dvizhok.
`build_assignment.py` содержит авторские требования и воспроизводит YAML;
он не читает дамп или результат исполнения. 16 сценариев ПослеСоздания
охватывают восемь границ времени с рабочим/нерабочим днём; отдельный сценарий
использует один текущий снимок внутри Docker и SBSL oracle. Срезы календаря
включают пятницу, воскресенье, выходные, месяц, год, 29 февраля, всю неделю,
перевёрнутый диапазон и другие настройки недели. Sequence сохраняет форму.

```sh
python3 tests/corpus/welcome-calendar/build_assignment.py
bin/element-test test --project Dvizhok.xdump \
  --assignment assignments/dvizhok-welcome-calendar \
  --output result/dvizhok-welcome-calendar/test
bin/element-test run --project Dvizhok.xdump \
  --assignment assignments/dvizhok-welcome-calendar \
  --output result/dvizhok-welcome-calendar/run
ELEMENT_TEST_DOCKER_TESTS=1 python3 -m unittest discover \
  -s tests -p test_welcome_calendar.py -v
python3 tests/corpus/welcome-calendar/run_native_probes.py
```

`options`/`preferences` используют разные имена и одинаковые краткие owners
в Alpha/Beta, import alias, nested UI, повторное чтение констант, локальное
и параметрическое затенение. Их задания — welcome-options/welcome-preferences.
Одинаковые metadata UUID, разные константы и часы проверяются в двух batch;
одна мутированная работа не меняет соседние.

Native-пробы проверяют обе локали в Docker, отсутствующий ключ (исключение),
nullable ПолучитьИлиНеопределено, сохранность словаря и календарные переходы.
13 мутаций обнаруживают 19 FAIL в SBSL; helper проверяется и прямо, и через
вызывающий обработчик. Полный регрессионный запуск:

```sh
python3 tests/corpus/welcome-calendar/run_regression.py
```

Он включает Docker/SQL opt-in, генерирует временный пароль только в памяти,
сохраняет журнал и число тестов. Новые тесты не меняют runtime-лимиты.
Карта пересоздаётся из новых и прежних журналов:

```sh
python3 -m element_test.coverage --project Dvizhok.xdump --assignments assignments \
  --evidence result/dvizhok-welcome-calendar/test \
  --evidence result/dvizhok-welcome-calendar/run \
  --evidence result/dvizhok-welcome-calendar/previous-forms \
  --evidence result/dvizhok-welcome-calendar/previous-business \
  --output result/dvizhok-welcome-calendar/coverage
```

Семантика и ограничения — [контракт](../../../docs/welcome-calendar.md).
Native UI/транспорт, запись констант, SQL-константы, эффекты Записать/Открыть
и вычисление YAML-выражений остаются вне этого этапа. Непроверенные файлы
и методы в карте не получают PASS.
