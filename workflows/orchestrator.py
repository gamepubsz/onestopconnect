#!/usr/bin/env python3
"""Pipeline orchestrator.

Runs ``content_studio.py`` and then ``store_ops.py`` for a given product, posts
a summary to Slack ``#general`` on success, posts the full traceback to Slack
``#errors`` on failure, and appends a structured record of every run to
``data/pipeline_log.json``.

This is the script that n8n triggers via webhook when a product is approved.

Usage:
    python -m workflows.orchestrator --product "Wireless Earbuds" \
        --url "https://www.aliexpress.com/item/1005001234567890.html"

    # Dry-run: prints every step (including Slack messages) without
    # invoking the step scripts or hitting any external APIs.
    python -m workflows.orchestrator --product "Foo" --url "https://..." --dry-run

Slack credentials (used only when ``--dry-run`` is not set):
    SLACK_BOT_TOKEN          Bot token with ``chat:write``. Preferred.
                             Posts go to channel names ``#general`` / ``#errors``.
    SLACK_WEBHOOK_GENERAL    Incoming webhook for ``#general`` (fallback).
    SLACK_WEBHOOK_ERRORS     Incoming webhook for ``#errors``  (fallback).

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

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / "workflows"
DATA_DIR = ROOT / "data"
LOG_FILE = DATA_DIR / "pipeline_log.json"

CONTENT_STUDIO = WORKFLOWS_DIR / "content_studio.py"
STORE_OPS = WORKFLOWS_DIR / "store_ops.py"

GENERAL_CHANNEL = "#general"
ERRORS_CHANNEL = "#errors"

SLACK_TIMEOUT_S = 15
SLACK_API_URL = "https://slack.com/api/chat.postMessage"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def post_to_slack(channel: str, text: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Post ``text`` to a Slack ``channel``.

    Returns a small dict describing the delivery attempt; never raises.
    """
    if dry_run:
        print(f"[DRY-RUN] Slack -> {channel}:\n{text}")
        return {"channel": channel, "delivered": False, "reason": "dry-run"}

    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    if bot_token:
        payload = json.dumps({"channel": channel, "text": text}).encode("utf-8")
        req = urllib.request.Request(
            SLACK_API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {bot_token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=SLACK_TIMEOUT_S) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
            if body.get("ok"):
                return {"channel": channel, "delivered": True, "via": "bot_token"}
            return {
                "channel": channel,
                "delivered": False,
                "via": "bot_token",
                "error": body.get("error", "unknown"),
            }
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            return {"channel": channel, "delivered": False, "via": "bot_token", "error": str(e)}

    webhook_env = {
        GENERAL_CHANNEL: "SLACK_WEBHOOK_GENERAL",
        ERRORS_CHANNEL: "SLACK_WEBHOOK_ERRORS",
    }.get(channel)
    webhook_url = os.environ.get(webhook_env) if webhook_env else None
    if not webhook_url:
        msg = (
            "No Slack credentials configured "
            f"(set SLACK_BOT_TOKEN or {webhook_env}). Skipping post to {channel}."
        )
        print(msg, file=sys.stderr)
        return {"channel": channel, "delivered": False, "reason": "no_credentials"}

    req = urllib.request.Request(
        webhook_url,
        data=json.dumps({"text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=SLACK_TIMEOUT_S) as resp:
            resp.read()
        return {"channel": channel, "delivered": True, "via": "webhook"}
    except (urllib.error.URLError, TimeoutError) as e:
        return {"channel": channel, "delivered": False, "via": "webhook", "error": str(e)}


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
        help="AliExpress product URL",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print steps without calling APIs or running step scripts.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    started = _utcnow()
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    step_args = ["--product", args.product, "--url", args.url]
    if args.dry_run:
        step_args.append("--dry-run")

    entry: dict[str, Any] = {
        "run_id": run_id,
        "product": args.product,
        "url": args.url,
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
