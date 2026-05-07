"""Platform-specific formatting helpers."""

from __future__ import annotations

PLATFORM_LIMITS: dict[str, int] = {
    "x": 280,
    "twitter": 280,
    "linkedin": 3000,
    "instagram": 2200,
    "threads": 500,
}


def truncate_for_platform(text: str, platform: str | None) -> str:
    """Truncate to a platform-aware character limit. Adds an ellipsis when cut."""
    if not platform:
        return text
    limit = PLATFORM_LIMITS.get(platform.lower())
    if limit is None or len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def hashtags(topic: str, count: int = 3) -> list[str]:
    """Build naive hashtags from a topic string."""
    words = [w for w in topic.replace("/", " ").split() if w.isalnum()]
    return [f"#{w.lower()}" for w in words[:count]]
