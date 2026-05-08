"""Store Ops Agent.

Given a product name, this agent:
  1. Reads data/content_{product_name}.json (output from the content studio).
  2. Creates a Shopify draft product listing via the REST Admin API.
  3. Tags the product with: trending, tiktok-viral, and the current month/year.
  4. Posts the draft Shopify admin URL to Slack #store-ops.
  5. Creates a Linear issue titled "Launch: {product_name}" with the Shopify URL.
  6. Saves the Shopify product ID to data/products.json.

Required environment variables:
  SHOPIFY_ACCESS_TOKEN     Shopify Admin API access token (X-Shopify-Access-Token)
  SHOPIFY_STORE_DOMAIN     The store domain, e.g. "my-shop.myshopify.com"
  SHOPIFY_API_VERSION      Optional, defaults to "2024-10"
  SLACK_WEBHOOK_URL        Slack incoming webhook URL for #store-ops notifications
  LINEAR_API_KEY           Linear personal API key
  LINEAR_TEAM_ID           Linear team ID (UUID) where the issue should be created

Usage:
  python -m agents.store_ops "Foldable Cat Tunnel"
  python agents/store_ops.py "Foldable Cat Tunnel"
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:
    pass

logger = logging.getLogger("store_ops")


REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
PRODUCTS_FILE = DATA_DIR / "products.json"

DEFAULT_SHOPIFY_API_VERSION = "2024-10"
HTTP_TIMEOUT = 30


def startup_check() -> None:
    """Print which env variables are loaded vs missing for this agent."""
    required = [
        "SHOPIFY_ACCESS_TOKEN",
        "SHOPIFY_STORE_DOMAIN",
        "SLACK_WEBHOOK_URL",
        "LINEAR_API_KEY",
        "LINEAR_TEAM_ID",
    ]
    optional = ["SHOPIFY_API_VERSION"]

    loaded = [name for name in required + optional if os.environ.get(name)]
    missing_required = [name for name in required if not os.environ.get(name)]
    missing_optional = [name for name in optional if not os.environ.get(name)]

    print("=" * 60, file=sys.stderr)
    print("[store_ops] startup env check", file=sys.stderr)
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


class StoreOpsError(Exception):
    """Base error for the store ops agent."""


class ConfigError(StoreOpsError):
    """Raised when required configuration is missing or invalid."""


class ContentNotFoundError(StoreOpsError):
    """Raised when the content studio file cannot be found or parsed."""


class ShopifyError(StoreOpsError):
    """Raised when Shopify API interactions fail."""


class SlackError(StoreOpsError):
    """Raised when Slack API interactions fail."""


class LinearError(StoreOpsError):
    """Raised when Linear API interactions fail."""


def _slugify(value: str) -> str:
    """Lower-case, hyphenated slug used for filenames."""
    if not isinstance(value, str):
        raise TypeError("value must be a string")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    if not slug:
        raise ValueError("product name produced an empty slug")
    return slug


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def load_content(product_name: str) -> dict[str, Any]:
    """Load data/content_{product_name}.json produced by content studio."""
    if not isinstance(product_name, str) or not product_name.strip():
        raise ValueError("product_name must be a non-empty string")

    slug = _slugify(product_name)
    candidates = [
        DATA_DIR / f"content_{slug}.json",
        DATA_DIR / f"content_{product_name}.json",
    ]
    for path in candidates:
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except json.JSONDecodeError as exc:
                raise ContentNotFoundError(
                    f"Content file {path} is not valid JSON: {exc}"
                ) from exc
            if not isinstance(data, dict):
                raise ContentNotFoundError(
                    f"Content file {path} must contain a JSON object at the top level"
                )
            logger.info("Loaded content studio output from %s", path)
            return data

    raise ContentNotFoundError(
        f"Could not find content file for product {product_name!r}. "
        f"Looked for: {', '.join(str(c) for c in candidates)}"
    )


def _build_product_payload(
    product_name: str, content: dict[str, Any]
) -> dict[str, Any]:
    """Convert content studio output into a Shopify product payload."""
    today = dt.date.today()
    month_year_tag = today.strftime("%B-%Y").lower()
    tags = ["trending", "tiktok-viral", month_year_tag]
    extra_tags = content.get("tags")
    if isinstance(extra_tags, list):
        for tag in extra_tags:
            if isinstance(tag, str) and tag and tag not in tags:
                tags.append(tag)

    title = content.get("title") or product_name
    body_html = (
        content.get("body_html")
        or content.get("description_html")
        or content.get("description")
        or ""
    )
    vendor = content.get("vendor")
    product_type = content.get("product_type") or content.get("category")

    images_payload: list[dict[str, Any]] = []
    for image in content.get("images", []) or []:
        if isinstance(image, str):
            images_payload.append({"src": image})
        elif isinstance(image, dict) and image.get("src"):
            images_payload.append({"src": image["src"], "alt": image.get("alt")})

    variants_payload: list[dict[str, Any]] = []
    for variant in content.get("variants", []) or []:
        if isinstance(variant, dict):
            variants_payload.append(variant)

    if not variants_payload:
        price = content.get("price")
        if price is not None:
            variants_payload.append({"price": str(price)})

    product: dict[str, Any] = {
        "title": title,
        "body_html": body_html,
        "status": "draft",
        "tags": ", ".join(tags),
    }
    if vendor:
        product["vendor"] = vendor
    if product_type:
        product["product_type"] = product_type
    if images_payload:
        product["images"] = images_payload
    if variants_payload:
        product["variants"] = variants_payload

    return {"product": product}


def create_shopify_draft(
    product_name: str, content: dict[str, Any]
) -> tuple[int, str]:
    """Create a draft product on Shopify. Returns (product_id, admin_url)."""
    access_token = _require_env("SHOPIFY_ACCESS_TOKEN")
    store_domain = _require_env("SHOPIFY_STORE_DOMAIN").strip().rstrip("/")
    if store_domain.startswith("http://") or store_domain.startswith("https://"):
        store_domain = re.sub(r"^https?://", "", store_domain)
    api_version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_SHOPIFY_API_VERSION)

    url = f"https://{store_domain}/admin/api/{api_version}/products.json"
    payload = _build_product_payload(product_name, content)
    headers = {
        "X-Shopify-Access-Token": access_token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    logger.info("Creating Shopify draft product at %s", url)
    try:
        response = requests.post(
            url, headers=headers, json=payload, timeout=HTTP_TIMEOUT
        )
    except requests.RequestException as exc:
        raise ShopifyError(f"Network error talking to Shopify: {exc}") from exc

    if response.status_code >= 400:
        raise ShopifyError(
            f"Shopify returned HTTP {response.status_code}: {response.text}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise ShopifyError(
            f"Shopify response was not valid JSON: {response.text}"
        ) from exc

    product = body.get("product") if isinstance(body, dict) else None
    if not isinstance(product, dict) or "id" not in product:
        raise ShopifyError(f"Unexpected Shopify response shape: {body!r}")

    product_id = product["id"]
    if not isinstance(product_id, int):
        try:
            product_id = int(product_id)
        except (TypeError, ValueError) as exc:
            raise ShopifyError(
                f"Shopify returned a non-numeric product id: {product['id']!r}"
            ) from exc

    admin_url = f"https://{store_domain}/admin/products/{product_id}"
    logger.info("Created Shopify draft product %s", admin_url)
    return product_id, admin_url


def post_to_slack(product_name: str, draft_url: str) -> None:
    """Post the draft Shopify URL to Slack via SLACK_WEBHOOK_URL."""
    webhook_url = _require_env("SLACK_WEBHOOK_URL")
    text = (
        f":sparkles: New Shopify draft product ready for review: *{product_name}*\n"
        f"{draft_url}"
    )
    payload = {"text": text}

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise SlackError(f"Network error talking to Slack: {exc}") from exc

    if response.status_code >= 400:
        raise SlackError(
            f"Slack returned HTTP {response.status_code}: {response.text}"
        )

    logger.info("Posted draft URL to Slack via incoming webhook")


def create_linear_issue(product_name: str, draft_url: str) -> str:
    """Create a Linear issue for the launch and return its ID."""
    api_key = _require_env("LINEAR_API_KEY")
    team_id = _require_env("LINEAR_TEAM_ID")

    title = f"Launch: {product_name}"
    description = (
        f"Shopify draft product ready for review.\n\n"
        f"**Draft URL:** {draft_url}\n\n"
        f"Approve and publish from the Shopify admin once verified."
    )

    query = """
    mutation CreateIssue($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        success
        issue { id identifier url }
      }
    }
    """
    variables = {
        "input": {"teamId": team_id, "title": title, "description": description}
    }
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            "https://api.linear.app/graphql",
            headers=headers,
            json={"query": query, "variables": variables},
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise LinearError(f"Network error talking to Linear: {exc}") from exc

    if response.status_code >= 400:
        raise LinearError(
            f"Linear returned HTTP {response.status_code}: {response.text}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise LinearError(
            f"Linear response was not valid JSON: {response.text}"
        ) from exc

    if body.get("errors"):
        raise LinearError(f"Linear GraphQL errors: {body['errors']!r}")

    data = (body.get("data") or {}).get("issueCreate") or {}
    if not data.get("success") or not data.get("issue"):
        raise LinearError(f"Linear failed to create issue: {body!r}")

    issue = data["issue"]
    issue_id = issue.get("identifier") or issue.get("id")
    if not isinstance(issue_id, str) or not issue_id:
        raise LinearError(f"Linear returned an unexpected issue payload: {issue!r}")

    logger.info("Created Linear issue %s (%s)", issue_id, issue.get("url"))
    return issue_id


def save_product_record(
    product_name: str,
    product_id: int,
    draft_url: str,
    linear_issue_id: str | None,
) -> None:
    """Append the product record to data/products.json (a JSON object keyed by name)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    records: dict[str, Any] = {}
    if PRODUCTS_FILE.is_file():
        try:
            with PRODUCTS_FILE.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                records = loaded
            else:
                logger.warning(
                    "Existing %s is not a JSON object; replacing.", PRODUCTS_FILE
                )
        except json.JSONDecodeError:
            logger.warning(
                "Existing %s is not valid JSON; replacing.", PRODUCTS_FILE
            )

    records[product_name] = {
        "shopify_product_id": product_id,
        "shopify_draft_url": draft_url,
        "linear_issue_id": linear_issue_id,
        "status": "draft",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    tmp_path = PRODUCTS_FILE.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, sort_keys=True)
        fh.write("\n")
    tmp_path.replace(PRODUCTS_FILE)
    logger.info("Saved product record to %s", PRODUCTS_FILE)


def run(product_name: str) -> dict[str, Any]:
    """Run the full agent pipeline for a single product."""
    if not isinstance(product_name, str) or not product_name.strip():
        raise ValueError("product_name must be a non-empty string")
    product_name = product_name.strip()

    content = load_content(product_name)
    product_id, draft_url = create_shopify_draft(product_name, content)

    slack_error: Exception | None = None
    linear_error: Exception | None = None
    linear_issue_id: str | None = None

    try:
        post_to_slack(product_name, draft_url)
    except SlackError as exc:
        slack_error = exc
        logger.error("Slack notification failed: %s", exc)

    try:
        linear_issue_id = create_linear_issue(product_name, draft_url)
    except LinearError as exc:
        linear_error = exc
        logger.error("Linear issue creation failed: %s", exc)

    save_product_record(product_name, product_id, draft_url, linear_issue_id)

    if slack_error or linear_error:
        details = []
        if slack_error:
            details.append(f"Slack: {slack_error}")
        if linear_error:
            details.append(f"Linear: {linear_error}")
        raise StoreOpsError(
            "Shopify draft created but downstream steps failed: " + "; ".join(details)
        )

    return {
        "product_name": product_name,
        "shopify_product_id": product_id,
        "shopify_draft_url": draft_url,
        "linear_issue_id": linear_issue_id,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a Shopify draft product from content studio output, "
            "post it to Slack, and open a Linear launch issue."
        )
    )
    parser.add_argument(
        "product_name",
        help='The product name, e.g. "Foldable Cat Tunnel".',
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    startup_check()
    try:
        result = run(args.product_name)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 2
    except ContentNotFoundError as exc:
        logger.error("Content error: %s", exc)
        return 3
    except ShopifyError as exc:
        logger.error("Shopify error: %s", exc)
        return 4
    except StoreOpsError as exc:
        logger.error("%s", exc)
        return 5
    except (ValueError, TypeError) as exc:
        logger.error("Invalid input: %s", exc)
        return 2
    except Exception:
        logger.exception("Unexpected error in store_ops agent")
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
