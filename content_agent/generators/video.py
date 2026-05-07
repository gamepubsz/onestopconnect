"""Short-form video script generator (15–60s)."""

from __future__ import annotations

import uuid

from ..types import Brief, ContentDraft

LENGTH_TO_SECONDS = {"short": 15, "medium": 30, "long": 60}


def generate(brief: Brief) -> ContentDraft:
    seconds = LENGTH_TO_SECONDS.get(brief.length, 30)
    tone = brief.tone or "energetic"
    audience = brief.audience or "a curious general audience"

    hook = f"What if you could understand {brief.topic} in {seconds} seconds?"
    beats = [
        f"Beat 1 — Set the scene for {audience}.",
        f"Beat 2 — One surprising insight about {brief.topic}.",
        "Beat 3 — Show the payoff or transformation.",
    ]
    cta = "Follow for more — link in bio."

    body = "\n".join([f"HOOK: {hook}", *beats, f"CTA: {cta}"])

    return ContentDraft(
        id=str(uuid.uuid4()),
        type="video",
        title=hook,
        body=body,
        metadata={
            "platform": brief.platform or "tiktok",
            "duration_seconds": seconds,
            "tone": tone,
            "format": "hook-beats-cta",
        },
    )
