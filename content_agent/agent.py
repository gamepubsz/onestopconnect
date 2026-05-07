"""ContentAgent orchestrator."""

from __future__ import annotations

from typing import Callable

from .generators import blog, newsletter, social, video
from .scheduler import Scheduler
from .types import Brief, ContentDraft, ContentType

Generator = Callable[[Brief], ContentDraft]

_GENERATORS: dict[ContentType, Generator] = {
    "video": video.generate,
    "blog": blog.generate,
    "social": social.generate,
    "newsletter": newsletter.generate,
}


class ContentAgent:
    """Top-level agent that plans, generates, formats, and schedules content."""

    def __init__(self, scheduler: Scheduler | None = None) -> None:
        self.scheduler = scheduler or Scheduler()

    def run(self, brief: Brief) -> ContentDraft:
        if brief.type not in _GENERATORS:
            raise ValueError(
                f"Unsupported content type: {brief.type!r}. "
                f"Expected one of {sorted(_GENERATORS)}."
            )
        if not brief.topic or not brief.topic.strip():
            raise ValueError("brief.topic must be a non-empty string.")

        draft = _GENERATORS[brief.type](brief)

        if brief.schedule_at is not None:
            draft = self.scheduler.schedule(draft, brief.schedule_at)

        return draft
