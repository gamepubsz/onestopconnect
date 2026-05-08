#!/usr/bin/env python3
"""Pipeline orchestrator.

Runs ``agents/content_studio.py`` and then ``agents/store_ops.py`` for a given
product, posts a summary to Slack ``#general`` on success, posts the full
traceback to Slack ``#errors`` on failure, and appends a structured record of
every run to ``data/pipeline_log.json``.

This is the script that n8n triggers via webhook when a product is approved.

Usage:
    python -m workflows.orchestrator \
        --product "Wireless Earbuds" \
        --url "https://www.aliexpress.com/item/1005001234567890.html" \
        --image-url "https://cdn.example.com/wireless-earbuds.jpg"

    # Dry-run: prints every step (including Slack messages) without
    # invoking the step scripts or hitting any external APIs. ``--image-url``
    # is optional in this mode.
    python -m workflows.orchestrator --product "Foo" --url "https://..." --dry-run

Per-step CLI shapes (handled by ``build_step_args``):
    content_studio  --product P --url U --image-url I
    store_ops       <product_name>          # positional, no flags

Slack credentials (used only when ``--dry-run`` is not set):
    SLACK_WEBHOOK_URL        Slack incoming webhook for both success and
                             error notifications. Read via ``os.environ.get``
                             so GitHub Actions secrets are picked up.

If no credentials are configured the orchestrator logs a warning and continues;
Slack delivery failures never break the pipeline result.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / "workflows"
AGENTS_DIR = ROOT / "agents"
DATA_DIR = ROOT / "data"
LOG_FILE = DATA_DIR / "pipeline_log.json"

CONTENT_STUDIO = AGENTS_DIR / "content_studio.py"
STORE_OPS = AGENTS_DIR / "store_ops.py"

GENERAL_CHANNEL = "#general"
ERRORS_CHANNEL = "#errors"

SLACK_TIMEOUT_S = 15


def startup_check() -> None:
    """Print which env variables are loaded vs missing for the orchestrator."""
    required: list[str] = []
    optional = ["SLACK_WEBHOOK_URL", "GROQ_API_KEY", "APIFY_API_TOKEN"]

    loaded = [name for name in required + optional if os.environ.get(name)]
    missing_required = [name for name in required if not os.environ.get(name)]
    missing_optional = [name for name in optional if not os.environ.get(name)]

    print("=" * 60, file=sys.stderr)
    print("[orchestrator] startup env check", file=sys.stderr)
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def post_to_slack(channel: str, text: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Post ``text`` to Slack via the ``SLACK_WEBHOOK_URL`` incoming webhook.

    The ``channel`` argument is preserved in the returned metadata for
    bookkeeping (success vs error) but Slack incoming webhooks always
    deliver to the channel configured on the webhook itself. Returns a
    small dict describing the delivery attempt; never raises.
    """
    if dry_run:
        print(f"[DRY-RUN] Slack -> {channel}:\n{text}")
        return {"channel": channel, "delivered": False, "reason": "dry-run"}

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        msg = (
            "No Slack credentials configured "
            f"(set SLACK_WEBHOOK_URL). Skipping post for {channel}."
        )
        print(msg, file=sys.stderr)
        return {"channel": channel, "delivered": False, "reason": "no_credentials"}

    body_text = f"[{channel}] {text}"
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps({"text": body_text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=SLACK_TIMEOUT_S) as resp:
            resp.read()
        return {"channel": channel, "delivered": True, "via": "webhook"}
    except (urllib.error.URLError, TimeoutError) as e:
        return {
            "channel": channel,
            "delivered": False,
            "via": "webhook",
            "error": str(e),
        }


def run_step(
    name: str,
    script: Path,
    step_args: list[str],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Run a single pipeline step as a subprocess.

    Raises ``RuntimeError`` if the step exits non-zero, ``FileNotFoundError`` if
    the script does not exist (and we are not in dry-run mode).
    """
    cmd = [sys.executable, str(script), *step_args]
    started = _utcnow()

    if dry_run:
        print(f"[DRY-RUN] Would run: {' '.join(cmd)}")
        return {
            "name": name,
            "command": cmd,
            "started_at": _iso(started),
            "finished_at": _iso(started),
            "duration_s": 0.0,
            "returncode": None,
            "status": "dry-run",
            "stdout": "",
            "stderr": "",
        }

    if not script.exists():
        raise FileNotFoundError(f"Step script not found: {script}")

    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    duration = round(time.monotonic() - t0, 3)
    finished = _utcnow()

    result: dict[str, Any] = {
        "name": name,
        "command": cmd,
        "started_at": _iso(started),
        "finished_at": _iso(finished),
        "duration_s": duration,
        "returncode": proc.returncode,
        "status": "ok" if proc.returncode == 0 else "failed",
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"Step '{name}' failed (exit {proc.returncode}): {detail or '<no output>'}"
        )

    return result


def append_log(entry: dict[str, Any]) -> None:
    """Append ``entry`` to ``data/pipeline_log.json`` (atomic, JSON array)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, Any]] = []
    if LOG_FILE.exists():
        try:
            with LOG_FILE.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                existing = loaded
        except json.JSONDecodeError:
            existing = []

    existing.append(entry)
    tmp = LOG_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, default=str)
        f.write("\n")
    tmp.replace(LOG_FILE)


def format_summary(entry: dict[str, Any]) -> str:
    head = (
        ":white_check_mark: Pipeline "
        f"`{entry['run_id']}` completed for *{entry['product']}*"
    )
    if entry.get("dry_run"):
        head = (
            ":test_tube: Pipeline "
            f"`{entry['run_id']}` *dry-run* for *{entry['product']}*"
        )
    lines = [
        head,
        f"URL: {entry['url']}",
        f"Duration: {entry.get('duration_s', '?')}s",
        "Steps:",
    ]
    for step in entry.get("steps", []):
        lines.append(
            f"  • {step['name']}: {step['status']} "
            f"({step.get('duration_s', 0)}s)"
        )
    return "\n".join(lines)


def format_error(entry: dict[str, Any]) -> str:
    tb = entry.get("traceback", "")
    return (
        f":rotating_light: Pipeline `{entry['run_id']}` *FAILED* "
        f"for *{entry['product']}*\n"
        f"URL: {entry['url']}\n"
        f"Error: {entry.get('error', 'unknown')}\n"
        f"```\n{tb}```"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="workflows.orchestrator",
        description="Run the product pipeline: content_studio -> store_ops.",
    )
    parser.add_argument("--product", required=True, help="Product name")
    parser.add_argument(
        "--url",
        required=True,
        help="Product source URL (AliExpress product page, TikTok video, etc.)",
    )
    parser.add_argument(
        "--image-url",
        default=None,
        help=(
            "Public URL of the product image. Required by content_studio in "
            "real (non-dry-run) execution; optional with --dry-run."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print steps without calling APIs or running step scripts.",
    )
    args = parser.parse_args(argv)

    if not args.dry_run and not args.image_url:
        parser.error(
            "--image-url is required when --dry-run is not set "
            "(content_studio refuses to run without an image URL)."
        )

    return args


def build_step_args(step_name: str, args: argparse.Namespace) -> list[str]:
    """Build the CLI args for a single step from the orchestrator namespace.

    Each step script has its own argument shape; this function is the single
    place that translates the orchestrator's interface into per-step flags.
    """
    if step_name == "content_studio":
        cli = ["--product", args.product, "--url", args.url]
        image_url = args.image_url or "<dry-run-placeholder>"
        cli.extend(["--image-url", image_url])
        return cli
    if step_name == "store_ops":
        return [args.product]
    raise ValueError(f"Unknown pipeline step: {step_name!r}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    startup_check()

    started = _utcnow()
    run_id = started.strftime("%Y%m%dT%H%M%SZ")

    entry: dict[str, Any] = {
        "run_id": run_id,
        "product": args.product,
        "url": args.url,
        "image_url": args.image_url,
        "dry_run": args.dry_run,
        "started_at": _iso(started),
        "status": "running",
        "steps": [],
    }

    steps = [
        ("content_studio", CONTENT_STUDIO),
        ("store_ops", STORE_OPS),
    ]

    try:
        for step_name, script in steps:
            print(f"==> Running {step_name}...")
            step_args = build_step_args(step_name, args)
            result = run_step(step_name, script, step_args, dry_run=args.dry_run)
            entry["steps"].append(result)
            print(f"    {step_name}: {result['status']} ({result['duration_s']}s)")

        finished = _utcnow()
        entry["finished_at"] = _iso(finished)
        entry["duration_s"] = round((finished - started).total_seconds(), 3)
        entry["status"] = "dry-run" if args.dry_run else "success"

        summary = format_summary(entry)
        print(summary)
        delivery = post_to_slack(GENERAL_CHANNEL, summary, dry_run=args.dry_run)
        entry["slack"] = delivery
        append_log(entry)
        return 0

    except Exception as exc:
        tb = traceback.format_exc()
        finished = _utcnow()
        entry["finished_at"] = _iso(finished)
        entry["duration_s"] = round((finished - started).total_seconds(), 3)
        entry["status"] = "failed"
        entry["error"] = str(exc)
        entry["traceback"] = tb

        message = format_error(entry)
        print(message, file=sys.stderr)
        delivery = post_to_slack(ERRORS_CHANNEL, message, dry_run=args.dry_run)
        entry["slack"] = delivery
        append_log(entry)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
