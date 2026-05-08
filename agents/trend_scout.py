"""Trend Scout agent.

Scrapes TikTok hashtags via the Apify TikTok Scraper actor, ranks the
returned videos by a weighted engagement score, posts the top results to
a Slack webhook, and persists them to ``data/trending.json``.

Run as a standalone script:

    python agents/trend_scout.py

Required environment variables (loaded from a ``.env`` file via
``python-dotenv``):

* ``APIFY_TOKEN``        - Apify API token used to authenticate the client.
* ``SLACK_WEBHOOK_URL``  - Slack incoming webhook URL to post results to.

Optional environment variables:

* ``APIFY_TIKTOK_ACTOR`` - Apify actor id to run. Defaults to
  ``clockworks/tiktok-scraper``.
* ``TIKTOK_HASHTAGS``    - Comma-separated hashtags (without ``#``) to
  override the defaults.
* ``RESULTS_PER_HASHTAG`` - Max videos requested per hashtag. Defaults to
  ``50``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

import requests
from apify_client import ApifyClient
from dotenv import load_dotenv


DEFAULT_HASHTAGS: tuple[str, ...] = ("TikTokMadeMeBuyIt", "aestheticfinds")
DEFAULT_ACTOR_ID = "clockworks/tiktok-scraper"
DEFAULT_RESULTS_PER_HASHTAG = 50
TOP_N = 5

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_FILE = DATA_DIR / "trending.json"


def _coerce_int(value: Any) -> int:
    """Best-effort conversion of an Apify metric field to ``int``."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0


def _extract_metrics(item: dict[str, Any]) -> tuple[int, int, int]:
    """Return ``(views, comments, likes)`` for an Apify TikTok item."""
    views = _coerce_int(item.get("playCount") or item.get("views"))
    comments = _coerce_int(item.get("commentCount") or item.get("comments"))
    likes = _coerce_int(
        item.get("diggCount") or item.get("likes") or item.get("likeCount")
    )
    return views, comments, likes


def _extract_video_url(item: dict[str, Any]) -> str:
    """Return the canonical TikTok video URL for an Apify item."""
    url = item.get("webVideoUrl") or item.get("videoUrl") or item.get("url")
    if url:
        return str(url)

    author = item.get("authorMeta") or {}
    author_name = author.get("name") if isinstance(author, dict) else None
    video_id = item.get("id")
    if author_name and video_id:
        return f"https://www.tiktok.com/@{author_name}/video/{video_id}"
    return ""


def _extract_product_name(item: dict[str, Any]) -> str:
    """Best-effort product/video title for the Slack message."""
    text = item.get("text") or item.get("desc") or ""
    text = str(text).strip()
    if text:
        first_line = text.splitlines()[0].strip()
        if len(first_line) > 140:
            return first_line[:137].rstrip() + "..."
        return first_line

    author = item.get("authorMeta") or {}
    if isinstance(author, dict):
        for key in ("nickName", "name"):
            value = author.get(key)
            if value:
                return f"Video by @{value}"
    return "Untitled TikTok"


def score_item(item: dict[str, Any]) -> float:
    """Weighted engagement score: views*0.4 + comments*0.4 + likes*0.2."""
    views, comments, likes = _extract_metrics(item)
    return (views * 0.4) + (comments * 0.4) + (likes * 0.2)


def scrape_hashtags(
    client: ApifyClient,
    hashtags: Iterable[str],
    *,
    actor_id: str = DEFAULT_ACTOR_ID,
    results_per_hashtag: int = DEFAULT_RESULTS_PER_HASHTAG,
) -> list[dict[str, Any]]:
    """Run the Apify TikTok actor for the given hashtags and collect items."""
    hashtags = [h.lstrip("#") for h in hashtags if h]
    if not hashtags:
        return []

    run_input: dict[str, Any] = {
        "hashtags": hashtags,
        "resultsPerPage": results_per_hashtag,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadSubtitles": False,
        "shouldDownloadSlideshowImages": False,
    }

    print(
        f"Running Apify actor '{actor_id}' for hashtags: "
        f"{', '.join('#' + h for h in hashtags)}",
        file=sys.stderr,
    )
    run = client.actor(actor_id).call(run_input=run_input)
    if not run or "defaultDatasetId" not in run:
        raise RuntimeError("Apify actor run did not return a dataset id")

    dataset = client.dataset(run["defaultDatasetId"])
    items: list[dict[str, Any]] = list(dataset.iterate_items())
    print(f"Fetched {len(items)} items from Apify dataset", file=sys.stderr)
    return items


def rank_top(items: Iterable[dict[str, Any]], top_n: int = TOP_N) -> list[dict[str, Any]]:
    """Score every item, sort descending, and return the top N as dicts."""
    ranked: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        views, comments, likes = _extract_metrics(item)
        score = (views * 0.4) + (comments * 0.4) + (likes * 0.2)
        ranked.append(
            {
                "product_name": _extract_product_name(item),
                "video_url": _extract_video_url(item),
                "score": score,
                "views": views,
                "comments": comments,
                "likes": likes,
                "hashtags": [
                    h.get("name")
                    for h in (item.get("hashtags") or [])
                    if isinstance(h, dict) and h.get("name")
                ],
                "author": (
                    (item.get("authorMeta") or {}).get("name")
                    if isinstance(item.get("authorMeta"), dict)
                    else None
                ),
            }
        )

    ranked.sort(key=lambda r: r["score"], reverse=True)
    return ranked[:top_n]


def post_to_slack(webhook_url: str, top_items: list[dict[str, Any]]) -> None:
    """Send a formatted summary of the top items to a Slack webhook."""
    if not top_items:
        payload = {"text": "Trend Scout: no trending TikTok items found."}
        response = requests.post(webhook_url, json=payload, timeout=15)
        response.raise_for_status()
        return

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Top {len(top_items)} trending TikTok finds",
            },
        }
    ]

    for rank, item in enumerate(top_items, start=1):
        url = item.get("video_url") or ""
        name = item.get("product_name") or "Untitled TikTok"
        title = f"*{rank}. <{url}|{name}>*" if url else f"*{rank}. {name}*"
        body = (
            f"{title}\n"
            f"Score: `{item['score']:,.0f}`  "
            f"Views: `{item['views']:,}`  "
            f"Comments: `{item['comments']:,}`  "
            f"Likes: `{item['likes']:,}`"
        )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": body}})

    fallback_text = "Top trending TikTok finds:\n" + "\n".join(
        f"{i}. {it['product_name']} - score {it['score']:,.0f} "
        f"({it['views']:,} views) {it['video_url']}".rstrip()
        for i, it in enumerate(top_items, start=1)
    )

    payload = {"text": fallback_text, "blocks": blocks}
    response = requests.post(webhook_url, json=payload, timeout=15)
    response.raise_for_status()


def save_results(top_items: list[dict[str, Any]], output_path: Path = OUTPUT_FILE) -> Path:
    """Persist the top items to ``data/trending.json`` (pretty-printed)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fp:
        json.dump(top_items, fp, indent=2, ensure_ascii=False)
    return output_path


def _parse_hashtags_env(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_HASHTAGS
    parsed = tuple(h.strip().lstrip("#") for h in raw.split(",") if h.strip())
    return parsed or DEFAULT_HASHTAGS


def main() -> int:
    load_dotenv()

    apify_token = os.getenv("APIFY_TOKEN")
    slack_webhook = os.getenv("SLACK_WEBHOOK_URL")
    if not apify_token:
        print("ERROR: APIFY_TOKEN is not set in the environment.", file=sys.stderr)
        return 1
    if not slack_webhook:
        print("ERROR: SLACK_WEBHOOK_URL is not set in the environment.", file=sys.stderr)
        return 1

    actor_id = os.getenv("APIFY_TIKTOK_ACTOR", DEFAULT_ACTOR_ID)
    hashtags = _parse_hashtags_env(os.getenv("TIKTOK_HASHTAGS"))
    try:
        results_per_hashtag = int(
            os.getenv("RESULTS_PER_HASHTAG", str(DEFAULT_RESULTS_PER_HASHTAG))
        )
    except ValueError:
        results_per_hashtag = DEFAULT_RESULTS_PER_HASHTAG

    client = ApifyClient(apify_token)

    items = scrape_hashtags(
        client,
        hashtags,
        actor_id=actor_id,
        results_per_hashtag=results_per_hashtag,
    )
    top_items = rank_top(items, top_n=TOP_N)

    output_path = save_results(top_items)
    print(f"Saved {len(top_items)} trending items to {output_path}", file=sys.stderr)

    post_to_slack(slack_webhook, top_items)
    print("Posted top trending items to Slack.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
