# Подключение внешних систем

В примерах используется `https://autocheck.example.com/autocheck`. Подставьте адрес своего развёртывания с действующим TLS-сертификатом.
Существующий API на этом сервере сохраняет свои маршруты.

Каждая система получает отдельный `clientId`, Bearer-токен и HMAC-ключ webhook.
Результаты, номера работ и ключи идемпотентности изолированы по `clientId`.
Общий лимит сервера — одна исполняемая работа; при 429 повторите запрос позднее.

## Запуск проверки

1. Вычислите SHA-256 и размеры исходного `.xdump` и YAML/ZIP с критериями.
2. Отправьте `POST /v1/reservations` относительно базового URL:

```http
POST /autocheck/v1/reservations HTTP/1.1
Host: autocheck.example.com
Authorization: Bearer <apiToken>
Idempotency-Key: reserve-work-123-attempt-1
Content-Type: application/json
```

```json
{
  "submissionId": "work-123",
  "attempt": 1,
  "studentId": "student-42",
  "assignmentId": "task-1",
  "runMode": "isolated",
  "webhookUrl": "https://your-system.example/api/autocheck-result",
  "dump": {"format": "xdump", "sizeBytes": 123, "sha256": "<реальные 64 hex символа>"},
  "criteria": {"format": "yaml", "sizeBytes": 456, "sha256": "<реальные 64 hex символа>"}
}
```

3. Из ответа 201 возьмите `reservationId`. В течение 120 секунд отправьте
   `POST /v1/checks` с `Authorization`, новым `Idempotency-Key`, заголовком
   `X-Reservation-Id` и multipart-полями `dump` и `criteria`. Ответ 202 содержит `checkId`.
4. Примите JSON webhook на адрес из `webhookUrl`. Результат также доступен через
   `GET /v1/checks/{checkId}` с токеном отправившей системы.

При повторе запроса с тем же ключом идемпотентности сохраняйте прежний manifest.
Поле `criteriaVersion` принимается для совместимости и возвращается в результате,
если его передал отправитель. Файл критериев идентифицируется по SHA-256.

Пути `uploadUrl`, `statusUrl` и ссылки на артефакты в JSON относятся к API base:
соединяйте строкой `baseUrl + returnedPath`, сохраняя префикс `/autocheck`.
Контракт: [OpenAPI](autocheck-openapi.yaml).

## Критерии задания «Программирование»

Соберите файлы критериев командой `python tools/ci/package_programming.py` из корня репозитория. В поле `criteria` отправьте файл `output/programming/Критерии_Программирование.yaml`
или одноимённый ZIP с `assignment.yaml` в корне. В manifest укажите
`runMode: isolated`. Передавайте размер и
SHA-256 фактически отправленного файла. Список `checks` задаёт все 320
обязательных критериев, включая 140 сценариев исполнения. Файл с суффиксом
`_с_дополнительным` содержит 326 критериев.

Встроенный профиль `programming-practice` версии `"1"` остаётся доступен
для ранее скачанных ZIP. Для новых заданий можно передавать собственный
список `checks` и при необходимости `executionOptions`. Проверки, баллы,
ожидаемые результаты и правила разрешения имён находятся в YAML.

Пример проверки метаданных с одним баллом:

```yaml
name: Проверка документа
checks:
  - id: order-source
    type: assertion
    points: 1
    failureExplanation: Заказ должен создаваться на основании сделки
    assertion:
      all:
        - query:
            from: element
            owner: Продажи::Заказ
          some:
            - path: [СозданиеНаОсновании]
              contains: Сделка.Ссылка
```

`assertion.all` требует выполнить каждое условие. `query.from` выбирает
`project`, `element` по `owner`, `module` по `owner`, `method` по
`owner` и `method` или `execution` по ID runtime-критерия в поле `criterion`.
Метод предоставляет `parameterTypes`, `returnType`, `annotations`, `tokens`,
`bodyTokens` и `calls`. Для результата запроса доступны `count`, `some`
(есть совпадение) и `every` (непустой набор и совпадают все).

Шаг `path` извлекает поле или элемент по индексу. `items: true` раскрывает
массив, `walk: true` находит вложенные объекты, `where` оставляет совпадения.
В фильтре `path` можно опустить, чтобы сравнить сам результат. Операции
сравнения — `eq`, `contains`, `prefix`, `suffix`, `exists`, `typeEq`,
`typeEqLocal` и `setEq`. Дополнительные шаги `withoutIds: true`,
`distinctStrings: true` и `stripPrefix` применяются к данным запроса.

`executionOptions.aliases` задаёт явную карту `owners` и `names`.
`executionOptions.resolveAliases` задаёт `owner`, необязательное `name`,
запрос `select` и необязательный запрос `unless`. Автоматическое правило
применяется при единственном результате. В запросах можно ссылаться на
разрешённый идентификатор через `{$owner: "Раздел::Форма"}` или
`{$name: ["Раздел::Форма", "Метод"]}`. Опции `lowerLocalOverloads` и
`missingMethodFails` включают соответственно адаптацию перегрузок разной
арности в рабочей копии и оценку отсутствующего runtime-метода как `FAIL`.

Эталон для обязательного набора должен получить `passed`, 320/320,
`unavailablePoints: 0`. Публикация из IDE, внешний вид интерфейса и
работа с отладчиком оцениваются отдельно. JSON-артефакты и webhook
используют существующий формат.

`webhookUrl` может содержать публичный IP вместо домена, если у получателя есть
валидный HTTPS-сертификат. Запрещены HTTP, localhost, частные/link-local IP,
URL с логином/паролем, fragment и перенаправления. DNS проверяется при каждой
попытке; TCP соединяется с проверенным IP, TLS проверяет исходное имя получателя.
Адрес сохраняется с работой. Повтор reservation с тем же ключом и другим адресом
даёт 409. Для совместимости поле необязательно: без него используется legacy URL
из настроек клиента; если нет обоих адресов, доставка получает `delivery_url_missing`,
а результат остаётся доступен через GET. Новые интеграции всегда передают поле.

## Проверка webhook

Сохраните исходные байты тела до JSON-декодирования. Проверьте обязательные заголовки
`X-Autocheck-Event-Id`, `X-Autocheck-Timestamp`, `X-Autocheck-Key-Id` и
`X-Autocheck-Signature`:

```python
import hashlib
import hmac
import time

assert abs(time.time() - int(timestamp)) <= 300
expected = "v1=" + hmac.new(
    webhook_secret.encode(),
    f"{timestamp}.{event_id}.".encode() + raw_body,
    hashlib.sha256,
).hexdigest()
assert hmac.compare_digest(expected, signature)
```

Выберите ключ по `keyId`, сверьте `eventId` заголовка с JSON. Атомарно сохраните
событие и результат, затем верните 204. Повтор того же `eventId` также получает 204.
Временные ошибки 408/429/5xx повторяются до 7 дней; оценивание повторно не запускается.

## Регистрация ещё одной системы на сервере

```bash
sudo python3 /opt/element-autocheck/current/tools/ci/autocheck_add_client.py my-system \
  --base-url https://autocheck.example.com/autocheck \
  --credentials "$HOME/.config/element-autocheck/my-system.json" \
  --owner-uid "$(id -u)" --owner-gid "$(id -g)"
sudo /opt/element-autocheck/compose up -d --no-deps --force-recreate --wait api notifier
```

Файл credentials содержит `apiToken`, `webhookKeyId`, `webhookSecret`; права 0600.
Передайте его только владельцу соответствующей системы. Значения не хранятся в Git.
Скрипт отказывается перезаписывать существующего клиента или credentials.
