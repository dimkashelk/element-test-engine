# Технический корпус №53

`ordinary` — `Ware::Ledger`; `renamed` — `Запасы::История`. В каждом проекте
три измерения Product/Supplier/Channel, nullable Supplier, период Момент,
ссылочный реквизит Link, второй регистр Mirror и документ Log с исходным
ПослеЗаписи. Наборы реально читаются и записываются в Script.

Исходные данные и независимые ожидания создаёт `tests/partial_filter_fixtures.py`.
Сценарии проверяют частичное/полное чтение, NULL, повторную смену маски,
отдельные снимки строк/ссылок, повторное/пустое замещение, добавление истории,
свежий запрос/срез, конфликт ключей и откат объекта с двумя регистрами.
Суммы/счётчики не зависят от порядка обхода. Массивы хранения сравниваются
как мультимножества в SBSL.

Воспроизведение:

```sh
python3 tests/partial_filter_fixtures.py
ELEMENT_TEST_DOCKER_TESTS=1 ELEMENT_TEST_INTEGRATION_TESTS=1 \
  python3 -m unittest discover -s tests -p test_partial_information_filters.py -v
```

Контракт и границы: `docs/partial-information-register-filters.md`.
Бизнес-приёмка Dvizhok-final.xdump относится к №56.
