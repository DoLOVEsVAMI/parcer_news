# VK news parser (explicit ads filter)

Скрипт парсит посты из новостных пабликов ВКонтакте через VK API (`wall.get`) и классифицирует каждый пост по вашему JSON-пресету:
- `explicit_ad` — есть сильные маркеры рекламы (или набран порог score),
- `not_explicit_ad` — явной рекламы нет.

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
```

## Быстрый запуск

```bash
export VK_TOKEN='ваш_токен'
python vk_news_parser.py \
  --domains lenta_ru meduzalive \
  --count 20 \
  --rules-json rules.strict_explicit_ads_min_fp.json \
  --pretty
```

## Аргументы

- `--token` — VK API token (или переменная `VK_TOKEN`)
- `--domains` — список пабликов: `domain`, URL или `owner_id`
- `--count` — число постов на каждый паблик
- `--rules-json` — JSON с правилами (по умолчанию встроенный пресет)
- `--only-not-blocked` — вывести только `not_explicit_ad`
- `--pretty` — красивый JSON вывод

## Формат результата

```json
[
  {
    "source": "lenta_ru",
    "owner_id": -29684145,
    "post_id": 123456,
    "date": 1739876543,
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

## Важно

Для работы нужен валидный токен VK API с доступом к чтению стен сообществ.
