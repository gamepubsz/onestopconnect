"""Customer service agent.

Classifies an inbound customer email and produces (or escalates) a reply.

Usage:
    python -m agents.customer_service \
        --subject "Where is my order?" \
        --body "Hi, I haven't received tracking yet..." \
        --from-email customer@example.com

Environment variables (all optional - the agent degrades gracefully):
    GROQ_API_KEY          - Used to call Groq for product questions / complaints.
    GROQ_MODEL            - Override the Groq model (default: llama-3.3-70b-versatile).
    SHOPIFY_STORE         - Shopify store domain, e.g. my-shop.myshopify.com.
    SHOPIFY_ACCESS_TOKEN  - Shopify Admin API access token.
    SHOPIFY_API_VERSION   - Shopify Admin API version (default: 2024-07).
    SLACK_WEBHOOK_URL     - Slack incoming webhook used to post escalations.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None

try:
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:
    pass


LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "cs_log.json"


def startup_check() -> None:
    """Print which env variables are loaded vs missing for this agent.

    Reads via ``os.environ.get`` so values set by GitHub Actions secrets are
    visible even when no ``.env`` file exists.
    """
    required: list[str] = []
    optional = [
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "SHOPIFY_STORE",
        "SHOPIFY_ACCESS_TOKEN",
        "SHOPIFY_API_VERSION",
        "SLACK_WEBHOOK_URL",
    ]
    loaded = [name for name in required + optional if os.environ.get(name)]
    missing_required = [name for name in required if not os.environ.get(name)]
    missing_optional = [name for name in optional if not os.environ.get(name)]

    print("=" * 60, file=sys.stderr)
    print("[customer_service] startup env check", file=sys.stderr)
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

CATEGORIES = (
    "shipping_query",
    "return_request",
    "product_question",
    "complaint",
    "other",
)

RETURN_POLICY_REPLY = (
    "Hi there,\n\n"
    "Thanks for reaching out about a return. Our return policy is as follows:\n\n"
    "- You have 15 days from the delivery date to request a return.\n"
    "- The item must be unused and in its original packaging.\n"
    "- Return shipping costs are the responsibility of the buyer.\n\n"
    "If you'd like to proceed, please reply to this email with your order number "
    "and we'll send you instructions.\n\n"
    "Best regards,\n"
    "Customer Service"
)


@dataclass
class AgentResult:
    timestamp: str
    from_email: str | None
    subject: str
    body: str
    category: str
    reply: str | None
    auto_reply: bool
    escalated: bool
    metadata: dict[str, Any] = field(default_factory=dict)


def classify_email(subject: str, body: str) -> str:
    """Heuristic classifier covering the five required buckets.

    The classifier is deterministic and keyword based so it is testable and free
    to run. If keyword scoring is inconclusive the email is routed to "other"
    so a human can review it.
    """

    text = f"{subject}\n{body}".lower()

    keywords: dict[str, tuple[str, ...]] = {
        "shipping_query": (
            "tracking", "track my order", "where is my order", "where's my order",
            "shipping", "shipped", "delivery", "delivered", "arrive", "arrival",
            "package", "parcel", "courier", "shipment", "in transit", "wismo",
        ),
        "return_request": (
            "return", "refund", "exchange", "send it back", "rma", "money back",
            "return policy", "return label",
        ),
        "product_question": (
            "how do i use", "how does it work", "specs", "specification",
            "compatible", "compatibility", "does it work", "feature", "size",
            "dimensions", "material", "ingredients", "instructions", "manual",
            "question about",
        ),
        "complaint": (
            "broken", "damaged", "defective", "doesn't work", "not working",
            "disappointed", "terrible", "awful", "worst", "scam", "angry",
            "frustrated", "complaint", "unhappy", "missing parts", "wrong item",
            "poor quality",
        ),
    }

    scores = {category: 0 for category in keywords}
    for category, terms in keywords.items():
        for term in terms:
            if term in text:
                scores[category] += 1

    best_category = max(scores, key=scores.get)
    if scores[best_category] == 0:
        return "other"
    return best_category


def _extract_order_number(subject: str, body: str) -> str | None:
    """Pull a likely order number out of the email text."""

    pattern = re.compile(r"#?\b([0-9]{4,})\b")
    for source in (subject, body):
        match = pattern.search(source or "")
        if match:
            return match.group(1)
    return None


def lookup_shopify_order(
    email: str | None,
    order_number: str | None = None,
) -> dict[str, Any] | None:
    """Look up the most recent matching order in Shopify.

    Returns a dict with order_name, fulfillment_status, tracking_numbers and
    tracking_urls. Returns None if Shopify is not configured, the network call
    fails, or no matching order is found.
    """

    store = os.environ.get("SHOPIFY_STORE")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    version = os.environ.get("SHOPIFY_API_VERSION", "2024-07")

    if not store or not token or requests is None:
        return None

    base_url = f"https://{store}/admin/api/{version}/orders.json"
    params: dict[str, str] = {"status": "any", "limit": "5"}
    if email:
        params["email"] = email
    if order_number:
        params["name"] = order_number

    try:
        response = requests.get(
            base_url,
            params=params,
            headers={"X-Shopify-Access-Token": token},
            timeout=15,
        )
        response.raise_for_status()
        orders = response.json().get("orders", [])
    except Exception as exc:
        print(f"[shopify] lookup failed: {exc}", file=sys.stderr)
        return None

    if not orders:
        return None

    order = orders[0]
    tracking_numbers: list[str] = []
    tracking_urls: list[str] = []
    for fulfillment in order.get("fulfillments", []) or []:
        tracking_numbers.extend(fulfillment.get("tracking_numbers", []) or [])
        tracking_urls.extend(fulfillment.get("tracking_urls", []) or [])

    return {
        "order_name": order.get("name"),
        "fulfillment_status": order.get("fulfillment_status"),
        "tracking_numbers": tracking_numbers,
        "tracking_urls": tracking_urls,
    }


def build_shipping_reply(order: dict[str, Any] | None) -> str:
    """Compose a customer-facing reply for shipping queries."""

    if not order:
        return (
            "Hi there,\n\n"
            "Thanks for reaching out about your shipment. We couldn't locate an "
            "order using the email address on file. Could you reply with the "
            "order number from your confirmation email so we can pull up the "
            "tracking details?\n\n"
            "Best regards,\n"
            "Customer Service"
        )

    name = order.get("order_name") or "your order"
    status = order.get("fulfillment_status") or "unfulfilled"
    numbers = order.get("tracking_numbers") or []
    urls = order.get("tracking_urls") or []

    lines = [
        "Hi there,",
        "",
        f"Thanks for checking in on {name}. Here is the latest status:",
        f"- Fulfillment status: {status}",
    ]
    if numbers:
        lines.append("- Tracking number(s): " + ", ".join(numbers))
    if urls:
        lines.append("- Tracking link(s): " + ", ".join(urls))
    if not numbers and not urls:
        lines.append(
            "- Tracking has not been generated yet. We'll email you the tracking "
            "link as soon as the carrier scans the package."
        )
    lines.extend([
        "",
        "Let us know if there's anything else we can help with.",
        "",
        "Best regards,",
        "Customer Service",
    ])
    return "\n".join(lines)


def call_groq(category: str, subject: str, body: str) -> str:
    """Generate a reply with the Groq API for product questions / complaints."""

    api_key = os.environ.get("GROQ_API_KEY")
    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    try:
        import groq  # type: ignore
    except ImportError:
        groq = None  # type: ignore

    if not api_key or groq is None:
        return (
            "Hi there,\n\n"
            "Thanks for getting in touch. A member of our team will follow up "
            "shortly with a personalized response to your message.\n\n"
            "Best regards,\n"
            "Customer Service"
        )

    tone = (
        "The customer is reporting a problem or complaint. Open with empathy, "
        "acknowledge the issue, take ownership, and offer concrete next steps."
        if category == "complaint"
        else
        "The customer is asking a product question. Answer clearly and "
        "factually. If you don't have enough information, ask a focused "
        "follow-up question."
    )

    system_prompt = (
        "You are a customer service agent for an e-commerce store. "
        "Write a concise, friendly email reply (under 180 words). "
        f"{tone} Sign off as 'Customer Service'."
    )

    user_prompt = (
        f"Subject: {subject}\n\n"
        f"Body:\n{body}\n\n"
        "Write the reply email body only, no subject line."
    )

    try:
        client = groq.Groq(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        response = client.chat.completions.create(
            model=model,
            max_tokens=600,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        reply = (response.choices[0].message.content or "").strip()
        if reply:
            return reply
    except Exception as exc:
        print(f"[groq] generation failed: {exc}", file=sys.stderr)

    return (
        "Hi there,\n\n"
        "Thanks for getting in touch. A member of our team will follow up "
        "shortly with a personalized response to your message.\n\n"
        "Best regards,\n"
        "Customer Service"
    )


def post_to_slack(subject: str, body: str, from_email: str | None) -> bool:
    """Post the full inbound email to the Slack escalation webhook.

    Uses ``SLACK_WEBHOOK_URL`` (incoming webhook) so the same secret works
    in local dev and GitHub Actions. Returns True if Slack accepted the
    message, False otherwise.
    """

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")

    if not webhook_url or requests is None:
        print(
            "[slack] would escalate (SLACK_WEBHOOK_URL / requests unavailable)",
            file=sys.stderr,
        )
        return False

    text = (
        f":rotating_light: *New CS escalation*\n"
        f"*From:* {from_email or 'unknown'}\n"
        f"*Subject:* {subject}\n\n"
        f"```\n{body}\n```"
    )

    try:
        response = requests.post(
            webhook_url,
            json={"text": text},
            timeout=15,
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        print(f"[slack] post failed: {exc}", file=sys.stderr)
        return False


def log_result(result: AgentResult, log_path: Path = LOG_PATH) -> None:
    """Append the result to data/cs_log.json (a JSON array on disk)."""

    log_path.parent.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    if log_path.exists():
        try:
            with log_path.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
                if isinstance(loaded, list):
                    entries = loaded
        except (json.JSONDecodeError, OSError):
            entries = []

    entries.append(asdict(result))

    with log_path.open("w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, ensure_ascii=False)


def handle_email(
    subject: str,
    body: str,
    from_email: str | None = None,
    log_path: Path = LOG_PATH,
) -> AgentResult:
    """Run the full customer service workflow on a single email."""

    if not isinstance(subject, str) or not isinstance(body, str):
        raise TypeError("subject and body must be strings")

    category = classify_email(subject, body)
    metadata: dict[str, Any] = {}
    reply: str | None = None
    auto_reply = False
    escalated = False

    if category == "shipping_query":
        order_number = _extract_order_number(subject, body)
        order = lookup_shopify_order(from_email, order_number)
        metadata["shopify_order"] = order
        reply = build_shipping_reply(order)
        auto_reply = True
    elif category == "return_request":
        reply = RETURN_POLICY_REPLY
        auto_reply = True
    elif category in {"product_question", "complaint"}:
        reply = call_groq(category, subject, body)
        auto_reply = True
    else:
        escalated = post_to_slack(subject, body, from_email)
        metadata["slack_posted"] = escalated
        auto_reply = False

    result = AgentResult(
        timestamp=datetime.now(timezone.utc).isoformat(),
        from_email=from_email,
        subject=subject,
        body=body,
        category=category,
        reply=reply,
        auto_reply=auto_reply,
        escalated=escalated,
        metadata=metadata,
    )

    log_result(result, log_path=log_path)

    print("=" * 72)
    print(f"Category : {result.category}")
    print(f"From     : {result.from_email or '(unknown)'}")
    print(f"Subject  : {result.subject}")
    print("-" * 72)
    if reply is not None:
        print("Proposed reply (review before sending):\n")
        print(reply)
    else:
        print("No auto-reply generated. Email escalated for human review.")
        if escalated:
            print("Posted to Slack escalation channel.")
        else:
            print("Slack escalation FAILED - please follow up manually.")
    print("=" * 72)

    return result


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify a customer email and draft a reply."
    )
    parser.add_argument("--subject", required=True, help="Email subject line.")
    parser.add_argument(
        "--body",
        help="Email body text. If omitted, reads from stdin.",
    )
    parser.add_argument(
        "--from-email",
        dest="from_email",
        default=None,
        help="Sender's email address (used for Shopify lookup).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    startup_check()

    body = args.body if args.body is not None else sys.stdin.read()
    handle_email(args.subject, body, args.from_email)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
