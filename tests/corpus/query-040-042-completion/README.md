# Доработка заданий №40–42

Справка Элемента 9.3, установленный Script 10.0.2-1, профиль `current`.
Это приёмка общего executor-адаптера, а не native XBQL или платформенного UI.
Исторические baseline/измерения №40–42 и исходные архивы сохранены.

Независимые исходные данные/ответы заданы в
`tests/query_completion_fixtures.py` до запуска источников. Два проекта отличаются
namespace, владельцами, именами полей и точкой входа. Python строит AST;
выражения, строки запросов и сравнение исполняет Script/SBSL. Expected не входит
в IR. Fixtures не содержат готовых query rows и не подменяют реальный источник.

Проверены шесть точных review templates JOIN/NULL, функции ПервыйНеNull,
ПолноеСовпадение, Ууид, свойства даты, математика и степень, квалификаторы
временных полей, defaults/computed/auto, INSERT VALUES/SELECT, UPDATE,
повторное Выполнить и порядок захвата параметров. Точный внешний
ПродажиПоНеделям.xbql исполняется через отдельную literal-обёртку копии архива;
сам файл и архив неизменны. Query-only снимок содержит типизированные поля,
опуская неиспользуемое неявно типизированное Файлы; файл/объектные API остаются
неподдержанными.

48 различных тестовых методов прошли ограниченные прогоны: 13 новых и 35
затронутых прежних. Шесть исполнимых мутаций получили SBSL FAIL; эквивалентное
вычисление прошло. Публичные test/run нового задания — 18/18, контроль —
FAIL → UNSUPPORTED → PASS (unavailablePoints=1). Два свежих batch — 3/0/3,
cacheHit=false. Четыре PostgreSQL-сценария, включая реальный отчёт, проверены
независимым чтением и SBSL; cleanup успешен, исходник не commit. 56 артефактов
grading/batch/events/manifest прошли схемы.

Воспроизведение новых тестов из корня репозитория (Docker opt-in):

```sh
ELEMENT_TEST_DOCKER_TESTS=1 ELEMENT_TEST_INTEGRATION_TESTS=1 PYTHONPATH=tests:. \
  python3 tests/corpus/query-040-042-completion/run_completion.py
```

Только планировщик без Docker:

```sh
PYTHONPATH=tests:. python3 tests/corpus/query-040-042-completion/run_completion.py \
  test_query_completion.CompletionPlanTest
```

Точный список затронутых прежних тестов и SHA-256 успешных журналов находится в
`docs/query-stage-040-042-completion-measurements.json`, поле
`tests.latestSuccessfulEvidence`. Runner перенаправляет свидетельства прежних
№40–42 в `result/query-040-042-completion/fresh-040`, `fresh-041`, `fresh-042`,
сохраняя исторические результаты. Для прежних №44–45 выбираются только точные
методы. В первых затронутых прогонах они записали evidence по прежним путям;
это отклонение отмечено в измерениях. Runner теперь перенаправляет также их
в fresh-044/fresh-045.

Нативные пробы основных Script-примитивов:

```sh
bin/script-runtime -c current tests/corpus/query-040-042-completion/native/Functions.sbsl
bin/script-runtime -c current tests/corpus/query-040-042-completion/native/Pattern.sbsl
```

После успешных прогонов `record_completion.py` проверяет сохранённые
sourceHash/projectSourceHash/range, архивные SHA-256, независимые receipts и
JSON Schema. Для него нужен `jsonschema` в Python; в текущей приёмке использован
уже существующий `/private/tmp/element-query-fill-schema`. Скрипт не выполняет
discovery или код проектов. Документационные страницы не получают A=1 на
основании семейства переносимых тестов. Исторические измерения не переписываются.

Имена успешных журналов можно передать `record_completion.py` аргументами.
Для повтора всех 48 точных методов без discovery взять ключи
`tests.latestSuccessfulEvidence` из сохранённых измерений и передать их runner:

```python
import json, runpy, sys
from pathlib import Path
report = json.loads(Path("docs/query-stage-040-042-completion-measurements.json").read_text())
sys.argv = ["run_completion.py", *report["tests"]["latestSuccessfulEvidence"]]
runpy.run_path("tests/corpus/query-040-042-completion/run_completion.py", run_name="__main__")
```

Запустить этот код с обоими opt-in переменными и `PYTHONPATH=tests:.`, сохранив
stderr в `result/query-040-042-completion/reproduced.stderr`. Затем передать
`reproduced.stderr` в `record_completion.py` с доступным jsonschema. Скрипт
свяжет только успешные exact receipts и проверит все 48 выбранных методов.

Ограничения: полный query-каталог, native XBQL/отчёты/права/файловый сервис,
физические индексы, union-типы временных полей, defaults от auto-поля и
недетерминированные/captured defaults. Auto-счётчик локален одному Выполнить;
DELETE/ОБРЕЗАТЬ его сохраняют, уничтожение/создание сбрасывают. Степень основания
с неизвестной дробностью использует девять знаков. SQL NULL и Неопределено
различаются в relational AST, но оба сериализуются JSON null за его пределами.

Полный discovery, 303 формы, 44 прежних корня, четыре validate и полная
регрессия не запускались. Архивы, прежние assignment expected, лимиты и
расписание CI не менялись; запуск nightly не заявляется. Первичные сбои,
отдельные повторы и успешные журналы находятся в
`result/query-040-042-completion/`; их хеши включены в новые измерения.
