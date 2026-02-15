#!/usr/bin/env python3
"""VK news public parser with explicit-ad filtering rules.

Supports two data sources:
- API mode: official VK API (`utils.resolveScreenName` + `wall.get`) — requires token.
- Web mode: parse public pages from m.vk.com — token is not required.
"""

from __future__ import annotations

import argparse
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
        ]

        candidates: List[str] = []
        for feed_url in feed_urls:
            try:
                feed_html = self._http_get(feed_url)
            except Exception:
                continue
            candidates.extend(re.findall(r'href="(/wall-?\d+_\d+[^"]*)"', feed_html))
            candidates.extend(re.findall(r"(?:\\/|/)wall-?\d+_\d+", feed_html))

        seen: Set[str] = set()
        links: List[str] = []
        for link in candidates:
            clean = html.unescape(link.replace("\\/", "/").split("?")[0])
            wall_match = re.search(r"/wall(-?\d+_\d+)", clean)
            if not wall_match:
                continue
            post_key = wall_match.group(1)
            if post_key in seen:
                continue
            seen.add(post_key)
            links.append(f"{self.MOBILE_URL}/wall{post_key}")
            if len(links) >= count:
                break

        if not links:
            raise RuntimeError(
                "Не удалось найти посты на веб-странице паблика. Попробуйте --source-mode api с VK_TOKEN"
            )

        posts: List[Dict[str, Any]] = []
        for link in links:
            try:
                page = self._http_get(link)
            except Exception:
                continue
            post_key = re.search(r"wall(-?\d+_\d+)", link).group(1)  # type: ignore[union-attr]
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
                "Посты найдены, но текст не удалось извлечь. Попробуйте --source-mode api с VK_TOKEN"
            )

        return posts


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Парсер постов VK с детектором явной рекламы")
    parser.add_argument("--token", default=os.getenv("VK_TOKEN"), help="VK API token (для --source-mode api)")
    parser.add_argument("--domains", nargs="+", required=True, help="Список пабликов: domain/url/owner_id")
    parser.add_argument("--count", type=int, default=20, help="Количество постов на паблик")
    parser.add_argument("--source-mode", choices=["auto", "api", "web"], default="auto", help="Источник постов")
    parser.add_argument("--rules-json", help="Путь к JSON с правилами. По умолчанию встроенный preset")
    parser.add_argument("--only-not-blocked", action="store_true", help="Выводить только not_explicit_ad")
    parser.add_argument("--pretty", action="store_true", help="Форматировать JSON вывод с отступами")
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
            text = post.get("text", "") or ""
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

    print(json.dumps(results, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
