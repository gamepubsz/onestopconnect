"""CLI entry point for the Content Agent."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from typing import Sequence

from .agent import ContentAgent
from .types import Brief


def _parse_schedule(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="content-agent",
        description="Create, format, and schedule digital content.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Generate a content draft.")
    gen.add_argument(
        "--type",
        required=True,
        choices=["video", "blog", "social", "newsletter"],
    )
    gen.add_argument("--topic", required=True)
    gen.add_argument("--audience")
    gen.add_argument("--tone")
    gen.add_argument("--platform")
    gen.add_argument(
        "--length",
        choices=["short", "medium", "long"],
        default="medium",
    )
    gen.add_argument(
        "--schedule-at",
        help="ISO-8601 datetime (e.g. 2026-05-08T09:00:00).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "generate":
        brief = Brief(
            type=args.type,
            topic=args.topic,
            audience=args.audience,
            tone=args.tone,
            platform=args.platform,
            length=args.length,
            schedule_at=_parse_schedule(args.schedule_at),
        )
        draft = ContentAgent().run(brief)
        payload = asdict(draft)
        if payload.get("scheduled_for") is not None:
            payload["scheduled_for"] = draft.scheduled_for.isoformat()  # type: ignore[union-attr]
        print(json.dumps(payload, indent=2))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
