"""Social media copy generator."""

from __future__ import annotations

import uuid

from ..formatters import hashtags, truncate_for_platform
from ..types import Brief, ContentDraft


def generate(brief: Brief) -> ContentDraft:
    platform = (brief.platform or "x").lower()
    tone = brief.tone or "concise"
    tags = hashtags(brief.topic)

    hook = f"Quick take on {brief.topic}:"
    insight = (
        f"Most teams overcomplicate {brief.topic}. "
        "Here's the simplest version that still works."
    )
    raw = " ".join([hook, insight, *tags])
    body = truncate_for_platform(raw, platform)

    return ContentDraft(
        id=str(uuid.uuid4()),
        type="social",
        title=hook,
        body=body,
        metadata={
            "platform": platform,
            "tone": tone,
            "hashtags": tags,
            "char_count": len(body),
        },
    )
