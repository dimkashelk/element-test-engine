# Контракты типов проверок v1

№46 реализует спецификацию и read-only карту инфраструктуры.
№47 реализует [исходный query entry](source-query-execution.md), №48 —
[управляемые часы и таймеры](timer-events.md); №49–52 остаются в очереди.
Семейства не добавляются в YAML `type`.
Результаты исполнения/баллы по Dvizhok-final не формируются.

Модель: [реестр](test-type-contracts-v1.json),
[схема контракта](test-type-contract-v1.schema.json),
[схема карты](capability-map-v1.schema.json).
Реализация: `element_test.test_contracts` и `element_test.capability_map`.
`validate_contract` проверяет поля, версию, типы, независимый oracle, имена входов,
операции семейства, изоляцию и привязку свидетельств к текущему sourceHash.
`validate_map` проверяет схему, идентичности, профили и ссылки между вхождениями.
Встроенный валидатор реализует только набор ключевых слов этих схем v1;
это не API для проверки произвольных JSON Schema.

## Общая модель

Контракт содержит `schemaVersion`, `family`, `selector`, `profile`, типизированные
`inputs`, `actions`, `observations`, независимый `comparison`, `dependencies`,
`unavailableReasonCodes`, `isolation` и `evidence`.
`selector` хранит относительный файл, SHA-256 файла, SHA-256 проекта,
владельца (namespace/name/kind), полуоткрытый диапазон символов `[start,end)`,
строку/колонку от единицы и путь YAML от корня. Позиции относятся к тексту
UTF-8 после удаления BOM, а SHA-256 — к исходным байтам. Равные тексты в разных
методах и разные значения в одном YAML сохраняют разные идентичности.

`profile` отделяет режим исходника от адаптера и справки запросов. Для этого
архива исходник имеет режим 9.0, справка — 9.3, adapter profile — current.
`versionApplicability: not-checked` не означает подтверждения платформенной
семантики 9.0 сведениями из справки 9.3.

Действия ссылаются только на объявленные входы. Сравнение задаёт автор независимо
от проверяемого исходника и выполняет SBSL. Спецификация контракта не содержит
конкретных входных значений, expected, criterionId или points. Объявление oracle
не является его исполнением. Фактическую оценку реализуют будущие адаптеры через
общие `test`/`run`/`batch`; текущие команды и схемы результатов сохранены.

Изоляция: новая check-session, fresh storage, явно заданные часы и отмена callbacks
при закрытии. №48 реализует это для собственного декларативно разрешённого
callback; порядок событий, равные моменты, повторное подключение и отключение
внутри callback определены в [исполняемом контракте](timer-events.md). Покрытие ветвей и результаты
инструментализации — технические наблюдения, без дополнительных бизнес-баллов.

## Решения по API

| Семейство | Решение | Входы и действия | Наблюдения и границы |
|---|---|---|---|
| method | Переиспользовать runtime | Типизированные args/context/storage; исходный вызов/sequence | Возврат, состояние, эффекты; независимое сравнение SBSL |
| query | Runtime с querySource (№47 реализовано) | Литерал или XBQL, типизированные параметры, storage; execute/repeat after write | Типизированные строки, NULL, cardinality, колонки и ресурсы; точная исходная идентичность |
| time-event | Runtime с timers (№48 реализовано) | Фиксированные часы, зона, локаль; исходные register/cancel, advance/next/close | Активные callbacks, снимки состояния и закрытие; динамические/чужие callbacks за границей |
| interface | Расширить runtime/formRequirements | Селектор роли/типа/привязки, ввод, контекст; input/recalculate/handler/open/write | Структура, значения привязок, состояние, сохранённые эффекты раздельно; browser DOM за границей |
| metadata-lifecycle | Расширить element/field/method/runtime и storage | Метаданные, новое/существующее состояние, write-параметры; validate/write/reload/rollback | Декларация ограничения, отказ, свежий снимок объекта/регистров; enforcement требует №50 |
| report | Расширить element/runtime, добавить report IR | Селектор отчёта, параметры, storage; query/evaluate layout | Данные, формулы, роли, иерархия и сортировка; формулы не считаются XBSL-привязками |
| navigation-panel | Расширить element/runtime | Фрагмент/панель, контекст видимости; resolve/open/refresh | Маршрут, доступность, виджеты, расположение, refresh; встроенные native формы/рендеринг отдельно |
| branches-invariants | Расширить наблюдения runtime | Seed, типизированные домены, независимое отношение; instrument/generate/shrink | Исходы ветвей, seed и контрпример; сохранение однократного вычисления и short circuit требует №52 |

Отдельного публичного YAML типа на этом этапе не требуется ни одному семейству.
Если при реализации самостоятельный source/report entry нельзя выразить расширением
runtime, новый публичный тип потребуется отдельным решением с реализацией, валидацией
и технической приёмкой. `formRequirements` — существующий раздел задания, не YAML `type`.

## Стадии готовности

Каждое вхождение имеет отдельные discovery, parsing, planning, adapter и
technicalAcceptance со status/basis/reasonCode/detail. Статусы:
`ready`, `blocked`, `not-checked`, `not-applicable`. Здесь `ready` означает готовность
конкретной стадии, не PASS приложения. Для declaration parsing готовность означает
разбор YAML, но не интерпретацию вложенной формулы отчёта.

Реестр возможностей различает `scoped` (существует ограниченная реализация) и
`extension-required` (нужно расширение). Наличие реализации или подготовленного
плана не доказывает поддержку конкретного исходника адаптером. Поэтому adapter
остаётся `not-checked` либо имеет известный блокирующий пробел; technicalAcceptance
всех вхождений здесь `not-checked`.

`firstBlocker` показывает первую известную блокировку в порядке стадий.
При ошибке разбора запросов planning остаётся неизвестным. В №47 устранены
пробел входа XBQL, среза без скобок и смешанного CASE. Все пять query-форм
проходят разбор/генерацию; adapter и technicalAcceptance исходников остаются
not-checked до независимого исполнения. Приёмка механизма хранится отдельно.
`authorScenario` хранится отдельно: отсутствие авторского сценария не означает
отсутствия возможности движка. Старое исполнение не импортируется; прежние планы
можно использовать только при совпадении sourceHash и полного набора точных методов.

Коды причин перечислены в реестре. Они относятся к инфраструктурной карте и
не заменяют `reasonCode` существующих grading/result контрактов.

## Анализатор

Анализатор переиспользует coverage/method inventory, query source_records,
inspect_query и существующий разбор XBSL. В запросах проверяется только AST и
генерация; Script, PostgreSQL и приложение не исполняются. YAML-позиции получены
из узлов исходного документа, поэтому сохраняются повторные ключи в разных
ветках и многострочные формулы. Ссылки разрешаются через namespace/import/visibility.

Категории: методы, исходные запросы, UI-декларации, bindings, handlers,
system-action, report/report-source/report-field/report-formula,
navigation/navigation-link, panel/panel-widget/panel-refresh,
metadata-constraint, platform-call, dynamic-component и branch.
Из известных платформенных обращений обнаруживаются часы/таймеры,
запись/чтение/загрузка объектов и регистров, чтение констант, открытие/закрытие.
Вызовы с непроверенным типом receiver помечены как API candidates;
совпадение имени не доказывает native семантику. Динамическая `Надпись` также
остаётся кандидатом до проверки адаптером. Счётчик branch отражает синтаксические
конструкции, не исходы условий и не фактическое покрытие.

Системные команды классифицируются по стандартной операции; это discovery,
не доказательство выполнения команды. Прочие `=` остаются bindings. Формулы
макетов и декларации ролей имеют собственные счётчики. Native автоматические формы
сохраняют отдельный признак `nativeSurface`; неразрешённая ссылка не выбирает
произвольный одноимённый объект.

## Воспроизведение

Без авторских сценариев:

```bash
python3 -m element_test.capability_map --project Dvizhok-final.xdump \
  --output result/dvizhok-final-capabilities
```

С подготовкой существующих планов для точного нового исходника:

```bash
python3 -m element_test.capability_map --project Dvizhok-final.xdump \
  --assignments assignments --output result/dvizhok-final-capabilities
```

Повторное использование прежнего sourceHash-matched инвентаря этой выгрузки:

```bash
python3 -m element_test.capability_map --project Dvizhok-final.xdump \
  --method-inventory result/dvizhok-final-assessment/coverage/coverage.json \
  --output result/dvizhok-final-capabilities
```

Выход: capability-map.json/md, реестр и две схемы. `--assignments` и
`--method-inventory` взаимоисключающие. Можно выбрать проект составного архива
через `--project-name` и adapter profile через `--adapter-profile`.
Архив читается с проверкой неизменности его SHA-256.

Сохранённая [карта финальной выгрузки](dvizhok-final-capability-map.json) и
[измерения №46](test-type-stage-046-measurements.json) фиксируют discovery,
подготовку и ограничения. [№47 реализовано](tasks/047-source-query-entry.md),
[измерения механизма](test-type-stage-047-measurements.json) отделены от исходной приёмки.
[№48 реализовано](tasks/048-managed-timer-events.md); [измерения механизма](test-type-stage-048-measurements.json).
Очередь №49–52 сохраняет зависимости и первые блокировки. Следующий этап — P1 №49.
