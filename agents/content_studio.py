"""HoeshaHome Content Studio agent.

Generates on-brand marketing copy (Instagram caption, TikTok hook, product page
description) for a product using Groq, saves the result to disk, and posts the
short-form copy to the Slack #content-review channel.

Usage:
    python agents/content_studio.py \
        --product "Sunset Lounge Chair" \
        --url "https://hoeshahome.com/products/sunset-lounge-chair" \
        --image-url "https://cdn.hoeshahome.com/images/sunset-lounge-chair.jpg"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("content_studio")


MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = (
    "You are a copywriter for HoeshaHome, a trendy lifestyle brand. "
    "Tone: fun, confident, aspirational. "
    "Target: US Gen Z (18-26) and Indonesian millennials (22-35). "
    "Never sound corporate."
)

USER_PROMPT_TEMPLATE = """Write marketing copy for this HoeshaHome product.

Product name: {product_name}
Product URL: {product_url}
Product image URL: {image_url}

Use the attached product image as visual reference for tone, color, and styling cues.

Return STRICTLY a single JSON object (no markdown fences, no extra prose) with exactly these keys:

{{
  "instagram_caption": "<Instagram caption: ~150 words, fun and aspirational, end with a clear CTA, include exactly 5 relevant hashtags at the end>",
  "tiktok_hook": "<TikTok opening hook: exactly 15 words or fewer, curiosity-driven, scroll-stopping, no hashtags>",
  "product_page_description": "<Product page description: ~200 words, SEO-friendly, naturally weave in 3-5 likely search keywords for this product, structured for shoppers>"
}}

Rules:
- Output MUST be valid JSON parseable by json.loads.
- Do NOT wrap the JSON in markdown code fences.
- Do NOT include any text before or after the JSON object.
- Speak to US Gen Z and Indonesian millennials. Avoid corporate language."""


class ContentStudioError(Exception):
    """Raised when the content studio pipeline fails."""


def _load_env() -> str:
    """Return GROQ_API_KEY from the environment, raising if missing.

    Loads variables from a local ``.env`` file when ``python-dotenv`` is
    available, but always reads the final value via ``os.environ.get`` so
    GitHub Actions secrets (which are exposed as real environment variables)
    take precedence.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ContentStudioError(
            "GROQ_API_KEY is not set. Set it as an environment variable "
            "(GitHub Actions secret) or add it to .env."
        )
    return api_key


def startup_check() -> None:
    """Print which env variables are loaded vs missing for this agent.

    Reads the environment via ``os.environ.get`` so that values supplied by
    GitHub Actions (which sets real env vars) are visible even when no
    ``.env`` file exists.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass

    required = ["GROQ_API_KEY"]
    optional = [
        "GROQ_MODEL",
        "SLACK_WEBHOOK_URL",
        "SLACK_BOT_TOKEN",
        "SLACK_CONTENT_REVIEW_CHANNEL",
    ]

    loaded = [name for name in required + optional if os.environ.get(name)]
    missing_required = [name for name in required if not os.environ.get(name)]
    missing_optional = [name for name in optional if not os.environ.get(name)]

    print("=" * 60, file=sys.stderr)
    print("[content_studio] startup env check", file=sys.stderr)
    print(f"  loaded:           {', '.join(loaded) or '(none)'}", file=sys.stderr)
    print(
        f"  missing required: {', '.join(missing_required) or '(none)'}",
        file=sys.stderr,
    )
    print(
        f"  missing optional: {', '.join(missing_optional) or '(none)'}",
        file=sys.stderr,
    )
    print("=" * 60, file=sys.stderr)


def generate_content(
    api_key: str,
    product_name: str,
    product_url: str,
    image_url: str,
) -> dict[str, str]:
    """Call Groq and return the parsed content dict."""
    try:
        import groq
    except ImportError as exc:
        raise ContentStudioError(
            "groq SDK is not installed. Run: pip install groq"
        ) from exc

    client = groq.Groq(api_key=api_key)

    user_text = USER_PROMPT_TEMPLATE.format(
        product_name=product_name,
        product_url=product_url,
        image_url=image_url,
    )

    try:
        response = client.chat.completions.create(
            model=MODEL,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
        )
        raw_text = (response.choices[0].message.content or "").strip()
    except groq.APIError as exc:
        raise ContentStudioError(f"Groq API error: {exc}") from exc
    except Exception as exc:
        raise ContentStudioError(f"Unexpected error calling Groq: {exc}") from exc

    return _parse_content_json(raw_text)


def _parse_content_json(raw_text: str) -> dict[str, str]:
    """Extract and validate the JSON payload returned by the model."""
    if not raw_text:
        raise ContentStudioError("Model returned an empty response.")

    candidate = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1)
    else:
        first = candidate.find("{")
        last = candidate.rfind("}")
        if first != -1 and last != -1 and last > first:
            candidate = candidate[first : last + 1]

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ContentStudioError(
            f"Model did not return valid JSON. Raw output:\n{raw_text}"
        ) from exc

    required = {"instagram_caption", "tiktok_hook", "product_page_description"}
    missing = required - data.keys()
    if missing:
        raise ContentStudioError(
            f"Model response is missing required keys: {sorted(missing)}"
        )

    for key in required:
        value = data[key]
        if not isinstance(value, str) or not value.strip():
            raise ContentStudioError(f"Model returned empty value for '{key}'.")
        data[key] = value.strip()

    return {key: data[key] for key in required}


def _slugify(value: str) -> str:
    """Turn an arbitrary product name into a safe filename slug."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return slug or "product"


def save_content(
    output_dir: Path,
    product_name: str,
    product_url: str,
    image_url: str,
    content: dict[str, str],
) -> Path:
    """Persist the generated content to data/content_{product_name}.json."""
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ContentStudioError(
            f"Could not create output directory {output_dir}: {exc}"
        ) from exc

    payload = {
        "product_name": product_name,
        "product_url": product_url,
        "image_url": image_url,
        "model": MODEL,
        "content": content,
    }

    filename = f"content_{_slugify(product_name)}.json"
    output_path = output_dir / filename
    try:
        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    except OSError as exc:
        raise ContentStudioError(
            f"Could not write content file {output_path}: {exc}"
        ) from exc

    logger.info("Saved content to %s", output_path)
    return output_path


def post_to_slack(product_name: str, content: dict[str, str]) -> bool:
    """Post the IG caption and TikTok hook to Slack via SLACK_WEBHOOK_URL.

    Returns True on success, False on (logged) failure. Slack failures do not
    abort the pipeline because the content has already been generated and saved.
    """
    text = (
        f"*New content for review:* {product_name}\n\n"
        f"*Instagram caption*\n{content['instagram_caption']}\n\n"
        f"*TikTok hook*\n{content['tiktok_hook']}"
    )

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("Skipping Slack post: SLACK_WEBHOOK_URL is not set.")
        return False

    try:
        import requests
    except ImportError:
        logger.error("requests is not installed. Run: pip install requests")
        return False

    try:
        response = requests.post(
            webhook_url,
            json={"text": text},
            timeout=15,
        )
        response.raise_for_status()
        logger.info("Posted to Slack via webhook.")
        return True
    except requests.RequestException as exc:
        logger.error("Failed to post to Slack: %s", exc)
        return False
    except Exception as exc:
        logger.error("Unexpected error posting to Slack: %s", exc)
        return False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate HoeshaHome marketing copy with Groq."
    )
    parser.add_argument("--product", required=True, help="Product name.")
    parser.add_argument("--url", required=True, help="Product page URL.")
    parser.add_argument(
        "--image-url",
        required=True,
        help="Public URL of the product image.",
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where content_{product}.json is written (default: data).",
    )
    parser.add_argument(
        "--no-slack",
        action="store_true",
        help="Skip posting to Slack.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def run(
    product_name: str,
    product_url: str,
    image_url: str,
    output_dir: Path,
    post_slack: bool,
) -> dict[str, Any]:
    """End-to-end pipeline. Returns a result dict."""
    if not product_name.strip():
        raise ContentStudioError("Product name must not be empty.")
    if not product_url.strip():
        raise ContentStudioError("Product URL must not be empty.")
    if not image_url.strip():
        raise ContentStudioError("Image URL must not be empty.")

    api_key = _load_env()
    content = generate_content(api_key, product_name, product_url, image_url)
    output_path = save_content(
        output_dir, product_name, product_url, image_url, content
    )

    slack_posted = False
    if post_slack:
        slack_posted = post_to_slack(product_name, content)

    return {
        "content": content,
        "output_path": str(output_path),
        "slack_posted": slack_posted,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    startup_check()

    try:
        result = run(
            product_name=args.product,
            product_url=args.url,
            image_url=args.image_url,
            output_dir=Path(args.output_dir),
            post_slack=not args.no_slack,
        )
    except ContentStudioError as exc:
        logger.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logger.error("Interrupted.")
        return 130
    except Exception as exc:
        logger.exception("Unexpected error: %s", exc)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
