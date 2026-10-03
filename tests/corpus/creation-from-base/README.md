# Прямые бизнес-корни №37

Fixtures и независимые ответы: `tests/creation_fixtures.py`.
Публичное задание: `assignments/dvizhok-remaining-business-roots`.
Переносимые ledgers/accounts проверяют другие имена, полную проектную
квалификацию, import alias, два одинаковых кратких owner, nullable/union,
свежие загрузки после мутации и собственные методы/локальное затенение.

Целевые проверки (без полного набора):

```sh
PYTHONPATH=tests:. python3 -m unittest test_creation_from_base.CreationPlanTest -v
ELEMENT_TEST_DOCKER_TESTS=1 ELEMENT_TEST_INTEGRATION_TESTS=1 \
  PYTHONPATH=tests:. python3 -m unittest test_creation_from_base -v
```

Docker/SQL требуют локальные Script runtimes и временный
ELEMENT_TEST_INTEGRATION_PASSWORD. Не сохраняйте пароль в файлах/журналах.
Ночные тесты автоматически обнаруживают test_creation_from_base.py.

```sh
bin/element-test test --project Dvizhok.xdump \
  --assignment assignments/dvizhok-remaining-business-roots \
  --output result/dvizhok-remaining-business-roots/test --integration
bin/element-test run --project Dvizhok.xdump \
  --assignment assignments/dvizhok-remaining-business-roots \
  --output result/dvizhok-remaining-business-roots/run --integration
```

Полный контракт: [creation-from-base.md](../../../docs/creation-from-base.md).
Ошибки отсутствующего основания проверяются отдельно как ERROR с наблюдениями,
без выдачи баллов. Evidence и первичные отказы сохраняются в result/.
