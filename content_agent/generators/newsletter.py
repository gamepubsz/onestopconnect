"""Newsletter draft generator."""

from __future__ import annotations

import uuid

from ..types import Brief, ContentDraft


def generate(brief: Brief) -> ContentDraft:
    audience = brief.audience or "subscribers"
    subject = f"{brief.topic.strip().rstrip('.')} — what we learned this week"
    preheader = f"A short note for {audience}: practical takeaways on {brief.topic}."

    sections = [
        ("Intro", f"Hey — quick update for {audience}."),
        ("Main story", f"This week we dug into {brief.topic}. Here's what stood out."),
        ("Three takeaways", "1. \n2. \n3. "),
        ("Worth a click", "- \n- \n- "),
        ("Sign-off", "Talk soon,\nThe team"),
    ]

    body = "\n\n".join(f"## {heading}\n{content}" for heading, content in sections)

    return ContentDraft(
        id=str(uuid.uuid4()),
        type="newsletter",
        title=subject,
        body=body,
        metadata={
            "subject": subject,
            "preheader": preheader,
            "sections": [heading for heading, _ in sections],
            "format": "markdown",
        },
    )
