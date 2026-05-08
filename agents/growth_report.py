"""Weekly CEO growth report agent.

Pulls last 7 days of Shopify data, reads CS ticket log, asks Groq to write a
brief CEO-style summary, posts it to Slack, and saves it to disk.

Environment variables required:
    SHOPIFY_STORE              e.g. "my-shop.myshopify.com"
    SHOPIFY_ACCESS_TOKEN       Shopify Admin API access token
    SHOPIFY_API_VERSION        optional, defaults to "2024-10"
    GROQ_API_KEY               Groq API key
    GROQ_MODEL                 optional, defaults to "llama-3.3-70b-versatile"
    SLACK_BOT_TOKEN            Slack bot token (xoxb-...)
    SLACK_CHANNEL              optional, defaults to "#reports"
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
CS_LOG_PATH = ROOT / "data" / "cs_log.json"
REPORTS_DIR = ROOT / "data" / "reports"

DEFAULT_SHOPIFY_API_VERSION = "2024-10"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_SLACK_CHANNEL = "#reports"


@dataclass
class ShopifyMetrics:
    orders: int
    revenue: float
    currency: str
    best_selling_product: str | None
    best_selling_units: int
    refund_count: int


@dataclass
class CsMetrics:
    auto_resolved: int
    escalated: int
    total: int


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _shopify_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    store = _require_env("SHOPIFY_STORE")
    token = _require_env("SHOPIFY_ACCESS_TOKEN")
    version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_SHOPIFY_API_VERSION)
    url = f"https://{store}/admin/api/{version}/{path}"
    headers = {
        "X-Shopify-Access-Token": token,
        "Accept": "application/json",
    }
    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_shopify_metrics(days: int = 7) -> ShopifyMetrics:
    """Fetch last `days` days of Shopify orders and refunds."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    since_iso = since.isoformat()

    orders: list[dict[str, Any]] = []
    params: dict[str, Any] = {
        "status": "any",
        "created_at_min": since_iso,
        "limit": 250,
        "fields": "id,created_at,total_price,currency,line_items,refunds,financial_status",
    }
    data = _shopify_get("orders.json", params=params)
    orders.extend(data.get("orders", []))

    revenue = 0.0
    currency = "USD"
    units_by_product: Counter[str] = Counter()
    refund_count = 0

    for order in orders:
        try:
            revenue += float(order.get("total_price") or 0)
        except (TypeError, ValueError):
            pass
        if order.get("currency"):
            currency = order["currency"]
        for item in order.get("line_items", []) or []:
            title = item.get("title") or item.get("name") or "Unknown"
            qty = int(item.get("quantity") or 0)
            units_by_product[title] += qty
        refunds = order.get("refunds") or []
        if refunds:
            refund_count += len(refunds)

    best_product, best_units = (None, 0)
    if units_by_product:
        best_product, best_units = units_by_product.most_common(1)[0]

    return ShopifyMetrics(
        orders=len(orders),
        revenue=round(revenue, 2),
        currency=currency,
        best_selling_product=best_product,
        best_selling_units=best_units,
        refund_count=refund_count,
    )


def read_cs_metrics(path: Path = CS_LOG_PATH) -> CsMetrics:
    """Read the customer support log and count auto-resolved vs escalated tickets."""
    if not path.exists():
        return CsMetrics(auto_resolved=0, escalated=0, total=0)

    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    if isinstance(raw, dict) and "tickets" in raw:
        tickets = raw["tickets"]
    elif isinstance(raw, list):
        tickets = raw
    else:
        tickets = []

    auto = 0
    esc = 0
    for ticket in tickets:
        status = str(ticket.get("status") or ticket.get("resolution") or "").lower()
        if ticket.get("escalated") is True or "escalat" in status:
            esc += 1
        elif (
            ticket.get("auto_resolved") is True
            or ticket.get("auto_reply") is True
            or status in {"auto_resolved", "auto-resolved", "resolved", "auto"}
        ):
            auto += 1
    return CsMetrics(auto_resolved=auto, escalated=esc, total=len(tickets))


def build_groq_prompt(shopify: ShopifyMetrics, cs: CsMetrics) -> str:
    payload = {
        "shopify_last_7_days": asdict(shopify),
        "customer_support_last_7_days": asdict(cs),
    }
    return (
        "Here is the last 7 days of business data:\n"
        f"{json.dumps(payload, indent=2)}\n\n"
        "Write a CEO weekly briefing: 3 bullet wins, 1 warning if anything looks "
        "bad, 1 action for next week. Under 200 words. Be direct."
    )


def call_groq(prompt: str) -> str:
    try:
        import groq
    except ImportError as exc:
        raise RuntimeError(
            "groq SDK is not installed. Run: pip install groq"
        ) from exc

    api_key = _require_env("GROQ_API_KEY")
    model = os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL)
    client = groq.Groq(api_key=api_key, base_url=GROQ_BASE_URL)
    response = client.chat.completions.create(
        model=model,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return (response.choices[0].message.content or "").strip()


def post_to_slack(header: str, body: str) -> None:
    token = _require_env("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL", DEFAULT_SLACK_CHANNEL)
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header, "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": body},
        },
    ]
    response = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={"channel": channel, "text": header, "blocks": blocks},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data.get('error')}")


def save_report(date_str: str, content: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"report_{date_str}.txt"
    out_path.write_text(content, encoding="utf-8")
    return out_path


def main() -> int:
    today = datetime.now(timezone.utc).date()
    date_str = today.isoformat()
    header = f"Weekly Report - {date_str}"

    shopify = fetch_shopify_metrics(days=7)
    cs = read_cs_metrics()

    prompt = build_groq_prompt(shopify, cs)
    report = call_groq(prompt)

    out_path = save_report(date_str, report)
    print(f"Saved report to {out_path}")

    post_to_slack(header, report)
    print("Posted to Slack")
    return 0


if __name__ == "__main__":
    sys.exit(main())
