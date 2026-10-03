# №39 — типизированное заполнение

`build_fixtures.py` воспроизводит ordinary (Справочник) и daily (СрезПоследних)
с независимыми именами, Data/Other owners, alias, обязательным readonly полем,
скалярными/nullable/ref/enum полями и авторским default. `tests/fill_fixtures.py`
содержит заранее заданные истории и expected, не вычисляя ответы по исходнику.

`native-probes` и `run_native.py` фиксируют constructor/defaults/readonly/обз,
widening, runtime отказ nullable narrowing и отсутствие native Запрос в Script
10.0.2-1 / -c 9.0, включая первичные неудачные пробы.

`documented-contracts.json` сохраняет официальные версии источников, все
семейства оглавления и отдельные review-templates. Документация 9.1 выбрана
пользователем для совместимости 9.0. Будущие review-templates не объявлены
исполняемыми сценариями. Журналы и независимые критерии обязательны для PASS.

Доказательства: `result/storage-query-fill`. Контракт и команды:
[docs/storage-query-fill.md](../../../docs/storage-query-fill.md).
