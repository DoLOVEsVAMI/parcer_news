# VK news parser (explicit ads filter)

Скрипт парсит посты из новостных пабликов ВКонтакте и классифицирует каждый пост по вашему JSON-пресету:
- `explicit_ad` — есть сильные маркеры рекламы (или набран порог score),
- `not_explicit_ad` — явной рекламы нет.

После каждого запуска скрипт также создает HTML-отчет (по умолчанию `vk_posts_report.html`) c адаптивной версткой для мобильных устройств.

## Нужен ли API токен?

Не обязательно.

Поддерживаются режимы:
- `--source-mode api` — **официальный VK API** (`utils.resolveScreenName` + `wall.get`), **нужен `VK_TOKEN`**.
- `--source-mode web` — парсинг **публичных страниц** через `m.vk.com`, **токен не нужен**.
- `--source-mode auto` (по умолчанию) — если есть токен, используется API; если токена нет, используется web-режим.

## Быстрый запуск (без API токена)


> Важно: без API парсинг работает в режиме "best effort" (зависит от текущей разметки/ограничений VK).
> Если web-режим не видит посты, это не ваша ошибка — используйте API-режим для гарантированного результата.


```bash
python vk_news_parser.py \
  --source-mode web \
  --domains lenta_ru meduzalive \
  --count 20 \
  --rules-json rules.strict_explicit_ads_min_fp.json \
  --pretty \
  --html-output vk_posts_report.html \
  --json-output json/posts.json
```

## Быстрый запуск (через API)

```bash
export VK_TOKEN='ваш_токен'
python vk_news_parser.py \
  --source-mode api \
  --domains lenta_ru meduzalive \
  --count 20 \
  --rules-json rules.strict_explicit_ads_min_fp.json \
  --pretty \
  --html-output vk_posts_report.html \
  --json-output json/posts.json
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
- `--html-output` — путь к HTML-отчету (по умолчанию `vk_posts_report.html`)
- `--json-output` — путь к JSON-файлу с накоплением постов (по умолчанию `json/posts.json`)

## HTML-отчет

- В отчете сохраняются переносы строк поста (`\n`) и форматирование текста выглядит близко к оригиналу поста.
- Есть адаптация под мобильные экраны (responsive).
- В HTML попадают как посты, так и диагностические `error`/`warning` записи.
- Для постов, полученных через API, в HTML отображаются фотографии из вложений поста.
- Фото вставляются в HTML как обычные изображения (`<img>`), а при доступности сети дополнительно встраиваются в сам HTML (data URI), чтобы страница была самодостаточной.
- Служебные строки `Источник/Пост/Дата/Оценка` убраны из карточки поста — оставлен текст поста, фотографии и ссылка «Открыть пост во VK».

## Накопление JSON без дублей

- После каждого запуска сохраняется файл `json/posts.json` (или путь из `--json-output`).
- При следующем запуске скрипт сначала читает этот файл и добавляет только новые посты.
- Уже сохраненные посты повторно не дублируются.

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

## Если web-режим не отдает посты (`[]` или `error`)

1. Убедитесь, что паблик действительно публичный и доступен без авторизации.
2. Попробуйте передать только short-name паблика (например, `rsportru` вместо полного URL).
3. Переключитесь на API-режим (самый стабильный):

```powershell
$env:VK_TOKEN = "ваш_токен"
python vk_news_parser.py --source-mode api --domains rsportru --count 20 --pretty
```

Теперь в web-режиме при проблеме парсинга скрипт вернет не пустой массив, а объект с `error`, чтобы было понятно, что именно пошло не так.
