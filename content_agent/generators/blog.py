"""Blog / SEO article generator."""

from __future__ import annotations

import re
import uuid

from ..types import Brief, ContentDraft

LENGTH_TO_WORDS = {"short": 400, "medium": 900, "long": 1600}


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "post"


def generate(brief: Brief) -> ContentDraft:
    target_words = LENGTH_TO_WORDS.get(brief.length, 900)
    audience = brief.audience or "readers new to the topic"
    title = f"{brief.topic.strip().rstrip('.')}: A Practical Guide"
    meta_description = (
        f"A practical guide to {brief.topic} for {audience}. "
        "Concrete steps, examples, and common pitfalls."
    )
    outline = [
        "Introduction — why this matters",
        "Background and definitions",
        "Step-by-step walkthrough",
        "Common pitfalls",
        "Worked example",
        "Conclusion and next steps",
    ]
    body = "\n\n".join(
        [
            f"# {title}",
            f"_Target length: ~{target_words} words._",
            "## Outline",
            "\n".join(f"- {section}" for section in outline),
            "## Draft",
            f"<!-- TODO: expand each outline section into prose targeting ~{target_words} words. -->",
        ]
    )

    return ContentDraft(
        id=str(uuid.uuid4()),
        type="blog",
        title=title,
        body=body,
        metadata={
            "slug": _slugify(brief.topic),
            "meta_description": meta_description,
            "target_keywords": [brief.topic.lower()],
            "target_word_count": target_words,
            "outline": outline,
        },
    )
