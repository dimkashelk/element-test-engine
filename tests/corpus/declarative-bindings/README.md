# Целевой корпус №38

`first` и `second` соответствуют одному русскому описанию `requirements.yaml`.
Имена компонентов, порядок свободных компонентов, alias/полная квалификация
и условное выражение различаются. `second` не содержит XBSL-модуля формы;
`first` содержит только import, без исходных методов. Alpha и Beta объявляют
одноимённые owners для проверки полной идентичности типов.

Fixtures и expected заморожены до исполнения. Числа дебета/кредита различаются;
источники содержат разные строки, порядок и повторы. Встроены независимые
маркеры для потери, перестановки и дублирования строк. Тесты меняют декларации
работы, сохраняя преподавательский контракт.

`build_portable.py` пересоздаёт две работы и их задание. `build_assignment.py`
подготавливает 16 описаний Dvizhok: инвентаризацию/типы берёт из деклараций
эталона, сценарии и ожидаемые значения задаёт автор. Генератор не вызывается
при проверке студентов. Изменять контракт по результату мутации нельзя.

Целевые команды из корня репозитория:

```sh
PYTHONPATH=tests python3 -m unittest test_declarative_bindings.BindingPlanTest -v
ELEMENT_TEST_DOCKER_TESTS=1 ELEMENT_TEST_INTEGRATION_TESTS=1 PYTHONPATH=tests python3 -m unittest test_declarative_bindings -v
bin/element-test test Dvizhok.xdump --assignment assignments/dvizhok-declarative-bindings --output result/my-bindings-test
bin/element-test run Dvizhok.xdump --assignment assignments/dvizhok-declarative-bindings --output result/my-bindings-run
```

`run_batches.py` выполняет два свежих batch только по пяти переносимым
критериям: правильная работа → мутация колонки → другая правильная работа.
Для сохранности свидетельств скрипт отказывается перезаписывать batch-1/2.
`verify_public.py` (зависимость jsonschema) проверяет публичные JSON-пакеты,
сопоставляет факты/баллы test/run и подтверждает неизменность архива.
`update_report.py` пересоздаёт карту и измерения из реальных журналов.

Для №38 публичные Dvizhok проверки выполнены двумя пакетами: первоначальные
453 критерия и добавленные 32 собственных свойства/defaults. Единое текущее
задание содержит 485 проверок. Старые 44 метода не переисполнялись целиком;
карта сохраняет происхождение №36/37. Полная регрессия остаётся nightly.
