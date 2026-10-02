# Корпус этапа 34

`ledgers` и `accounts` — два переносимых проекта с разными именами форм,
объектов, свойств, компонентов и методов. Обе подсистемы каждого проекта
содержат объект с одинаковым кратким именем. Сценарии проверяют вложенные
компоненты, native UUID, лексическое затенение и состояние одного экземпляра
между вызовами. Их задания: `assignments/form-ledgers` и `form-accounts`.

`build_assignment.py` воспроизводит `assignments/dvizhok-form-context`:

```sh
python3 tests/corpus/form-context/build_assignment.py
bin/element-test test --project Dvizhok.xdump \
  --assignment assignments/dvizhok-form-context \
  --output result/dvizhok-form-context/test
bin/element-test run --project Dvizhok.xdump \
  --assignment assignments/dvizhok-form-context \
  --output result/dvizhok-form-context/run
bin/element-test test --project Dvizhok.xdump \
  --assignment assignments/dvizhok-runtime-regression \
  --output result/dvizhok-form-context/legacy
```

Ожидаются 74 PASS и 3 UNSUPPORTED у форм (код завершения 2), 22 PASS у
прежних бизнес-сценариев. Ожидания определены как требования сценариев;
генератор не читает результат исполнения исходника. Аргументы `contextPath`
передают настоящую таблицу экземпляра, observe проверяет её сохранность.

```sh
ELEMENT_TEST_DOCKER_TESTS=1 python3 -m unittest discover \
  -s tests -p test_form_context.py -v
```

Тесты исполняют исходный архив и девять мутаций временных копий, проверяют
одиннадцать независимых FAIL, два публичных batch в свежие каталоги и
изолированные экземпляры с одинаковыми ссылками. Исходные архивы не меняются.
Журналы и итоговая карта сохраняются в `result/dvizhok-form-context/`.

`native-probes` содержит доверенные технические пробы, которые можно запускать
на host через `bin/script-runtime -c 9.0`. State проверяет умолчания,
затенение и отдельные экземпляры; UUID — native ссылки; ArrayBinding — передачу
изменяемого массива. UI и Generic намеренно демонстрируют отказ компиляции
платформенных UI-типов и собственной generic-структуры в standalone Script.
Студенческий/архивный код запускается через Docker. Подробный контракт и
подтверждённые ограничения — в `docs/form-context.md`.
