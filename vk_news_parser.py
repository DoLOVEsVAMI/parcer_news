#!/usr/bin/env python3
"""VK news public parser with explicit-ad filtering rules.

Supports two data sources:
- API mode: official VK API (`utils.resolveScreenName` + `wall.get`) — requires token.
- Web mode: parse public pages from m.vk.com — token is not required.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import html
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


DEFAULT_RULESET: Dict[str, Any] = {
    "version": "1.0",
    "preset": "strict_explicit_ads_min_fp",
    "scope": ["telegram", "vk"],
    "decision": {
        "mode": "rule_based_then_score",
        "block_if_any_strong_rule_matches": True,
        "score_threshold": 10,
    },
    "rules": {
        "strong_blocklist": [
            {
                "id": "label_reklama",
                "type": "regex",
                "weight": 10,
                "pattern": r"(?i)(^|\s|[\[({«])реклама($|\s|[\])}».,!?:;])",
                "reason": "Явная пометка «Реклама»",
            },
            {
                "id": "has_erid",
                "type": "regex",
                "weight": 10,
                "pattern": r"(?i)\berid\s*[:=]\s*[a-z0-9_-]{6,}|[?&]erid=[a-z0-9_-]{6,}|\bЕРИД\b|\bЕRID\b",
                "reason": "ERID-токен в тексте/ссылке",
            },
            {
                "id": "ad_hashtags",
                "type": "regex",
                "weight": 9,
                "pattern": r"(?i)#\s*(реклама|ad|ads|sponsored|спонсор|партнерство|партнёрство|промо)\b",
                "reason": "Явные рекламные хештеги",
            },
            {
                "id": "explicit_sponsored_phrase",
                "type": "regex",
                "weight": 8,
                "pattern": r"(?i)\b(на правах рекламы|спонсор(ированный|ское)?|пост подготовлен при поддержке|партн(е|ё)рский материал|в сотрудничестве с)\b",
                "reason": "Явные формулировки спонсорства/партнерства",
            },
            {
                "id": "explicit_advertiser_field",
                "type": "regex",
                "weight": 8,
                "pattern": r"(?i)\b(рекламодатель|заказчик)\s*[:—-]",
                "reason": "Явное поле 'рекламодатель/заказчик'",
            },
        ],
        "optional_support_signals": [
            {
                "id": "utm_or_affiliate",
                "type": "regex",
                "weight": 2,
                "pattern": r"(?i)[?&]utm_(source|medium|campaign|content|term)=[^\s]+|[?&](ref|aff|affiliate|partner|promo)=([^\s]+)",
            },
            {
                "id": "promo_discount_price",
                "type": "regex",
                "weight": 1,
                "pattern": r"(?i)\b(промокод|скидк|акци|распродаж|кэшбэк|cashback|\d{2,}\s*(₽|руб\.?|р\.?|\$|€))\b",
            },
            {
                "id": "cta_soft",
                "type": "regex",
                "weight": 1,
                "pattern": r"(?i)\b(переходи(те)? по ссылке|жми(те)?|оформ(и|ля)ть|закаж(и|ите)|остав(ь|ьте) заявку|пиши(те)? в лс|в директ)\b",
            },
        ],
    },
    "output": {
        "label_if_blocked": "explicit_ad",
        "label_if_not_blocked": "not_explicit_ad",
        "explain": True,
        "explain_fields": ["matched_rule_ids", "matched_fragments", "score"],
    },
}


@dataclass
class Rule:
    rule_id: str
    weight: int
    pattern: re.Pattern[str]
    strong: bool


class ExplicitAdClassifier:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.score_threshold = int(config["decision"].get("score_threshold", 10))
        self.block_if_any_strong = bool(config["decision"].get("block_if_any_strong_rule_matches", True))
        self.strong_rules = self._compile_rules(config["rules"]["strong_blocklist"], strong=True)
        self.support_rules = self._compile_rules(config["rules"].get("optional_support_signals", []), strong=False)

        output_cfg = config.get("output", {})
        self.blocked_label = output_cfg.get("label_if_blocked", "explicit_ad")
        self.not_blocked_label = output_cfg.get("label_if_not_blocked", "not_explicit_ad")

    @staticmethod
    def _compile_rules(raw_rules: Iterable[Dict[str, Any]], strong: bool) -> List[Rule]:
        compiled: List[Rule] = []
        for rr in raw_rules:
            if rr.get("type") != "regex":
                continue
            compiled.append(
                Rule(
                    rule_id=rr["id"],
                    weight=int(rr.get("weight", 0)),
                    pattern=re.compile(rr["pattern"]),
                    strong=strong,
                )
            )
        return compiled

    def classify(self, text: str) -> Dict[str, Any]:
        matched_rule_ids: List[str] = []
        matched_fragments: List[str] = []
        score = 0
        strong_hit = False

        for rule in [*self.strong_rules, *self.support_rules]:
            matches = list(rule.pattern.finditer(text))
            if not matches:
                continue
            matched_rule_ids.append(rule.rule_id)
            score += rule.weight
            if rule.strong:
                strong_hit = True
            for m in matches[:3]:
                fragment = m.group(0).strip().replace("\n", " ")
                if fragment:
                    matched_fragments.append(fragment[:160])

        blocked = (self.block_if_any_strong and strong_hit) or (score >= self.score_threshold)
        return {
            "label": self.blocked_label if blocked else self.not_blocked_label,
            "blocked": blocked,
            "matched_rule_ids": matched_rule_ids,
            "matched_fragments": matched_fragments,
            "score": score,
        }


class VKClient:
    API_URL = "https://api.vk.com/method"
    MOBILE_URL = "https://m.vk.com"

    def __init__(self, token: Optional[str], version: str = "5.199", timeout: int = 20) -> None:
        self.token = token
        self.version = version
        self.timeout = timeout

    def _http_get(self, url: str) -> str:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 CodexVKParser/1.0"})
        with urlopen(req, timeout=self.timeout) as response:
            return response.read().decode("utf-8", errors="replace")

    def _call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self.token:
            raise RuntimeError("VK token обязателен для API режима")
        payload = {**params, "access_token": self.token, "v": self.version}
        url = f"{self.API_URL}/{method}?{urlencode(payload)}"
        data = json.loads(self._http_get(url))
        if "error" in data:
            raise RuntimeError(f"VK API error ({method}): {data['error']}")
        return data["response"]

    def resolve_owner_id(self, domain_or_owner: str) -> int:
        if domain_or_owner.startswith("-") and domain_or_owner[1:].isdigit():
            return int(domain_or_owner)
        if domain_or_owner.isdigit():
            return int(domain_or_owner)

        raw = normalize_domain(domain_or_owner)
        entity = self._call("utils.resolveScreenName", {"screen_name": raw})
        if not entity:
            raise RuntimeError(f"Не удалось определить owner_id для '{domain_or_owner}'")

        obj_type = entity.get("type")
        obj_id = int(entity["object_id"])
        if obj_type in {"group", "page", "event"}:
            return -obj_id
        return obj_id

    def fetch_wall_posts_api(self, owner_id: int, count: int = 20) -> List[Dict[str, Any]]:
        resp = self._call(
            "wall.get",
            {"owner_id": owner_id, "count": count, "filter": "owner", "extended": 0},
        )
        return resp.get("items", [])

    def fetch_wall_posts_web(self, domain_or_url: str, count: int = 20) -> List[Dict[str, Any]]:
        domain = normalize_domain(domain_or_url)
        if not domain or domain.lstrip("-").isdigit():
            raise RuntimeError("Web режим поддерживает только domain/URL паблика, не owner_id")

        feed_urls = [
            f"{self.MOBILE_URL}/{domain}",
            f"https://vk.com/{domain}",
            f"https://vk.com/{domain}?w=wall",
        ]

        post_keys: List[str] = []
        for feed_url in feed_urls:
            try:
                feed_html = self._http_get(feed_url)
            except Exception:
                continue
            post_keys.extend(extract_post_keys(feed_html))

        seen: Set[str] = set()
        ordered_keys: List[str] = []
        for key in post_keys:
            if key in seen:
                continue
            seen.add(key)
            ordered_keys.append(key)
            if len(ordered_keys) >= count:
                break

        if not ordered_keys:
            raise RuntimeError(
                "Не удалось найти post id на веб-странице паблика. Для стабильной выдачи используйте --source-mode api с VK_TOKEN"
            )

        posts: List[Dict[str, Any]] = []
        for post_key in ordered_keys:
            pages_to_try = [
                f"{self.MOBILE_URL}/wall{post_key}",
                f"https://vk.com/wall{post_key}",
            ]
            page = ""
            for page_url in pages_to_try:
                try:
                    page = self._http_get(page_url)
                    if page:
                        break
                except Exception:
                    continue
            if not page:
                continue

            owner_id_str, post_id_str = post_key.split("_")
            text = extract_post_text(page)
            date_value: Optional[str] = None
            date_match = re.search(r'itemprop="datePublished"\s+content="([^"]+)"', page)
            if date_match:
                date_value = date_match.group(1)

            posts.append(
                {
                    "id": int(post_id_str),
                    "date": date_value,
                    "text": text,
                    "owner_id": int(owner_id_str),
                    "post_url": f"https://vk.com/wall{post_key}",
                }
            )

        if not posts:
            raise RuntimeError(
                "Post id найдены, но страницы постов недоступны. Для стабильной выдачи используйте --source-mode api с VK_TOKEN"
            )

        return posts



def extract_post_keys(feed_html: str) -> List[str]:
    patterns = [
        r'href="(?:\/|/)?wall(-?\d+_\d+)(?:[^"\s]*)"',
        r'(?:^|[^a-z_])wall(-?\d+_\d+)(?:$|[^\d])',
        r'post-(-?\d+_\d+)',
    ]

    keys: List[str] = []
    for pattern in patterns:
        keys.extend(re.findall(pattern, feed_html))

    normalized: List[str] = []
    seen: Set[str] = set()
    for key in keys:
        clean = html.unescape(key).replace('\/', '/')
        clean = clean.strip('/').replace('wall', '')
        if not re.fullmatch(r'-?\d+_\d+', clean):
            continue
        if clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean)
    return normalized

def normalize_domain(value: str) -> str:
    raw = value.strip().rstrip("/")
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        raw = parsed.path.strip("/")
    return raw


def strip_tags(raw_html: str) -> str:
    cleaned = re.sub(r"<br\s*/?>", "\n", raw_html, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def extract_post_text(page_html: str) -> str:
    meta_match = re.search(r'<meta\s+property="og:description"\s+content="([^"]*)"', page_html)
    if meta_match:
        return html.unescape(meta_match.group(1)).strip()

    text_match = re.search(r'<div[^>]*class="[^"]*pi_text[^"]*"[^>]*>(.*?)</div>', page_html, flags=re.DOTALL)
    if text_match:
        return strip_tags(text_match.group(1))

    return ""


def normalize_post_text(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\\n", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    return normalized.strip()


def render_html_report(results: List[Dict[str, Any]], output_path: str) -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cards: List[str] = []

    for item in results:
        if "error" in item:
            cards.append(
                (
                    '<article class="card card-error">'
                    f'<div class="meta"><b>Источник:</b> {html.escape(str(item.get("source", "-")))}</div>'
                    f'<div class="meta"><b>Режим:</b> {html.escape(str(item.get("source_mode", "-")))}</div>'
                    f'<div class="error">Ошибка: {html.escape(str(item.get("error", "-")))}</div>'
                    '</article>'
                )
            )
            continue

        if "warning" in item:
            cards.append(
                (
                    '<article class="card card-warning">'
                    f'<div class="meta"><b>Источник:</b> {html.escape(str(item.get("source", "-")))}</div>'
                    f'<div class="warning">{html.escape(str(item.get("warning", "")))}</div>'
                    '</article>'
                )
            )
            continue

        post_text = html.escape(normalize_post_text(str(item.get("text", ""))))
        label = html.escape(str(item.get("label", "-")))
        label_cls = "label-blocked" if item.get("blocked") else "label-ok"
        post_url = html.escape(str(item.get("post_url", "#")))

        cards.append(
            (
                '<article class="card">'
                f'<div class="meta"><b>Источник:</b> {html.escape(str(item.get("source", "-")))}</div>'
                f'<div class="meta"><b>Пост:</b> <a href="{post_url}" target="_blank" rel="noopener">{post_url}</a></div>'
                f'<div class="meta"><b>Дата:</b> {html.escape(str(item.get("date", "-")))}</div>'
                f'<div class="meta"><b>Оценка:</b> <span class="label {label_cls}">{label}</span> (score={html.escape(str(item.get("score", 0)))})</div>'
                f'<div class="post-text">{post_text}</div>'
                '</article>'
            )
        )

    html_doc = f"""<!doctype html>
<html lang=\"ru\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>VK parser report</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f7fb; color: #1d2433; }}
    .container {{ max-width: 980px; margin: 0 auto; padding: 20px 16px 40px; }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    .sub {{ margin-bottom: 16px; color: #55627a; font-size: 14px; }}
    .grid {{ display: grid; gap: 12px; }}
    .card {{ background: #fff; border: 1px solid #e5eaf2; border-radius: 12px; padding: 14px; box-shadow: 0 1px 2px rgba(0,0,0,.04); }}
    .meta {{ margin-bottom: 6px; font-size: 14px; line-height: 1.35; word-break: break-word; }}
    .post-text {{ margin-top: 10px; padding-top: 10px; border-top: 1px solid #eef1f6; white-space: pre-wrap; line-height: 1.45; font-size: 15px; }}
    .label {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; }}
    .label-ok {{ background: #e8f8ef; color: #1e7a46; }}
    .label-blocked {{ background: #ffe9e9; color: #a73737; }}
    .card-error {{ border-color: #f3b1b1; background: #fff7f7; }}
    .card-warning {{ border-color: #f3d8a8; background: #fffaf1; }}
    .error {{ color: #a73737; font-weight: 600; }}
    .warning {{ color: #8a5b00; font-weight: 600; }}
    @media (max-width: 640px) {{
      .container {{ padding: 12px 10px 24px; }}
      h1 {{ font-size: 20px; }}
      .meta {{ font-size: 13px; }}
      .post-text {{ font-size: 14px; }}
    }}
  </style>
</head>
<body>
  <main class=\"container\">
    <h1>VK parser report</h1>
    <div class=\"sub\">Сгенерировано: {html.escape(generated_at)} • Записей: {len(results)}</div>
    <section class=\"grid\">{''.join(cards) if cards else '<article class="card">Нет данных для отображения.</article>'}</section>
  </main>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_doc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Парсер постов VK с детектором явной рекламы")
    parser.add_argument("--token", default=os.getenv("VK_TOKEN"), help="VK API token (для --source-mode api)")
    parser.add_argument("--domains", nargs="+", required=True, help="Список пабликов: domain/url/owner_id")
    parser.add_argument("--count", type=int, default=20, help="Количество постов на паблик")
    parser.add_argument("--source-mode", choices=["auto", "api", "web"], default="auto", help="Источник постов")
    parser.add_argument("--rules-json", help="Путь к JSON с правилами. По умолчанию встроенный preset")
    parser.add_argument("--only-not-blocked", action="store_true", help="Выводить только not_explicit_ad")
    parser.add_argument("--pretty", action="store_true", help="Форматировать JSON вывод с отступами")
    parser.add_argument("--html-output", default="vk_posts_report.html", help="Путь для HTML-отчета")
    return parser


def load_rules(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return DEFAULT_RULESET
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    args = build_parser().parse_args()
    mode = args.source_mode
    if mode == "auto":
        mode = "api" if args.token else "web"

    rules = load_rules(args.rules_json)
    classifier = ExplicitAdClassifier(rules)
    client = VKClient(args.token)
    results: List[Dict[str, Any]] = []

    for source in args.domains:
        try:
            if mode == "api":
                owner_id = client.resolve_owner_id(source)
                posts = client.fetch_wall_posts_api(owner_id=owner_id, count=args.count)
            else:
                posts = client.fetch_wall_posts_web(domain_or_url=source, count=args.count)
        except Exception as exc:
            results.append({"source": source, "error": str(exc), "source_mode": mode})
            continue

        if not posts:
            results.append(
                {
                    "source": source,
                    "source_mode": mode,
                    "warning": "Посты не найдены (пустой результат).",
                }
            )
            continue

        for post in posts:
            text = normalize_post_text(post.get("text", "") or "")
            verdict = classifier.classify(text)
            if args.only_not_blocked and verdict["blocked"]:
                continue

            owner_id = post.get("owner_id")
            if owner_id is None and mode == "api":
                owner_id = client.resolve_owner_id(source)

            results.append(
                {
                    "source": source,
                    "source_mode": mode,
                    "owner_id": owner_id,
                    "post_id": post.get("id"),
                    "date": post.get("date"),
                    "text": text,
                    **verdict,
                    "post_url": post.get("post_url") or f"https://vk.com/wall{owner_id}_{post.get('id')}",
                }
            )

    render_html_report(results, args.html_output)
    print(json.dumps(results, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
