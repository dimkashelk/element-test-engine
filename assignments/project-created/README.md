# Проект создан

Одна структурная проверка на 1 балл для задания «Начало работы».
Загрузчик требует читаемый `Проект.yaml`, а критерий `project_created`
проверяет наличие модуля `Проект.xbsl` в корне выбранного проекта
(пустое пространство имён). Имя, поставщик, версия, UUID и состав
объектов проекта не ограничены.

Запуск из корня репозитория:

```bash
ELEMENT_TEST_PYTHON=.venv/bin/python bin/element-test test \
  --project '/path/to/dump_practice.xdump' \
  --assignment assignments/project-created \
  --output result/project-created
```

Для другой работы замените путь в `--project`.
Результаты: `result/project-created/result.json` и
`result/project-created/report.html`.

Отсутствующий `Проект.xbsl` даёт `FAIL` и 0/1 баллов.
Отсутствующий или нечитаемый `Проект.yaml` отклоняется загрузчиком
до выставления оценки. Проверка не требует запуска приложения или Docker.
