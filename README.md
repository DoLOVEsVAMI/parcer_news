# VK news parser (explicit ads filter)

Скрипт парсит посты из новостных пабликов ВКонтакте и классифицирует каждый пост по вашему JSON-пресету:
- `explicit_ad` — есть сильные маркеры рекламы (или набран порог score),
- `not_explicit_ad` — явной рекламы нет.

## Нужен ли API токен?

Не обязательно.

Поддерживаются режимы:
- `--source-mode api` — **официальный VK API** (`utils.resolveScreenName` + `wall.get`), **нужен `VK_TOKEN`**.
- `--source-mode web` — парсинг **публичных страниц** через `m.vk.com`, **токен не нужен**.
- `--source-mode auto` (по умолчанию) — если есть токен, используется API; если токена нет, используется web-режим.

## Быстрый запуск (без API токена)

```bash
python vk_news_parser.py \
  --source-mode web \
  --domains lenta_ru meduzalive \
  --count 20 \
  --rules-json rules.strict_explicit_ads_min_fp.json \
  --pretty
```

## Быстрый запуск (через API)

```bash
export VK_TOKEN='ваш_токен'
python vk_news_parser.py \
  --source-mode api \
  --domains lenta_ru meduzalive \
  --count 20 \
  --rules-json rules.strict_explicit_ads_min_fp.json \
  --pretty
```


## Запуск в Windows PowerShell

Важно: не вставляйте аргументы по одной строке отдельно. Команда должна быть одной командой.

**Вариант 1: одной строкой (проще всего)**

```powershell
python vk_news_parser.py --source-mode web --domains lenta_ru https://vk.com/rsportru --count 20 --pretty
```

**Вариант 2: переносы строк в PowerShell** (используйте обратную кавычку `` ` ``, а не `\`)

```powershell
python vk_news_parser.py `
  --source-mode web `
  --domains lenta_ru https://vk.com/rsportru `
  --count 20 `
  --pretty
```

Если нужен API-режим:

```powershell
$env:VK_TOKEN = "ваш_токен"
python vk_news_parser.py --source-mode api --domains lenta_ru https://vk.com/rsportru --count 20 --pretty
```

## Аргументы

- `--token` — VK API token (для `--source-mode api`; или переменная `VK_TOKEN`)
- `--domains` — список пабликов: `domain`, URL или `owner_id`
- `--count` — число постов на каждый паблик
- `--source-mode` — `auto | api | web`
- `--rules-json` — JSON с правилами (по умолчанию встроенный пресет)
- `--only-not-blocked` — вывести только `not_explicit_ad`
- `--pretty` — красивый JSON вывод

## Формат результата

```json
[
  {
    "source": "lenta_ru",
    "source_mode": "web",
    "owner_id": -29684145,
    "post_id": 123456,
    "date": "2026-02-15T10:00:00+03:00",
    "text": "...",
    "label": "not_explicit_ad",
    "blocked": false,
    "matched_rule_ids": [],
    "matched_fragments": [],
    "score": 0,
    "post_url": "https://vk.com/wall-29684145_123456"
  }
]
```

## Ограничения web-режима

- Работает только для публично доступных страниц.
- Разметка `m.vk.com` может меняться, поэтому извлечение текста менее стабильно, чем API.
- Для `owner_id` (число) используйте API-режим; web-режим ожидает `domain`/URL паблика.

## Если получили пустой результат `[]`

1. Убедитесь, что паблик действительно публичный и доступен без авторизации.
2. Попробуйте передать только short-name паблика (например, `rsportru` вместо полного URL).
3. Переключитесь на API-режим (самый стабильный):

```powershell
$env:VK_TOKEN = "ваш_токен"
python vk_news_parser.py --source-mode api --domains rsportru --count 20 --pretty
```

Теперь в web-режиме при проблеме парсинга скрипт вернет не пустой массив, а объект с `error`, чтобы было понятно, что именно пошло не так.
