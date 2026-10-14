# Контракты секретов

В Git хранится только этот документ. Реальные файлы создаёт оператор при
реализации и развёртывании. Здесь нет рабочих паролей и токенов.

- `db_password`: одна непустая строка. Compose secret читает PostgreSQL и сервис.
- `integration_password`: одна непустая строка. Worker передаёт значение только
  процессу текущей SQL-проверки как `ELEMENT_TEST_INTEGRATION_PASSWORD`; в общем
  окружении API и notifier его нет. Это начальный пароль временной БД проверки,
  не пароль управляющей БД `autocheck`.
- `api_clients.json`: описанные ниже клиентские записи. Токены — случайные
  256-битные значения; сохраняются только SHA-256 токенов, сравнение constant-time.
- `webhook_clients.json`: фиксированные URL и HMAC-ключи доставки по `clientId`.
  Клиент не выбирает URL callback в запросе. HTTPS, проверка сертификата, без
  redirects. Доступ к этому секрету есть только у notifier.

Структура `api_clients.json` (значения в угловых скобках — описания, не секреты):

```json
{
  "clients": [
    {
      "clientId": "lk-main",
      "tokens": [
        {
          "keyId": "lk-2026-01",
          "tokenSha256": "<64 hex characters>",
          "scopes": ["checks:read", "checks:write"],
          "enabled": true
        }
      ],
      "allowedRunModes": ["isolated", "integration"]
    },
    {
      "clientId": "operations",
      "tokens": [
        {
          "keyId": "ops-2026-01",
          "tokenSha256": "<64 hex characters>",
          "scopes": ["settings:read", "settings:write", "delivery:retry"],
          "enabled": true
        }
      ],
      "allowedRunModes": []
    }
  ]
}
```

Структура `webhook_clients.json`:

```json
{
  "clients": [
    {
      "clientId": "lk-main",
      "url": "https://lk.example.edu/internal/autocheck/events",
      "currentKeyId": "callback-2026-01",
      "keys": {
        "callback-2026-01": "<random HMAC secret>"
      }
    }
  ]
}
```

Сервис перечитывает конфигурацию ключей при атомарной замене файла. Если Compose
bind mount не видит замену inode, соответствующий сервис пересоздают; обе стороны
сохраняют старый ключ на период ротации. Невалидная новая конфигурация не заменяет
последнюю валидную. Старт без валидных секретов запрещён. При допуске reserve API
также проверяет, что `clientId` находится в списке получателей доставки из
несекретного реестра в БД; этот реестр синхронизирует notifier. До его появления
admission/readiness недоступны, но процесс API запускается: порядок старта
API/notifier не должен создавать взаимную блокировку.

UID 10001 должен иметь доступ на чтение к секретам API, notifier и migrate.
File-based Compose secrets в локальном Compose используют bind mounts: параметры
`uid/gid/mode` не заменяют настройку прав исходного файла. Можно хранить файлы на
хосте с владельцем 10001 и правами 0400; worker/root также сможет их прочитать.
