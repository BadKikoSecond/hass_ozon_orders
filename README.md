# Ozon Orders — Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Неофициальная интеграция **личного кабинета покупателя Ozon** для Home Assistant.

Отслеживает активные заказы: статус, срок доставки, готовность в ПВЗ, срок хранения (если есть в ответе API), оплату и срок жизни cookies-сессии.

> **Важно:** Ozon не предоставляет публичный API для покупателей. Интеграция использует тот же внутренний `entrypoint-api`, что и сайт `ozon.ru`, с cookies вашей браузерной сессии. Это может перестать работать без предупреждения.

<p align="center">
  <img src="custom_components/ozon_orders/logo.png" alt="Ozon" width="120" />
</p>

---

## Возможности

| Сущность | Назначение |
|----------|------------|
| `sensor.*_active_orders` | Сколько активных заказов на странице |
| `sensor.*_at_pickup` | Сколько отправлений в ПВЗ |
| `sensor.*_in_transit` | Сколько в пути / в доставке |
| `sensor.*_session_expires` | Когда истекает сессия (по cookies) |
| `binary_sensor.*_session_valid` | Сессия жива / ошибка опроса |
| `sensor.*_order_*_status` | Статус конкретного отправления + атрибуты |
| `binary_sensor.*_order_*_at_pickup` | Удобно для автоматизаций «забери посылку» |
| `binary_sensor.*_order_*_in_transit` | Удобно для «скоро приедет» |

### Атрибуты сенсора статуса заказа

- `order_number` — номер заказа (`50761504-0120`)
- `eta` — ожидаемая дата / «Сегодня с 09:00…» / «Хранится до…»
- `delivery_type` — тип доставки / адрес ПВЗ
- `storage_until` — текст срока хранения (если распознан)
- `products_count` — число товаров в плитке
- `payment_status` — Оплачен / Не оплачен
- `detail_url` — ссылка на заказ на ozon.ru
- `is_at_pickup_point`, `is_in_transit`

---

## Установка

### HACS (рекомендуется)

1. HACS → **Интеграции** → три точки → **Пользовательские репозитории**
2. URL репозитория + категория **Integration**
3. Установить **Ozon Orders**
4. Перезагрузить Home Assistant

### Вручную

Скопируйте папку `custom_components/ozon_orders` в `/config/custom_components/` и перезагрузите HA.

---

## Настройка cookies

1. Залогиньтесь на [ozon.ru](https://www.ozon.ru) в браузере на ПК
2. Установите расширение **Cookie-Editor** или **EditThisCookie**
3. На странице ozon.ru → Export → **JSON** (весь массив целиком)
4. **Настройки → Устройства и службы → Добавить → Ozon Orders**
5. Вставьте JSON в поле **Cookies (JSON)** и подтвердите

Нужны все cookies домена `.ozon.ru`, включая `__Secure-access-token`.

---

## Служба

```yaml
service: ozon_orders.refresh
```

Принудительно обновить данные со всех настроенных аккаунтов.

---

## Примеры автоматизаций

### Уведомление, когда посылка в ПВЗ

```yaml
alias: Ozon — можно забирать
trigger:
  - platform: state
    entity_id: binary_sensor.ozon_order_50761504_0120_0_at_pickup
    to: "on"
action:
  - service: notify.mobile_app_phone
    data:
      title: "Ozon"
      message: >
        {{ state_attr('sensor.ozon_order_50761504_0120_0_status', 'delivery_type') }}
        — {{ states('sensor.ozon_order_50761504_0120_0_status') }}
```

### Сессия скоро истечёт

```yaml
alias: Ozon — обнови cookies
trigger:
  - platform: numeric_state
    entity_id: sensor.ozon_session_expires
    attribute: days_remaining
    below: 7
action:
  - service: notify.persistent_notification
    data:
      title: "Ozon Orders"
      message: "Осталось меньше недели до истечения cookies. Пересоздайте интеграцию с новым JSON."
```

### Сессия отвалилась

```yaml
trigger:
  - platform: state
    entity_id: binary_sensor.ozon_session_valid
    to: "off"
action:
  - service: notify.mobile_app_phone
    data:
      message: "Ozon: сессия недействительна, обнови cookies"
```

---

## Нюансы и ограничения

### Не Seller API

Интеграция для **покупателя**, не для продавца. `api-seller.ozon.ru` здесь не используется.

### Cookies и antibot

- Запросы идут с HA на `www.ozon.ru` **без браузера** — только cookies + заголовки Chrome.
- Обычно работает, но Ozon (Variti) может выдать **403 / puzzle** — тогда обновите cookies из браузера, где вы уже прошли проверку.
- С другого IP / VPN сессия может умереть раньше.

### Срок сессии

Сенсор `session_expires` читает `expirationDate` из cookies (`__Secure-access-token` и др.).  
Это **срок в файле cookies**, не юридическая гарантия Ozon — сервер может разлогинить раньше.

Типичный сценарий: **раз в несколько месяцев / до года** обновить cookies (удалить и заново добавить интеграцию).

### Один заказ — несколько отправлений

Если в одном заказе два shipment (часть в ПВЗ, часть в пути), на странице Ozon будет **две плитки** с одним `order_number` — интеграция создаёт **две группы сущностей** (`…_0_…`, `…_1_…`).

### Зависимости

Только `aiohttp` — уже есть в Home Assistant, отдельно ничего ставить не нужно.

### «Invalid handler specified» при добавлении

Обычно значит, что **config flow не загрузился** (ошибка импорта), а не что интеграция «сломана» в UI.

1. **Проверь путь установки** — папка должна называться ровно `ozon_orders`:
   ```
   /config/custom_components/ozon_orders/manifest.json
   ```
   Неправильно: `custom_components/hass_ozon_orders/` (имя репозитория ≠ domain).

2. **Логи HA:** Настройки → Система → Логи → искать `ozon_orders` или `config_flow`.  
   Типично: `Exception importing custom_components.ozon_orders.config_flow`.

3. **Перезагрузи HA** после копирования/HACS (Настройки → Система → Перезапуск).

4. Обнови интеграцию до **v1.0.1+** (фикс совместимости Python 3.10).

---

Файл cookies = полный доступ к аккаунту Ozon. Храните только в `/config`, не коммитьте в git, ограничьте бэкапы.

---

## CLI (для отладки вне HA)

В корне репозитория есть `cli.py` и пакет `ozon_orders/`:

```bash
python cli.py --cookies cookies.json --active-only
```

---

## Иконка

Используется официальный favicon Ozon (`ozon.ru`). Фирменные материалы: [brandlab.ozon.ru](https://brandlab.ozon.ru).

---

## Лицензия

MIT. Не аффилировано с Ozon. Только для личного использования.
